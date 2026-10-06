"""专题学习：学生自学手册的「我学完了」进度记录（fce.db 同库）。

设计意图：
- 专题内容（手册 HTML/讲解）在前端静态打包，后端只管「谁在哪个专题学完了
  哪些主题」。一条 (user, topic_id) 一行，done_keys 存主题 key 的 JSON 数组，
  PUT 幂等覆盖；历史 key 不丢（completed_at 记首次时间，重置后可再学）。
- 专题对学生的可见性由表内 assignment 决定：assigned_user 为空＝全学生可见，
  指定用户＝仅该用户（本专题绑定 malin）。学生 GET 只能看到自己的专题；
  教师可看全部，用于跟进。
- 数据量小，不设删除。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_progress (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user           TEXT NOT NULL,
    topic_id       TEXT NOT NULL,
    assigned_user  TEXT NOT NULL DEFAULT '',
    done_keys      TEXT NOT NULL DEFAULT '[]',
    completed_at   TEXT DEFAULT '',
    updated_at     TEXT DEFAULT '',
    UNIQUE(user, topic_id)
);
CREATE TABLE IF NOT EXISTS topic_content (
    topic_id       TEXT PRIMARY KEY,        -- 稳定 id（slug）
    title          TEXT NOT NULL,
    subtitle       TEXT NOT NULL DEFAULT '',
    emoji          TEXT NOT NULL DEFAULT '📘',
    assigned_to    TEXT NOT NULL DEFAULT '', -- 空=全员；逗号分隔用户名=指定学生
    content        TEXT NOT NULL DEFAULT '{}', -- 结构化 JSON（nodes 树）
    published      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_topic_progress_user ON topic_progress(user, topic_id);
"""


class TopicStore:
    """topic_progress 读写 + topic_content（v2 结构化专题）读写。"""

    def __init__(self, db_path: Optional[str] = None,
                 grammar_db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self.grammar_db_path = grammar_db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "grammar.db")
        if Path(self.db_path).exists():
            # 打开一次触发建表（SCHEMA 在 _connect 内执行）
            self._connect().close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    # ---- 读取 ----
    def get(self, user: str, topic_id: str) -> dict:
        """单个专题进度（学生本人或教师；无记录返回空进度）。"""
        user = (user or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_progress WHERE user = ? AND topic_id = ?",
                (user, topic_id),
            ).fetchone()
        return _out(row) if row else {
            "user": user, "topic_id": topic_id, "done_keys": [],
            "completed_at": "", "updated_at": "",
        }

    def list_for(self, viewer: str, role: str) -> list[dict]:
        """专题进度列表：教师看全部，学生只看 assigned 给自己（或全员）的行。"""
        viewer = (viewer or "").strip()
        with self._connect() as conn:
            if role == "teacher":
                rows = conn.execute(
                    "SELECT * FROM topic_progress ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM topic_progress WHERE user = ?"
                    " AND (assigned_user = '' OR assigned_user = ?)"
                    " ORDER BY updated_at DESC",
                    (viewer, viewer),
                ).fetchall()
        return [_out(r) for r in rows]

    # ---- 写入 ----
    def put(
        self, user: str, topic_id: str, done_keys: list[str], *,
        assigned_user: str = "",
    ) -> dict:
        """幂等覆盖某用户某专题的已完成主题集合。

        done_keys 去重排序后存 JSON；全部主题完成时记 completed_at
        （已有值不覆盖，重置后再学完会更新为新一轮时间）。
        assigned_user 仅在首次落行时写入（内容归属由前端静态清单声明，
        后端不重复维护）。
        """
        user = (user or "").strip()[:60]
        topic_id = (topic_id or "").strip()[:64]
        if not user or not topic_id:
            raise ValueError("user 与 topic_id 不能为空")
        keys = sorted({str(k).strip()[:40] for k in (done_keys or []) if str(k).strip()})
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            old = conn.execute(
                "SELECT assigned_user, completed_at FROM topic_progress"
                " WHERE user = ? AND topic_id = ?",
                (user, topic_id),
            ).fetchone()
            assigned = (old["assigned_user"] if old else "") or (assigned_user or "").strip()[:60]
            completed_at = (old["completed_at"] if old else "") or ""
            if not completed_at and keys:
                completed_at = now  # 首次有完成项即记；全清空则保留历史
            conn.execute(
                "INSERT OR REPLACE INTO topic_progress"
                " (user, topic_id, assigned_user, done_keys, completed_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (user, topic_id, assigned,
                 json.dumps(keys, ensure_ascii=False), completed_at, now),
            )
            row = conn.execute(
                "SELECT * FROM topic_progress WHERE user = ? AND topic_id = ?",
                (user, topic_id),
            ).fetchone()
        return _out(row)

    # ================= v2 结构化专题内容 =================

    @staticmethod
    def validate_content(data: dict) -> list[str]:
        """结构校验：返回错误列表（空=通过）。

        结构：{nodes: [{key, title, explain, wrong_qs[], general_qs[],
        children[]...}]}；wrong_qs/general_qs 项为
        {ref: {lecture, qnum}} 或自含 {stem, answer, explain}。
        """
        errs = []
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return ["顶层须为 {nodes: [...]}"]
        keys = set()

        def walk(nodes: list, path: str) -> None:
            for i, n in enumerate(nodes):
                p = f"{path}.nodes[{i}]"
                if not isinstance(n, dict):
                    errs.append(f"{p} 须为对象")
                    continue
                k = str(n.get("key") or "").strip()
                if not k:
                    errs.append(f"{p}.key 不能为空")
                elif k in keys:
                    errs.append(f"{p}.key 重复：{k}")
                else:
                    keys.add(k)
                if not str(n.get("title") or "").strip():
                    errs.append(f"{p}.title 不能为空")
                for qf in ("wrong_qs", "general_qs"):
                    qs = n.get(qf) or []
                    if not isinstance(qs, list):
                        errs.append(f"{p}.{qf} 须为数组")
                        continue
                    for j, q in enumerate(qs):
                        qp = f"{p}.{qf}[{j}]"
                        if not isinstance(q, dict):
                            errs.append(f"{qp} 须为对象")
                            continue
                        ref = q.get("ref")
                        if ref is not None:
                            if not (isinstance(ref, dict) and ref.get("lecture")
                                    and ref.get("qnum")):
                                errs.append(f"{qp}.ref 须含 lecture 与 qnum")
                        elif not str(q.get("stem") or "").strip():
                            errs.append(f"{qp} 自含题缺 stem")
                        else:
                            continue
                children = n.get("children")
                if children is not None:
                    if not isinstance(children, list):
                        errs.append(f"{p}.children 须为数组")
                    else:
                        walk(children, p)

        walk(data["nodes"], "")
        return errs

    def upsert_topic(self, meta: dict, content: dict) -> dict:
        """导入/更新专题（同 topic_id 覆盖内容，进度行不动）。"""
        errs = self.validate_content(content)
        if errs:
            raise ValueError("内容校验失败：" + "；".join(errs[:8]))
        tid = str(meta.get("topic_id") or "").strip()[:64]
        if not tid:
            raise ValueError("topic_id 不能为空")
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO topic_content"
                " (topic_id, title, subtitle, emoji, assigned_to, content, published, updated_at)"
                " VALUES (?,?,?,?,?,?,1,?)"
                " ON CONFLICT(topic_id) DO UPDATE SET"
                " title=excluded.title, subtitle=excluded.subtitle,"
                " emoji=excluded.emoji, assigned_to=excluded.assigned_to,"
                " content=excluded.content, updated_at=excluded.updated_at",
                (tid, str(meta.get("title") or "")[:120],
                 str(meta.get("subtitle") or "")[:200],
                 str(meta.get("emoji") or "📘")[:8],
                 ",".join(x.strip() for x in str(meta.get("assigned_to") or "").split(",") if x.strip()),
                 json.dumps(content, ensure_ascii=False), now),
            )
        return self.get_topic(tid)

    def _expand_qs(self, qs: list) -> list[dict]:
        """题目项 → 展开：ref 引用 grammar.db 题库（带出题干/AI 答案/解析），
        自含项原样透传（缺字段补空）。"""
        out = []
        refs = [q.get("ref") for q in qs
                if isinstance(q, dict) and isinstance(q.get("ref"), dict)]
        bank: dict = {}
        if refs and Path(self.grammar_db_path).exists():
            try:
                # 注意：此连接不设 row_factory，row 是 tuple（按位取值）
                with sqlite3.connect(
                        f"file:{self.grammar_db_path}?mode=ro", uri=True) as conn:
                    for r in refs:
                        row = conn.execute(
                            "SELECT stem, options_json, answer FROM homework_question"
                            " WHERE lecture_number = ? AND qnum = ?",
                            (int(r["lecture"]), int(r["qnum"]))).fetchone()
                        if row:
                            bank[(int(r["lecture"]), int(r["qnum"]))] = {
                                "stem": row[0] or "",
                                "options": json.loads(row[1] or "[]"),
                                "answer": row[2] or "",
                            }
            except (sqlite3.Error, ValueError, TypeError):
                pass
        for q in qs or []:
            if not isinstance(q, dict):
                continue
            ref = q.get("ref")
            if isinstance(ref, dict):
                b = bank.get((int(ref.get("lecture", 0)), int(ref.get("qnum", 0))))
                out.append({
                    "kind": "ref",
                    "lecture": ref.get("lecture"), "qnum": ref.get("qnum"),
                    "stem": (b or {}).get("stem", ""),
                    "options": (b or {}).get("options", []),
                    "answer": (b or {}).get("answer", ""),
                    "explain": str(q.get("explain") or ""),
                    "note": str(q.get("note") or ""),  # 教师批注（如「当时做错」）
                })
            else:
                out.append({
                    "kind": "self",
                    "stem": str(q.get("stem") or ""),
                    "options": q.get("options") or [],
                    "answer": str(q.get("answer") or ""),
                    "explain": str(q.get("explain") or ""),
                    "note": str(q.get("note") or ""),
                })
        return out

    def _expand_nodes(self, nodes: list) -> list[dict]:
        out = []
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            out.append({
                "key": str(n.get("key") or ""),
                "title": str(n.get("title") or ""),
                "explain": str(n.get("explain") or ""),
                "qs_label": str(n.get("qs_label") or ""),
                "wrong_qs": self._expand_qs(n.get("wrong_qs") or []),
                "general_qs": self._expand_qs(n.get("general_qs") or []),
                "children": self._expand_nodes(n.get("children") or []),
            })
        return out

    def get_topic(self, topic_id: str, expand: bool = True) -> Optional[dict]:
        """单个专题内容（expand=True 展开题库引用为完整题目）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_content WHERE topic_id = ?",
                ((topic_id or "").strip(),)).fetchone()
        if row is None:
            return None
        try:
            content = json.loads(row["content"] or "{}")
        except (ValueError, TypeError):
            content = {"nodes": []}
        return {
            "topic_id": row["topic_id"],
            "title": row["title"],
            "subtitle": row["subtitle"],
            "emoji": row["emoji"],
            "assigned_to": row["assigned_to"],
            "published": bool(row["published"]),
            "updated_at": row["updated_at"],
            "nodes": self._expand_nodes(content.get("nodes") or []) if expand
                     else content.get("nodes") or [],
        }

    def list_topics(self, viewer: str = "", role: str = "teacher") -> list[dict]:
        """专题内容列表：教师全量；学生只见 published 且 assigned 含自己。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topic_content ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            assigned = [x for x in (r["assigned_to"] or "").split(",") if x]
            if role != "teacher":
                if not r["published"] or (assigned and viewer not in assigned):
                    continue
            out.append({
                "topic_id": r["topic_id"], "title": r["title"],
                "subtitle": r["subtitle"], "emoji": r["emoji"],
                "assigned_to": r["assigned_to"],
                "published": bool(r["published"]),
                "updated_at": r["updated_at"],
                "node_count": self._count_nodes(r["content"]),
            })
        return out

    @staticmethod
    def _count_nodes(content_json: str) -> int:
        try:
            nodes = json.loads(content_json or "{}").get("nodes") or []
        except (ValueError, TypeError):
            return 0

        def cnt(ns):
            return sum(1 + cnt(n.get("children") or []) for n in ns
                       if isinstance(n, dict))
        return cnt(nodes)

    def set_published(self, topic_id: str, published: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE topic_content SET published = ?,"
                " updated_at = datetime('now') WHERE topic_id = ?",
                (1 if published else 0, (topic_id or "").strip()))

    def set_assigned(self, topic_id: str, assigned_to: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE topic_content SET assigned_to = ?,"
                " updated_at = datetime('now') WHERE topic_id = ?",
                (",".join(x.strip() for x in (assigned_to or "").split(",")
                          if x.strip()), (topic_id or "").strip()))

    def delete_topic(self, topic_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM topic_content WHERE topic_id = ?",
                         ((topic_id or "").strip(),))


def _out(row: sqlite3.Row) -> dict:
    try:
        keys = json.loads(row["done_keys"] or "[]")
    except (ValueError, TypeError):
        keys = []
    return {
        "user": row["user"],
        "topic_id": row["topic_id"],
        "assigned_user": row["assigned_user"] or "",
        "done_keys": keys if isinstance(keys, list) else [],
        "completed_at": row["completed_at"] or "",
        "updated_at": row["updated_at"] or "",
    }

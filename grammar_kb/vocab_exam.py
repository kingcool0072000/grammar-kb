"""词汇级别考试：成绩登记 + 按题错词明细 + 解锁级别（fce.db 同库）。

单词级别（学生能背到哪一级）以教师设定为准：
- 在备课中心「背单词 · 级别」里为该生点过级别后，这个值直接生效，
  不要求词汇考试达到及格分（PASS_SCORE），也不会被之后的考试分数改写
  （可设得比考试推算更高或更低）。vocab_unlock.updated_at 记下最近一次设定。
- 从未单独设定过的学生仍走旧规则：L0 默认开放；L{n} 考试 ≥PASS_SCORE
  解锁 L{n+1}（上限 MAX_UNLOCK_LEVEL）。

考卷的生成/存档/渲染在 :mod:`grammar_kb.vocab_paper`（考试资产，
与学生无关）；本模块只管成绩与错词明细。批改登记成绩时勾选的错词
落 vocab_exam_answers（只存错行，对的题=卷面词减错词，不落行），
路由层再把错词回流进背词艾宾浩斯复习队列（ReciteStore.enter_review）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .atlas_vocab import exam_allowed
from . import dbreg
from .store_base import connect, ensure_schema, loads_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS vocab_unlock (
  user       TEXT PRIMARY KEY,
  level      INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS vocab_exams (
    id         INTEGER PRIMARY KEY,
    user       TEXT NOT NULL,
    level      INTEGER NOT NULL,
    score      INTEGER NOT NULL,
    exam_date  TEXT NOT NULL,
    note       TEXT DEFAULT '',
    created_at TEXT,
    paper_id   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_vex_user ON vocab_exams(user, level, score);
CREATE INDEX IF NOT EXISTS idx_vex_paper ON vocab_exams(paper_id);
CREATE TABLE IF NOT EXISTS vocab_exam_answers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id    INTEGER NOT NULL,
    user       TEXT NOT NULL,
    word       TEXT NOT NULL,
    qtype      TEXT DEFAULT '',
    correct    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_vea_exam ON vocab_exam_answers(exam_id);
"""

# 及格线（100 分卷）：解锁下一级、目标「词汇级别」完成判定共用此口径
PASS_SCORE = 80
# 单词级别上限（L0..L7）：解锁推算、教师手动设定、level 字段校验统一收口
MAX_UNLOCK_LEVEL = 7


class VocabExamStore:
    """vocab_exams 读写 + 解锁级别计算。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or dbreg.fce_db_path()
        # SCHEMA 全部 CREATE IF NOT EXISTS，幂等（生产库已含 paper_id 列）
        ensure_schema(self.db_path, SCHEMA)

    def _connect(self):
        return connect(self.db_path)

    def record(self, user: str, level: int, score: int, exam_date: str,
               note: str = "", paper_id: str = "",
               wrong_words: Optional[list] = None) -> dict:
        """登记一次成绩。wrong_words 非 None 时重写该卷错词明细
       （只存错行；传 [] 表示清空明细，None 表示不动明细）。"""
        if not (0 <= int(level) <= MAX_UNLOCK_LEVEL):
            raise ValueError(f"level 须为 0-{MAX_UNLOCK_LEVEL}")
        if not (0 <= int(score) <= 100):
            raise ValueError("score 须为 0-100")
        datetime.strptime(exam_date, "%Y-%m-%d")  # 非法日期抛 ValueError
        who = (user or "").strip()[:60]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO vocab_exams (user, level, score, exam_date, note, created_at, paper_id)"
                " VALUES (?,?,?,?,?,?,?)",
                (who, int(level), int(score), exam_date,
                 (note or "")[:500],
                 datetime.now(_tz.utc).isoformat(timespec="seconds"),
                 (paper_id or "")[:64]),
            )
            if wrong_words is not None:
                self._replace_answers(conn, cur.lastrowid, who, wrong_words)
            row = conn.execute(
                "SELECT * FROM vocab_exams WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return dict(row)

    def delete_exam(self, exam_id: int) -> int:
        """删一条考试记录（错词明细一并删），返回删除行数（0=不存在）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM vocab_exam_answers WHERE exam_id = ?",
                         (int(exam_id),))
            cur = conn.execute("DELETE FROM vocab_exams WHERE id = ?", (exam_id,))
        return cur.rowcount

    @staticmethod
    def _replace_answers(conn: sqlite3.Connection, exam_id: int, user: str,
                         wrong_words: Optional[list]) -> None:
        """重写一份成绩的错词明细：先删旧行再插入本次错词（qtype 留空）。

        词归一：去空白、小写、截 60 字符、去重（同名错一次只记一行）。
        """
        conn.execute("DELETE FROM vocab_exam_answers WHERE exam_id = ?",
                     (int(exam_id),))
        seen: set[str] = set()
        rows = []
        for w in wrong_words or []:
            word = str(w or "").strip().lower()[:60]
            if not word or word in seen:
                continue
            seen.add(word)
            rows.append((int(exam_id), user, word, "", 0,
                         datetime.now(_tz.utc).isoformat(timespec="seconds")))
        if rows:
            conn.executemany(
                "INSERT INTO vocab_exam_answers"
                " (exam_id, user, word, qtype, correct, created_at)"
                " VALUES (?,?,?,?,?,?)", rows)

    def answers_for_exams(self, exam_ids: list) -> dict[int, list[str]]:
        """批量取多份成绩的错词（批改卡汇总用；一次查询防 N+1）。

        返回 {exam_id: [word, ...]}，未登记明细的 exam_id 不出现在结果里。
        """
        ids = [int(x) for x in (exam_ids or []) if x]
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT exam_id, word FROM vocab_exam_answers"
                f" WHERE exam_id IN ({ph}) ORDER BY id", ids).fetchall()
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r["exam_id"], []).append(r["word"])
        return out

    def answers_for_exam(self, exam_id: int) -> list[dict]:
        """单份成绩的错词明细（明细查看用，含登记时间）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, exam_id, user, word, qtype, correct, created_at"
                " FROM vocab_exam_answers WHERE exam_id = ? ORDER BY id",
                (int(exam_id),)).fetchall()
        return [dict(r) for r in rows]

    def exams_for_papers(self, paper_ids: list[str]) -> dict[str, list[dict]]:
        """按试卷 id 反查已登记成绩（批改中心打分卡：标记哪卷已登分）。"""
        ids = [str(x)[:64] for x in paper_ids if x]
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, user, level, score, exam_date, paper_id"
                f" FROM vocab_exams WHERE paper_id IN ({ph})", ids).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["paper_id"], []).append(dict(r))
        return out

    def list(self, user: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM vocab_exams"
        args: tuple = ()
        if user:
            sql += " WHERE user = ?"
            args = (user,)
        sql += " ORDER BY exam_date DESC, id DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def get(self, exam_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM vocab_exams WHERE id = ?", (int(exam_id),)
            ).fetchone()
        return dict(row) if row else None

    def update(self, exam_id: int, user: str, level: int, score: int,
               exam_date: str, note: str = "", paper_id: str = "",
               wrong_words: Optional[list] = None) -> Optional[dict]:
        """改一条考试记录。不存在返回 None。字段校验与 record 相同。

        wrong_words：None=不改明细；传 list=重写明细（[] 清空）。
        换归属学生时，明细行的 user 一并改到新学生。
        """
        if not (0 <= int(level) <= MAX_UNLOCK_LEVEL):
            raise ValueError(f"level 须为 0-{MAX_UNLOCK_LEVEL}")
        if not (0 <= int(score) <= 100):
            raise ValueError("score 须为 0-100")
        datetime.strptime(exam_date, "%Y-%m-%d")
        who = (user or "").strip()[:60]
        if not who:
            raise ValueError("user 须指定学生账号")
        with self._connect() as conn:
            conn.execute(
                "UPDATE vocab_exams SET user=?, level=?, score=?, exam_date=?,"
                " note=?, paper_id=? WHERE id=?",
                (who, int(level), int(score), exam_date, (note or "")[:500],
                 (paper_id or "")[:64], int(exam_id)),
            )
            if wrong_words is not None:
                self._replace_answers(conn, int(exam_id), who, wrong_words)
            row = conn.execute(
                "SELECT * FROM vocab_exams WHERE id = ?", (int(exam_id),)
            ).fetchone()
        return dict(row) if row else None

    def unlocked_level(self, user: str) -> int:
        """学生当前单词级别（背单词能看到的最高级）。

        教师在 vocab_unlock 里显式设定过 → 以该值为准，与考试是否
        ≥PASS_SCORE 无关。从未设定过 → L0 恒开；L{n} 考试 ≥PASS_SCORE
        解锁 L{n+1}（上限 MAX_UNLOCK_LEVEL）。
        """
        configured = self._configured_level(user)
        if configured is not None:
            return configured
        passed = self.passed_levels(user)
        return min(MAX_UNLOCK_LEVEL, max(passed) + 1) if passed else 0

    # ---- 手动解锁配置（教师，备课中心-背单词） ----

    def _configured_level(self, user: str) -> Optional[int]:
        """教师显式设定的单词级别；没有设定行时返回 None（与「设成 0」区分）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT level FROM vocab_unlock WHERE user = ?",
                ((user or "").strip()[:60],),
            ).fetchone()
        if row is None:
            return None
        return min(MAX_UNLOCK_LEVEL, max(0, int(row["level"])))

    def level_configured(self, user: str) -> bool:
        return self._configured_level(user) is not None

    def get_unlock_override(self, user: str) -> int:
        """教师手动配置的单词级别（未配置返回 0）。"""
        lv = self._configured_level(user)
        return 0 if lv is None else lv

    def put_unlock(self, user: str, level: int) -> None:
        lv = min(MAX_UNLOCK_LEVEL, max(0, int(level)))
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vocab_unlock (user, level, updated_at)"
                " VALUES (?,?,?)",
                ((user or "").strip()[:60], lv,
                 datetime.now(_tz.utc).isoformat(timespec="seconds")),
            )

    def passed_levels(self, user: str) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT level FROM vocab_exams"
                " WHERE user = ? AND score >= ?",
                ((user or "").strip()[:60], PASS_SCORE),
            ).fetchall()
        return {r["level"] for r in rows}


# ---- 考卷生成 ---------------------------------------------------------------- #

def _grammar_db_path() -> Optional[str]:
    try:
        p = dbreg.grammar_db_path()
        return p if Path(p).exists() else None
    except Exception:
        return None


def load_level_words(level: int, limit: int = 0) -> list[dict]:
    """取某级别词条（word/gloss/meanings），按词频或字母序。"""
    path = _grammar_db_path()
    if not path:
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        sql = ("SELECT word, pos, gloss, meanings, extra FROM vocab_word"
               " WHERE level = ?")
        rows = conn.execute(sql, (int(level),)).fetchall()
    out = []
    for r in rows:
        meanings = loads_json(r["meanings"], [])
        # L0 的 meanings[0] 常是语料例句而非释义（many→“都表示‘许多’…”），
        # 题面用 gloss（词典式紧凑释义）兜底切分
        if not exam_allowed(r["extra"]):
            continue
        gloss = (r["gloss"] or "").strip()
        meaning = gloss.split("/")[0].split(",")[0].strip() or gloss
        if not meaning and meanings and len(str(meanings[0])) <= 12:
            meaning = str(meanings[0])
        out.append({
            "word": r["word"], "gloss": gloss, "meaning": meaning or "—",
            "freq": int(json.loads(r["extra"] or "{}").get("freq") or 0),
        })
    out.sort(key=lambda x: (-(x["freq"] or 0), x["word"]))
    if limit:
        out = out[:limit]
    return out

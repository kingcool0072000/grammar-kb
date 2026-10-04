"""背单词练习成绩上报（fce.db 同库）。

学生端每完成一组练习（recite.js finish）自动上报：题数、首答错词数、
正确率、用时、错词列表与练习模式（打字输入/翻面自评），以及逐词对错
明细（写入 recite_word_progress，云端唯一进度源）。教师端在
「批改中心」查看。数据量极小，不设删除（历史即成长记录）。

错词走艾宾浩斯记忆周期（recite_review 表）：答错日 D 起，在 D+1/2/4/7/
15/30 六个节点各复习一次；复习（scope=wrongbook 会话）答对过一节点，
六节点全过即「攻克」入已攻克词本；任何会话再答错则回炉重计周期。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

# 艾宾浩斯复习节点（天）：D 为本轮周期首次答错日
EB_STAGES = (1, 2, 4, 7, 15, 30)
# 复习会话标识（scope 值）：该类会话里的答对才推进节点
REVIEW_SCOPE = "wrongbook"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recite_sessions (
    id          INTEGER PRIMARY KEY,
    user        TEXT NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    wrong       INTEGER NOT NULL DEFAULT 0,
    acc         INTEGER NOT NULL DEFAULT 0,
    duration_sec INTEGER DEFAULT 0,
    wrong_words TEXT DEFAULT '[]',
    mode        TEXT DEFAULT '',
    scope       TEXT DEFAULT '',
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_rs_user ON recite_sessions(user, created_at);
CREATE TABLE IF NOT EXISTS recite_word_progress (
    user      TEXT NOT NULL,
    word      TEXT NOT NULL,
    level     INTEGER,
    right     INTEGER NOT NULL DEFAULT 0,
    wrong     INTEGER NOT NULL DEFAULT 0,
    mastered_at TEXT,
    last_seen TEXT,
    PRIMARY KEY (user, word)
);
CREATE INDEX IF NOT EXISTS idx_rwp_user_seen ON recite_word_progress(user, last_seen);
CREATE TABLE IF NOT EXISTS recite_review (
    user      TEXT NOT NULL,
    word      TEXT NOT NULL,
    level     INTEGER,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    stage     INTEGER NOT NULL DEFAULT 0,
    first_wrong_at TEXT,
    next_review TEXT,
    last_review_at TEXT,
    conquered_at TEXT,
    PRIMARY KEY (user, word)
);
CREATE INDEX IF NOT EXISTS idx_rr_due ON recite_review(user, next_review);
"""


def _add_days(day_iso: str, n: int) -> str:
    try:
        return (date.fromisoformat(day_iso) + timedelta(days=n)).isoformat()
    except ValueError:
        return datetime.now(_tz.utc).date().isoformat()


class ReciteStore:
    """recite_sessions 读写。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def submit(
        self, user: str, total: int, wrong: int, acc: int, duration_sec: int,
        wrong_words: Optional[list], mode: str = "", scope: str = "",
        details: Optional[list] = None,
    ) -> dict:
        if total <= 0:
            raise ValueError("题数须为正")
        wrong_words = [str(w)[:60] for w in (wrong_words or [])][:50]
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        review_stats: dict = {}
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO recite_sessions (user, total, wrong, acc, duration_sec,"
                " wrong_words, mode, scope, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (user, int(total), int(wrong), max(0, min(int(acc), 100)),
                 max(0, int(duration_sec or 0)), json.dumps(wrong_words, ensure_ascii=False),
                 mode or "", scope or "", now),
            )
            if details:
                self._upsert_details(conn, user, details)
                review_stats = self._update_reviews(
                    conn, user, details, is_review=(scope == REVIEW_SCOPE))
            row = conn.execute(
                "SELECT * FROM recite_sessions WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        out = _out(row)
        out["review"] = review_stats
        return out

    def _upsert_details(self, conn: sqlite3.Connection, user: str, details: list) -> None:
        """逐词对错明细落 recite_word_progress（累加计数 + 补 level）。

        details: [{word, correct}]。level 从本地 grammar.db 的 vocab_word
        小写 join 补全，join 不上的存 NULL（不阻塞上报）。
        """
        clean: dict[str, int] = {}
        for d in details or []:
            w = str(d.get("word", "")).strip().lower()[:60]
            if w:
                clean[w] = 1 if d.get("correct") else 0
        if not clean:
            return
        levels = self._vocab_levels(list(clean))
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        for w, correct in clean.items():
            row = conn.execute(
                "SELECT right, wrong, level, mastered_at FROM recite_word_progress"
                " WHERE user = ? AND word = ?", (user, w),
            ).fetchone()
            if row:
                r = row["right"] + (1 if correct else 0)
                wr = row["wrong"] + (0 if correct else 1)
                lvl = row["level"] if row["level"] is not None else levels.get(w)
                mastered = row["mastered_at"]
                if not mastered and r >= max(wr, 1):
                    mastered = now
                conn.execute(
                    "UPDATE recite_word_progress SET right=?, wrong=?, level=?,"
                    " mastered_at=?, last_seen=? WHERE user=? AND word=?",
                    (r, wr, lvl, mastered, now, user, w),
                )
            else:
                r, wr = (1, 0) if correct else (0, 1)
                conn.execute(
                    "INSERT INTO recite_word_progress (user, word, level, right, wrong,"
                    " mastered_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                    (user, w, levels.get(w), r, wr,
                     now if correct else None, now),
                )

    def _update_reviews(self, conn: sqlite3.Connection, user: str,
                        details: list, is_review: bool) -> dict:
        """艾宾浩斯周期状态机：答错 → 进/回炉周期；复习会话答对 → 推进
        一个节点；过满六节点（D+1/2/4/7/15/30）→ 攻克入已攻克词本。

        普通会话的答对不推进节点（避免常规练习顺手清空错题周期）；
        任何会话的答错都会把该词拉回 stage=0 重计。返回本组统计
        {entered, advanced, conquered, due_now}。
        """
        clean: dict[str, int] = {}
        for d in details or []:
            w = str(d.get("word", "")).strip().lower()[:60]
            if w and w not in clean:
                clean[w] = 1 if d.get("correct") else 0
        if not clean:
            return {}
        today = datetime.now(_tz.utc).date().isoformat()
        levels = self._vocab_levels(list(clean))
        stats = {"entered": 0, "advanced": 0, "conquered": 0, "due_now": 0}
        for w, correct in clean.items():
            row = conn.execute(
                "SELECT wrong_count, stage, conquered_at FROM recite_review"
                " WHERE user = ? AND word = ?", (user, w),
            ).fetchone()
            if not correct:
                # 答错：进周期（或攻克后回炉，stage 清零重计）
                wc = (row["wrong_count"] + 1) if row else 1
                conn.execute(
                    "INSERT INTO recite_review (user, word, level, wrong_count, stage,"
                    " first_wrong_at, next_review, last_review_at)"
                    " VALUES (?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(user, word) DO UPDATE SET wrong_count=excluded.wrong_count,"
                    " level=COALESCE(recite_review.level, excluded.level), stage=0,"
                    " first_wrong_at=excluded.first_wrong_at,"
                    " next_review=excluded.next_review, conquered_at=NULL",
                    (user, w, levels.get(w), wc, 0, today,
                     _add_days(today, EB_STAGES[0]), today),
                )
                stats["entered"] += 1
            elif row and not row["conquered_at"]:
                if not is_review:
                    continue  # 普通会话答对不动周期
                stage = row["stage"] + 1
                if stage >= len(EB_STAGES):
                    conn.execute(
                        "UPDATE recite_review SET stage=?, next_review=NULL,"
                        " last_review_at=?, conquered_at=? WHERE user=? AND word=?",
                        (stage, today, today, user, w),
                    )
                    stats["conquered"] += 1
                else:
                    conn.execute(
                        "UPDATE recite_review SET stage=?, next_review=?, last_review_at=?"
                        " WHERE user=? AND word=?",
                        (stage, _add_days(today, EB_STAGES[stage]), today, user, w),
                    )
                    stats["advanced"] += 1
        stats["due_now"] = conn.execute(
            "SELECT COUNT(*) FROM recite_review WHERE user=? AND conquered_at IS NULL"
            " AND next_review IS NOT NULL AND next_review <= ?",
            (user, today),
        ).fetchone()[0]
        return stats

    def _vocab_levels(self, words: list[str]) -> dict[str, int]:
        """从 grammar.db vocab_word 取词的层级（小写 join；库缺失返回空）。"""
        out: dict[str, int] = {}
        try:
            import sqlite3 as _sq
            from .ingest import default_db_path as _grammar_db
            path = _grammar_db()
            if not Path(path).exists():
                return out
            with _sq.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                marks = ",".join("?" * len(words))
                rows = conn.execute(
                    f"SELECT word, level FROM vocab_word WHERE lower(word) IN ({marks})",
                    words,
                ).fetchall()
                out = {r["word"].lower(): r["level"] for r in rows}
        except Exception:
            pass  # level 补全失败不阻塞进度上报
        return out

    def progress(self, user: str, include_words: bool = False) -> dict:
        """逐词进度 + 汇总。掌握口径：right >= max(wrong, 1)。

        返回 {total_seen, mastered, levels: {level: {total, seen, mastered}}, words?}。
        levels 的 total 需要词库总量（vocab_word 按 level 计数）；词库不可
        用时 total 为 None。
        """
        user = (user or "").strip()[:60]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT word, level, right, wrong, mastered_at, last_seen"
                " FROM recite_word_progress WHERE user = ? ORDER BY last_seen DESC",
                (user,),
            ).fetchall()
        per_level: dict[int, dict] = {}
        mastered = 0
        words: list[dict] = []
        for r in rows:
            m = r["right"] >= max(r["wrong"], 1)
            mastered += 1 if m else 0
            lvl = r["level"]
            if lvl is not None:
                d = per_level.setdefault(lvl, {"seen": 0, "mastered": 0})
                d["seen"] += 1
                d["mastered"] += 1 if m else 0
            words.append({
                "word": r["word"], "level": lvl, "right": r["right"],
                "wrong": r["wrong"], "mastered": m,
                "mastered_at": r["mastered_at"], "last_seen": r["last_seen"],
            })
        totals = self._vocab_level_totals()
        levels_out = {
            lvl: {"total": totals.get(lvl), "seen": d["seen"], "mastered": d["mastered"]}
            for lvl, d in sorted(per_level.items())
        }
        out = {
            "user": user, "total_seen": len(rows), "mastered": mastered,
            "levels": levels_out,
        }
        if include_words:
            out["words"] = words
        return out

    def _vocab_level_totals(self) -> dict[int, int]:
        try:
            import sqlite3 as _sq
            from .ingest import default_db_path as _grammar_db
            path = _grammar_db()
            if not Path(path).exists():
                return {}
            with _sq.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT level, COUNT(*) n FROM vocab_word GROUP BY level"
                ).fetchall()
                return {r["level"]: r["n"] for r in rows}
        except Exception:
            return {}

    def mastered_words(self, user: str) -> set[str]:
        """已掌握词集合（出题剔除用；right >= max(wrong, 1)）。"""
        user = (user or "").strip()[:60]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT word FROM recite_word_progress"
                " WHERE user = ? AND right >= max(wrong, 1)",
                (user,),
            ).fetchall()
        return {r["word"] for r in rows}

    def clear_progress(self, user: str) -> int:
        """清空某学生的云端逐词进度（教师操作），返回删除行数。

        一并清掉艾宾浩斯周期（错词本在下次答错时自然重建）。
        """
        user = (user or "").strip()[:60]
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM recite_word_progress WHERE user = ?", (user,)
            )
            conn.execute("DELETE FROM recite_review WHERE user = ?", (user,))
            return cur.rowcount

    def merge_progress(self, user: str, data: dict) -> dict:
        """本地进度快照合并上云：逐词 right/wrong 取 max，幂等。

        data: {word: {right: n, wrong: m}}（旧 localStorage 结构）。
        词归一小写；level 从 vocab_word 补全。返回合并后的云端全量。
        """
        user = (user or "").strip()[:60]
        clean: dict[str, tuple[int, int]] = {}
        for w, v in (data or {}).items():
            w = str(w).strip().lower()[:60]
            if not isinstance(v, dict):
                continue
            try:
                r = max(0, min(int(v.get("right", 0)), 999))
                wr = max(0, min(int(v.get("wrong", 0)), 999))
            except (TypeError, ValueError):
                continue
            if r or wr:
                clean[w] = (r, wr)
        if clean:
            levels = self._vocab_levels(list(clean))
            now = datetime.now(_tz.utc).isoformat(timespec="seconds")
            with self._connect() as conn:
                for w, (r, wr) in clean.items():
                    row = conn.execute(
                        "SELECT right, wrong, level, mastered_at FROM recite_word_progress"
                        " WHERE user = ? AND word = ?", (user, w),
                    ).fetchone()
                    if row:
                        nr, nw = max(row["right"], r), max(row["wrong"], wr)
                        lvl = row["level"] if row["level"] is not None else levels.get(w)
                        mastered = row["mastered_at"]
                        if not mastered and nr >= max(nw, 1):
                            mastered = now
                        conn.execute(
                            "UPDATE recite_word_progress SET right=?, wrong=?, level=?,"
                            " mastered_at=?, last_seen=COALESCE(last_seen, ?)"
                            " WHERE user=? AND word=?",
                            (nr, nw, lvl, mastered, now, user, w),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO recite_word_progress (user, word, level, right,"
                            " wrong, mastered_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                            (user, w, levels.get(w), r, wr,
                             now if r >= max(wr, 1) else None, now),
                        )
        return self.progress(user, include_words=True)

    def wrongbook(self, user: str, limit: int = 500) -> dict:
        """错词本（艾宾浩斯口径）：在周期词（含到期/节点/日程）+ 已攻克词本。

        返回 {in_cycle, due_today, conquered, stats}。存量兼容：历史会话
        错过但尚无 recite_review 行的词，首次读取时按最近答错日回填
        （stage=0、next_review=最近错日+1 天——绝大多数当天就到期，
        立即进入复习流）。
        """
        user = (user or "").strip()[:60]
        today = datetime.now(_tz.utc).date().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT wrong_words, created_at FROM recite_sessions"
                " WHERE user = ? ORDER BY id DESC LIMIT ?",
                (user, int(limit)),
            ).fetchall()
            wrong_at: dict[str, str] = {}
            counts: dict[str, int] = {}
            for r in rows:  # 由新到旧：setdefault 拿到最近答错时间
                try:
                    ws = json.loads(r["wrong_words"] or "[]")
                except (ValueError, TypeError):
                    ws = []
                for w in ws:
                    w = str(w).strip().lower()[:60]
                    if not w:
                        continue
                    counts[w] = counts.get(w, 0) + 1
                    wrong_at.setdefault(w, r["created_at"] or "")
            if counts:
                have = {r[0] for r in conn.execute(
                    "SELECT word FROM recite_review WHERE user = ?", (user,)
                ).fetchall()}
                missing = [w for w in counts if w not in have]
                if missing:
                    levels = self._vocab_levels(missing)
                    for w in missing:
                        last_day = (wrong_at.get(w) or "")[:10] or today
                        conn.execute(
                            "INSERT INTO recite_review (user, word, level, wrong_count,"
                            " stage, first_wrong_at, next_review, last_review_at)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (user, w, levels.get(w), counts[w], 0, last_day,
                             _add_days(last_day, EB_STAGES[0]), last_day),
                        )
            rr = conn.execute(
                "SELECT word, level, wrong_count, stage, first_wrong_at, next_review,"
                " last_review_at, conquered_at FROM recite_review WHERE user = ?",
                (user,),
            ).fetchall()
        in_cycle: list[dict] = []
        conquered: list[dict] = []
        for r in rr:
            base = {
                "word": r["word"], "level": r["level"],
                "wrong_count": r["wrong_count"], "stage": r["stage"],
                "first_wrong_at": r["first_wrong_at"],
                "next_review": r["next_review"],
                "last_review_at": r["last_review_at"],
            }
            if r["conquered_at"]:
                conquered.append({**base, "conquered_at": r["conquered_at"]})
            else:
                base["due"] = bool(r["next_review"] and r["next_review"] <= today)
                in_cycle.append(base)
        # 到期在前；同期按错次降序（错得多的先见）
        in_cycle.sort(key=lambda x: (x["next_review"] or "9999-12-31",
                                     -x["wrong_count"]))
        conquered.sort(key=lambda x: x["conquered_at"] or "", reverse=True)
        due_today = [x for x in in_cycle if x["due"]]
        stage_dist = [0] * len(EB_STAGES)
        for x in in_cycle:
            stage_dist[min(x["stage"], len(EB_STAGES) - 1)] += 1
        return {
            "in_cycle": in_cycle,
            "due_today": due_today,
            "conquered": conquered,
            "stats": {
                "total": len(in_cycle), "due": len(due_today),
                "conquered_n": len(conquered),
                "stages": list(EB_STAGES), "stage_dist": stage_dist,
            },
        }

    def list(self, user: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM recite_sessions"
        args: tuple = ()
        if user:
            sql += " WHERE user = ?"
            args = (user,)
        sql += " ORDER BY id DESC LIMIT ?"
        args += (int(limit),)
        with self._connect() as conn:
            return [_out(r) for r in conn.execute(sql, args).fetchall()]


def _out(r: sqlite3.Row) -> dict:
    try:
        wrong_words = json.loads(r["wrong_words"] or "[]")
    except (ValueError, TypeError):
        wrong_words = []
    return {
        "id": r["id"], "user": r["user"], "total": r["total"], "wrong": r["wrong"],
        "acc": r["acc"], "duration_sec": r["duration_sec"],
        "wrong_words": wrong_words, "mode": r["mode"], "scope": r["scope"],
        "created_at": r["created_at"],
    }

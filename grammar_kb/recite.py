"""背单词练习成绩上报（fce.db 同库）。

学生端每完成一组练习（recite.js finish）自动上报：题数、首答错词数、
正确率、用时、错词列表与练习模式（打字输入/翻面自评），以及逐词对错
明细（写入 recite_word_progress，云端唯一进度源）。教师端在
「批改中心」查看。数据量极小，不设删除（历史即成长记录）。

错词走艾宾浩斯记忆周期（recite_review 表）：答错日 D 起，在 D+1/2/4/7/
15/30 六个节点各复习一次；复习会话（scope=wrongbook，或 mode=conquer）
答对过一节点，六节点全过即「攻克」入已攻克词本；任何会话再答错则回炉重计。

复习闭环摘要（/recite/review-summary）与错词本同一口径：在周期 =
conquered_at 为空；到期 = next_review 等于今天；逾期 = next_review 早于
今天（北京日历）。逾期未清时限制当天首次见到的新词。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from typing import Optional

from .atlas_vocab import canonical_word
from .clock import CN_TZ, now_cn, today_cn
from .fce_query import _default_db_path

# 艾宾浩斯复习节点（天）：D 为本轮周期首次答错日
EB_STAGES = (1, 2, 4, 7, 15, 30)
# 复习会话标识（scope 值）：该类会话里的答对才推进节点
REVIEW_SCOPE = "wrongbook"
# mode=conquer 同样是复习会话（池子只有错词本，答对推进同一套节点）
CONQUER_MODE = "conquer"
# 逾期 > 0 时，当天「首次见到」的新词上限。环境变量 RECITE_NEW_WORD_CAP 可覆盖。
NEW_WORD_CAP = 20
# 教师告警：在周期达到此数、且近 N 日没有错词周期攻克、但有背词会话
ALERT_IN_CYCLE = 50
ALERT_QUIET_DAYS = 14
# 学生卡「近 7 日攻克」窗口（含今天，北京日历）
CONQUERED_RECENT_DAYS = 7

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
    first_seen TEXT,
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


def _headword(word: str) -> str:
    """别名折到词光词头。没有别名表时原样返回。"""
    return canonical_word(word)[:60]


def _fold_alias_progress(conn: sqlite3.Connection) -> None:
    """把派生词/拼写变体上的背词进度并到词光词头。

    只改词这个键，right/wrong 相加，不删库、不清空。跑过的别名版本记在
    recite_meta，别名表没变就不再扫。
    """
    from .atlas_vocab import load_alias_file

    aliases = load_alias_file()
    if not aliases:
        return
    import hashlib
    revision = hashlib.sha256(
        json.dumps(aliases, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    conn.execute(
        "CREATE TABLE IF NOT EXISTS recite_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = conn.execute(
        "SELECT value FROM recite_meta WHERE key = 'atlas_alias_revision'"
    ).fetchone()
    if row and row[0] == revision:
        return
    targets = {alias: canonical_word(alias, aliases) for alias in aliases}
    _fold_table(conn, "recite_word_progress", targets, progress=True)
    _fold_table(conn, "recite_review", targets, progress=False)
    _rewrite_session_words(conn, targets)
    conn.execute(
        "INSERT INTO recite_meta(key, value) VALUES('atlas_alias_revision', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (revision,),
    )


def _fold_table(conn, table: str, targets: dict[str, str], *, progress: bool) -> None:
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if table not in names:
        return
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    for row in rows:
        alias = row["word"]
        canon = targets.get(alias)
        if not canon or canon == alias:
            continue
        parent = conn.execute(
            f"SELECT * FROM {table} WHERE user = ? AND word = ?",
            (row["user"], canon),
        ).fetchone()
        if parent is None:
            conn.execute(
                f"UPDATE {table} SET word = ? WHERE user = ? AND word = ?",
                (canon, row["user"], alias),
            )
            continue
        if progress:
            firsts = [v for v in (parent["first_seen"], row["first_seen"]) if v]
            lasts = [v for v in (parent["last_seen"], row["last_seen"]) if v]
            mastered_vals = [v for v in (parent["mastered_at"], row["mastered_at"]) if v]
            conn.execute(
                "UPDATE recite_word_progress SET right = ?, wrong = ?, "
                "mastered_at = ?, first_seen = ?, last_seen = ? "
                "WHERE user = ? AND word = ?",
                (
                    parent["right"] + row["right"],
                    parent["wrong"] + row["wrong"],
                    min(mastered_vals) if mastered_vals else None,
                    min(firsts) if firsts else None,
                    max(lasts) if lasts else None,
                    row["user"], canon,
                ),
            )
        else:
            conquered = parent["conquered_at"]
            stage = parent["stage"]
            nxt = parent["next_review"]
            if not conquered and row["next_review"]:
                if not nxt or row["next_review"] < nxt:
                    nxt = row["next_review"]
                    stage = row["stage"]
            conn.execute(
                "UPDATE recite_review SET wrong_count = ?, stage = ?, next_review = ? "
                "WHERE user = ? AND word = ?",
                (
                    parent["wrong_count"] + row["wrong_count"],
                    stage, nxt, row["user"], canon,
                ),
            )
        conn.execute(
            f"DELETE FROM {table} WHERE user = ? AND word = ?",
            (row["user"], alias),
        )


def _rewrite_session_words(conn, targets: dict[str, str]) -> None:
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "recite_sessions" not in names:
        return
    rows = conn.execute("SELECT id, wrong_words FROM recite_sessions").fetchall()
    for row in rows:
        try:
            words = json.loads(row["wrong_words"] or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(words, list):
            continue
        changed = False
        rewritten = []
        for word in words:
            text = str(word).strip().lower()
            canon = targets.get(text, text)
            if canon != text:
                changed = True
            if canon:
                rewritten.append(canon)
        if changed:
            conn.execute(
                "UPDATE recite_sessions SET wrong_words = ? WHERE id = ?",
                (json.dumps(rewritten, ensure_ascii=False), row["id"]),
            )


def _calendar_today() -> str:
    """艾宾浩斯「今天」：北京日历，和计划表 today_cn 同一天。"""
    return today_cn().isoformat()


def _cn_day_of(ts: str) -> str:
    """把库存 UTC 时间戳归一成北京日历日。纯日期原样返回。"""
    raw = (ts or "").strip()
    if not raw:
        return ""
    if len(raw) <= 10:
        return raw[:10]
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(CN_TZ).date().isoformat()


def _add_days(day_iso: str, n: int) -> str:
    try:
        return (date.fromisoformat(day_iso) + timedelta(days=n)).isoformat()
    except ValueError:
        return _calendar_today()


def _within_days(day_iso: str, today: str, days: int) -> bool:
    """北京日历日 day 是否落在含 today 的最近 days 天内（今天算第 1 天）。"""
    if not day_iso or not today or days <= 0:
        return False
    try:
        delta = (date.fromisoformat(today[:10]) - date.fromisoformat(day_iso[:10])).days
    except ValueError:
        return False
    return 0 <= delta < days


def _is_review_session(scope: str, mode: str) -> bool:
    """错词本复习或攻克模式：答对才推进艾宾浩斯节点。"""
    return (scope or "") == REVIEW_SCOPE or (mode or "") == CONQUER_MODE


def new_word_base() -> int:
    """当天新词额度的配置值（默认 20）。RECITE_NEW_WORD_CAP 为非负整数时覆盖。"""
    raw = os.environ.get("RECITE_NEW_WORD_CAP", "").strip()
    if raw.isdigit():
        return max(0, min(int(raw), 500))
    return NEW_WORD_CAP


def effective_new_cap(overdue: int, base: int) -> Optional[int]:
    """逾期为 0 不限额（None）。

    逾期 > 0 时限额为 base。逾期堆已经不少于 base（没清掉）时收到 0：
    先复习再背新词，比「照样给满 base」更严，计算也只是一次比较。
    """
    if overdue <= 0:
        return None
    if overdue >= base:
        return 0
    return base


class DuplicateSessionError(ValueError):
    """同用户短时间内重复提交背词会话（连点/重试的兜底拦截）。"""


# 防重窗口（秒）：上一条会话落库后这么久内的新提交会被拒。
# 正常一组至少十几秒；曾因前端无防抖一分钟落 19 条假会话。
# RECITE_DEDUP_WINDOW_SEC 为非负整数时覆盖（0=关，测试用）。
SUBMIT_DEDUP_WINDOW_SEC = 10


def _dedup_window_sec() -> int:
    raw = os.environ.get("RECITE_DEDUP_WINDOW_SEC", "").strip()
    if raw.isdigit():
        return min(int(raw), 3600)
    return SUBMIT_DEDUP_WINDOW_SEC


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
        self._migrate(conn)
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """旧库补 first_seen。已有行保持 NULL：不把历史词算成「今天首见」。"""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(recite_word_progress)")}
        if cols and "first_seen" not in cols:
            conn.execute(
                "ALTER TABLE recite_word_progress ADD COLUMN first_seen TEXT"
            )
        _fold_alias_progress(conn)

    def _recent_submit(self, user: str, now_iso: str) -> bool:
        """防重窗口内该用户是否刚提交过会话。缺表容错返回 False。"""
        window = _dedup_window_sec()
        if window <= 0:
            return False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT MAX(created_at) m FROM recite_sessions WHERE user = ?",
                    (user,),
                ).fetchone()
        except sqlite3.Error:
            return False
        if not row or not row["m"]:
            return False
        try:
            gap = (datetime.fromisoformat(now_iso)
                   - datetime.fromisoformat(row["m"])).total_seconds()
        except ValueError:
            return False
        return 0 <= gap < window

    def submit(
        self, user: str, total: int, wrong: int, acc: int, duration_sec: int,
        wrong_words: Optional[list], mode: str = "", scope: str = "",
        details: Optional[list] = None,
    ) -> dict:
        if total <= 0:
            raise ValueError("题数须为正")
        wrong_words = [_headword(str(w)) for w in (wrong_words or [])][:50]
        wrong_words = [w for w in wrong_words if w]
        now = now_cn().astimezone(_tz.utc).isoformat(timespec="seconds")
        today = _calendar_today()
        # 防重兜底：同用户短窗口内只收一条会话。曾因前端入口连点无防抖，
        # 同一分钟落了 19 条递增假会话（每条还各推一次艾宾浩斯节点）。
        # 正常节奏下一组至少十几秒；连续两条提交只可能是连点或重试。
        if self._recent_submit(user, now):
            raise DuplicateSessionError("提交太频繁，请稍候再试（上一组刚提交）")
        # 先按错词本口径回填并取逾期，再决定今天还能不能见新词。
        pre = self.review_summary(user)
        cap = pre["new_cap"]
        review_stats: dict = {}
        with self._connect() as conn:
            fresh = self._unseen_words(conn, user, details)
            # 本组没有首见新词（复习/攻克/已见过）时不拦，即使今天已经用满额度。
            if cap is not None and fresh and pre["new_used"] + len(fresh) > cap:
                raise ValueError(
                    f"今日新词已达上限（{pre['new_used']}/{cap}），先清逾期再背新词"
                )
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
                    conn, user, details,
                    is_review=_is_review_session(scope, mode),
                )
            row = conn.execute(
                "SELECT * FROM recite_sessions WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        out = _out(row)
        out["review"] = review_stats
        out["new_used"] = self._count_new_used(user, today)
        out["new_cap"] = cap
        return out

    def _upsert_details(self, conn: sqlite3.Connection, user: str, details: list) -> None:
        """逐词对错明细落 recite_word_progress（累加计数 + 补 level）。

        details: [{word, correct}]。level 从本地 grammar.db 的 vocab_word
        小写 join 补全，join 不上的存 NULL（不阻塞上报）。
        """
        clean: dict[str, int] = {}
        for d in details or []:
            w = _headword(str(d.get("word", "")).strip().lower())
            if not w:
                continue
            correct = 1 if d.get("correct") else 0
            # 同一词头的别名一起交卷：有一次答错就记错。
            clean[w] = correct if w not in clean else min(clean[w], correct)
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
                # 已在错词周期里的词不是「今天首见的新词」（含历史回填）。
                in_cycle = conn.execute(
                    "SELECT 1 FROM recite_review WHERE user=? AND word=?",
                    (user, w),
                ).fetchone()
                conn.execute(
                    "INSERT INTO recite_word_progress (user, word, level, right, wrong,"
                    " mastered_at, last_seen, first_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (user, w, levels.get(w), r, wr,
                     now if correct else None, now,
                     None if in_cycle else now),
                )

    def _update_reviews(self, conn: sqlite3.Connection, user: str,
                        details: list, is_review: bool) -> dict:
        """艾宾浩斯周期状态机：答错 → 进/回炉周期；复习会话答对 → 推进
        一个节点；过满六节点（D+1/2/4/7/15/30）→ 攻克入已攻克词本。

        复习会话 = scope=wrongbook 或 mode=conquer。普通会话的答对不推进
        节点（避免常规练习顺手清空错题周期）；任何会话的答错都会把该词
        拉回 stage=0 重计。返回本组统计 {entered, advanced, conquered, due_now}。
        """
        clean: dict[str, int] = {}
        for d in details or []:
            w = _headword(str(d.get("word", "")).strip().lower())
            if not w:
                continue
            correct = 1 if d.get("correct") else 0
            if w not in clean:
                clean[w] = correct
            else:
                clean[w] = min(clean[w], correct)
        if not clean:
            return {}
        today = _calendar_today()
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
            w = _headword(str(w).strip().lower())
            if not isinstance(v, dict):
                continue
            try:
                r = max(0, min(int(v.get("right", 0)), 999))
                wr = max(0, min(int(v.get("wrong", 0)), 999))
            except (TypeError, ValueError):
                continue
            if r or wr:
                prev = clean.get(w)
                clean[w] = (max(prev[0], r), max(prev[1], wr)) if prev else (r, wr)
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
                        # 本地历史合并不是「今天新学的词」，first_seen 留空，
                        # 不占用逾期时的新词额度。
                        conn.execute(
                            "INSERT INTO recite_word_progress (user, word, level, right,"
                            " wrong, mastered_at, last_seen, first_seen)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (user, w, levels.get(w), r, wr,
                             now if r >= max(wr, 1) else None, now, None),
                        )
        return self.progress(user, include_words=True)

    def _backfill_review(self, conn: sqlite3.Connection, user: str, limit: int = 500) -> None:
        """历史会话错过但尚无 recite_review 行的词，按最近答错日回填。"""
        today = _calendar_today()
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
                w = _headword(str(w).strip().lower())
                if not w:
                    continue
                counts[w] = counts.get(w, 0) + 1
                wrong_at.setdefault(w, r["created_at"] or "")
        if not counts:
            return
        have = {r[0] for r in conn.execute(
            "SELECT word FROM recite_review WHERE user = ?", (user,)
        ).fetchall()}
        missing = [w for w in counts if w not in have]
        if not missing:
            return
        levels = self._vocab_levels(missing)
        for w in missing:
            last_day = _cn_day_of(wrong_at.get(w) or "") or today
            conn.execute(
                "INSERT INTO recite_review (user, word, level, wrong_count,"
                " stage, first_wrong_at, next_review, last_review_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user, w, levels.get(w), counts[w], 0, last_day,
                 _add_days(last_day, EB_STAGES[0]), last_day),
            )

    def wrongbook(self, user: str, limit: int = 500) -> dict:
        """错词本（艾宾浩斯口径）：在周期词（含到期/节点/日程）+ 已攻克词本。

        返回 {in_cycle, due_today, conquered, stats}。存量兼容：历史会话
        错过但尚无 recite_review 行的词，首次读取时按最近答错日回填
        （stage=0、next_review=最近错日+1 天——绝大多数当天就到期，
        立即进入复习流）。
        """
        user = (user or "").strip()[:60]
        today = _calendar_today()
        with self._connect() as conn:
            self._backfill_review(conn, user, limit)
            rr = conn.execute(
                "SELECT word, level, wrong_count, stage, first_wrong_at, next_review,"
                " last_review_at, conquered_at FROM recite_review WHERE user = ?",
                (user,),
            ).fetchall()
            # 已攻克 = 全部已掌握词（right >= max(wrong,1)，含一遍背会、
            # 从未进错题本的）；错题周期走完的词带 conquered_at 标注来源。
            mp_rows = conn.execute(
                "SELECT word, level, right, wrong, mastered_at, last_seen"
                " FROM recite_word_progress WHERE user = ?"
                " AND right >= max(wrong, 1)",
                (user,),
            ).fetchall()
            rr_wc = {r["word"]: r["wrong_count"] for r in rr}
            rr_cq = {r["word"]: r["conquered_at"] for r in rr if r["conquered_at"]}
        in_cycle: list[dict] = []
        for r in rr:
            if r["conquered_at"]:
                continue  # 已攻克的词不再占复习位（列表由 mastered 全集出）
            in_cycle.append({
                "word": r["word"], "level": r["level"],
                "wrong_count": r["wrong_count"], "stage": r["stage"],
                "first_wrong_at": r["first_wrong_at"],
                "next_review": r["next_review"],
                "last_review_at": r["last_review_at"],
                "due": bool(r["next_review"] and r["next_review"] <= today),
            })
        conquered = [
            {
                "word": r["word"], "level": r["level"],
                "right": r["right"], "wrong": r["wrong"],
                "wrong_count": rr_wc.get(r["word"], 0),
                "mastered_at": r["mastered_at"], "last_seen": r["last_seen"],
                "conquered_at": rr_cq.get(r["word"]),
            }
            for r in mp_rows
        ]
        have = {x["word"] for x in conquered}
        for w, cq in rr_cq.items():
            if w not in have:  # 周期攻克但计数未过掌握线（罕见）也归入
                conquered.append({
                    "word": w, "level": None, "right": 0, "wrong": 0,
                    "wrong_count": rr_wc.get(w, 0), "mastered_at": cq,
                    "last_seen": None, "conquered_at": cq,
                })
        # 到期在前；同期按错次降序（错得多的先见）
        in_cycle.sort(key=lambda x: (x["next_review"] or "9999-12-31",
                                     -x["wrong_count"]))
        conquered.sort(key=lambda x: x["mastered_at"] or "", reverse=True)
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

    def conquered_page(
        self, user: str, *, page: int = 1, limit: int = 40,
        q: str = "", also_words: Optional[list] = None,
    ) -> dict:
        """已攻克词本一页。不把全集交给调用方。

        items 为当前页；total / has_more 按筛选后的条数；
        summary 是未筛选的全集计数（一遍过 / 从错题攻克）。
        also_words：释义命中的词头，和 q 的词面子串一起筛。
        """
        user = (user or "").strip()[:60]
        page = max(1, int(page or 1))
        limit = min(100, max(1, int(limit or 40)))
        q = (q or "").strip().lower()[:40]
        extra: list[str] = []
        for raw in also_words or []:
            w = str(raw or "").strip().lower()[:60]
            if w and w not in extra:
                extra.append(w)
            if len(extra) >= 300:
                break
        where = ""
        filt: list = []
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses = ["lower(word) LIKE ? ESCAPE '\\'"]
            filt.append(like)
            if extra:
                marks = ",".join("?" * len(extra))
                clauses.append(f"lower(word) IN ({marks})")
                filt.extend(extra)
            where = " WHERE " + " OR ".join(clauses)
        src = """
            FROM (
              SELECT p.word AS word, p.level AS level, p.right AS right, p.wrong AS wrong,
                     p.mastered_at AS mastered_at, p.last_seen AS last_seen,
                     COALESCE(r.wrong_count, 0) AS wrong_count,
                     r.conquered_at AS conquered_at
              FROM recite_word_progress p
              LEFT JOIN recite_review r ON r.user = p.user AND r.word = p.word
              WHERE p.user = ? AND p.right >= max(p.wrong, 1)
              UNION ALL
              SELECT r.word, r.level, 0, 0, r.conquered_at, NULL,
                     COALESCE(r.wrong_count, 0), r.conquered_at
              FROM recite_review r
              WHERE r.user = ? AND r.conquered_at IS NOT NULL
                AND NOT EXISTS (
                  SELECT 1 FROM recite_word_progress p
                  WHERE p.user = r.user AND p.word = r.word
                    AND p.right >= max(p.wrong, 1)
                )
            )
        """
        with self._connect() as conn:
            self._backfill_review(conn, user, 500)
            base_args = (user, user)
            summary = conn.execute(
                "SELECT COUNT(*) AS n,"
                " SUM(CASE WHEN conquered_at IS NOT NULL OR wrong_count > 0"
                " THEN 1 ELSE 0 END) AS fr" + src,
                base_args,
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) AS n" + src + where, (*base_args, *filt),
            ).fetchone()["n"]
            offset = (page - 1) * limit
            rows = conn.execute(
                "SELECT word, level, right, wrong, mastered_at, last_seen,"
                " wrong_count, conquered_at" + src + where
                + " ORDER BY COALESCE(mastered_at, '') DESC, word"
                + " LIMIT ? OFFSET ?",
                (*base_args, *filt, limit, offset),
            ).fetchall()
        items = [
            {
                "word": r["word"], "level": r["level"],
                "right": r["right"], "wrong": r["wrong"],
                "wrong_count": r["wrong_count"] or 0,
                "mastered_at": r["mastered_at"], "last_seen": r["last_seen"],
                "conquered_at": r["conquered_at"],
            }
            for r in rows
        ]
        all_n = int(summary["n"] or 0)
        return {
            "items": items,
            "total": int(total or 0),
            "page": page,
            "limit": limit,
            "has_more": offset + len(items) < int(total or 0),
            "summary": {
                "total": all_n,
                "from_review": int(summary["fr"] or 0),
            },
        }

    def review_summary(self, user: str) -> dict:
        """复习闭环摘要。计数与 /recite/wrongbook、recite_review 同一口径。

        due_today：next_review 等于今天（不含逾期）。
        overdue：next_review 早于今天。
        in_cycle：conquered_at 为空的错词（含未到期）。
        conquered_n.total：已攻克词本全集（与 wrongbook stats.conquered_n 相同）。
        conquered_n.d7：其中 conquered_at（没有则 mastered_at）落在近 7 个北京日。
        conquer_pool：到期 ∪ 逾期 ∪ 在周期，不含新鲜词、不含已攻克。
        顺序：今天到期（错次多的在前）→ 逾期（更早的在前）→ 未到期。
        activity：最近的错词本/攻克会话，以及最近触碰的周期词。
        alert：在周期 ≥ 50、近 14 日没有错词周期攻克（只看 conquered_at）、
        且至少有一条背词会话。一遍背会不算把周期清掉。
        """
        user = (user or "").strip()[:60]
        today = _calendar_today()
        wb = self.wrongbook(user)
        in_cycle = wb["in_cycle"]
        conquered = wb["conquered"]
        due = [x for x in in_cycle if (x.get("next_review") or "") == today]
        overdue = [
            x for x in in_cycle
            if x.get("next_review") and x["next_review"] < today
        ]
        due_set = {x["word"] for x in due}
        over_set = {x["word"] for x in overdue}
        rest = [
            x for x in in_cycle
            if x["word"] not in due_set and x["word"] not in over_set
        ]
        due.sort(key=lambda x: (-int(x.get("wrong_count") or 0), x["word"]))
        overdue.sort(key=lambda x: (
            x["next_review"], -int(x.get("wrong_count") or 0), x["word"],
        ))
        rest.sort(key=lambda x: (x.get("next_review") or "9999-12-31", x["word"]))
        pool = [x["word"] for x in due + overdue + rest]

        def _book_day(item: dict) -> str:
            stamp = item.get("conquered_at") or item.get("mastered_at") or ""
            return _cn_day_of(stamp) if stamp else ""

        d7 = sum(
            1 for x in conquered
            if _within_days(_book_day(x), today, CONQUERED_RECENT_DAYS)
        )
        conquered_14 = sum(
            1 for x in conquered
            if x.get("conquered_at")
            and _within_days(_cn_day_of(x["conquered_at"]), today, ALERT_QUIET_DAYS)
        )
        cap = effective_new_cap(len(overdue), new_word_base())
        with self._connect() as conn:
            new_used = self._count_new_used_conn(conn, user, today)
            sessions_n = conn.execute(
                "SELECT COUNT(*) FROM recite_sessions WHERE user = ?", (user,),
            ).fetchone()[0]
            activity = self._recent_activity(conn, user)
        return {
            "user": user,
            "today": today,
            "due_today": len(due),
            "overdue": len(overdue),
            "in_cycle": len(in_cycle),
            "conquered_n": {"d7": d7, "total": len(conquered)},
            "activity": activity,
            "due_words": [x["word"] for x in due],
            "overdue_words": [x["word"] for x in overdue],
            "conquer_pool": pool,
            "new_used": new_used,
            "new_cap": cap,
            "alert": (
                len(in_cycle) >= ALERT_IN_CYCLE
                and conquered_14 == 0
                and sessions_n > 0
            ),
        }

    def _count_new_used(self, user: str, today: str) -> int:
        with self._connect() as conn:
            return self._count_new_used_conn(conn, user, today)

    @staticmethod
    def _count_new_used_conn(conn: sqlite3.Connection, user: str, today: str) -> int:
        rows = conn.execute(
            "SELECT first_seen FROM recite_word_progress"
            " WHERE user = ? AND first_seen IS NOT NULL",
            (user,),
        ).fetchall()
        return sum(1 for r in rows if _cn_day_of(r["first_seen"]) == today)

    @staticmethod
    def _unseen_words(conn: sqlite3.Connection, user: str, details: Optional[list]) -> list[str]:
        """本组里真正的首见新词：进度表没有、错词周期里也没有。"""
        fresh: list[str] = []
        seen: set[str] = set()
        for d in details or []:
            if not isinstance(d, dict):
                continue
            w = str(d.get("word", "")).strip().lower()[:60]
            if not w or w in seen:
                continue
            seen.add(w)
            row = conn.execute(
                "SELECT 1 FROM recite_word_progress WHERE user = ? AND word = ?",
                (user, w),
            ).fetchone()
            if row is not None:
                continue
            cyc = conn.execute(
                "SELECT 1 FROM recite_review WHERE user = ? AND word = ?",
                (user, w),
            ).fetchone()
            if cyc is None:
                fresh.append(w)
        return fresh

    @staticmethod
    def _recent_activity(conn: sqlite3.Connection, user: str) -> list[dict]:
        """最近错词本/攻克会话 + 最近触碰的周期词（各最多 8 条）。"""
        out: list[dict] = []
        sessions = conn.execute(
            "SELECT id, total, wrong, acc, mode, scope, created_at"
            " FROM recite_sessions WHERE user = ? AND (scope = ? OR mode = ?)"
            " ORDER BY id DESC LIMIT 8",
            (user, REVIEW_SCOPE, CONQUER_MODE),
        ).fetchall()
        for r in sessions:
            out.append({
                "kind": "session",
                "id": r["id"],
                "total": r["total"],
                "wrong": r["wrong"],
                "acc": r["acc"],
                "mode": r["mode"],
                "scope": r["scope"],
                "at": r["created_at"],
                "day": _cn_day_of(r["created_at"] or ""),
            })
        words = conn.execute(
            "SELECT word, stage, first_wrong_at, last_review_at, conquered_at"
            " FROM recite_review WHERE user = ?"
            " ORDER BY COALESCE(conquered_at, last_review_at, first_wrong_at) DESC,"
            " word LIMIT 8",
            (user,),
        ).fetchall()
        for r in words:
            if r["conquered_at"]:
                event, at = "conquered", r["conquered_at"]
            elif (r["stage"] or 0) > 0:
                event, at = "reviewed", r["last_review_at"] or r["first_wrong_at"]
            else:
                event, at = "entered", r["first_wrong_at"] or r["last_review_at"]
            out.append({
                "kind": "word",
                "word": r["word"],
                "event": event,
                "stage": r["stage"],
                "at": _cn_day_of(at or ""),
            })
        return out

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

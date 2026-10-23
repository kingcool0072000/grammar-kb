"""当日自动完成信号（成绩、背词、朗读、专题）。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from ..clock import cn_date, cn_dt, monday_of
from ..topics import node_keys_from_content


class SignalsMixin:

    def _topic_done_lectures(self, lo: str, hi: str, user: str) -> set[int]:
        """[lo, hi) 内学完的自学专题对应讲次号（topic_id 形如
        lecture-29-passive-1；完成=done_keys 覆盖全部节点 key）。
        讲次「学+测」的完成信号除成绩登记外补此口径——孩子在专题里
        学完并做完题，但没有线下卷面成绩时，周计划也应点亮。"""
        try:
            with self._connect() as conn:
                tabs = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "topic_progress" not in tabs or "topic_content" not in tabs:
                    return set()
                rows = conn.execute(
                    "SELECT topic_id, done_keys FROM topic_progress"
                    " WHERE user = ? AND completed_at != ''"
                    " AND date(julianday(COALESCE(NULLIF(completed_at,''),"
                    " updated_at)) + 8.0/24) >= date(?)"
                    " AND date(julianday(COALESCE(NULLIF(completed_at,''),"
                    " updated_at)) + 8.0/24) < date(?)",
                    (user, lo, hi),
                ).fetchall()
                out: set[int] = set()
                for r in rows:
                    m = re.match(r"lecture-(\d+)", r["topic_id"] or "")
                    if not m:
                        continue
                    try:
                        done_set = set(json.loads(r["done_keys"] or "[]"))
                    except (ValueError, TypeError):
                        continue
                    need = self._topic_node_keys(conn, r["topic_id"])
                    if need and done_set >= need:
                        out.add(int(m.group(1)))
                return out
        except sqlite3.Error:
            return set()


    def _topic_node_keys(self, conn: sqlite3.Connection,
                         topic_id: str) -> set[str]:
        """专题内容全部节点 key 集合（内容缺失/坏 JSON 返回空集=不可判定）。"""
        try:
            row = conn.execute(
                "SELECT content FROM topic_content WHERE topic_id = ?",
                (topic_id,)).fetchone()
            if not row:
                return set()
            return node_keys_from_content(row["content"])
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            return set()


    def _auto_done(self, user: str, day: str) -> tuple[dict[str, bool], list[str]]:
        """当天活动信号 → (task_key 完成表, 当日阅读书名列表)。

        各表缺表/缺库均容错返回空。阅读书名列表供 today_tasks 按书模糊
        匹配（focus_sessions.book_title）。
        """
        out: dict[str, bool] = {}
        read_titles: list[str] = []
        self._day_acts = {"speak_articles": [], "readings": [],
                          "vocab_n": 0, "vocab_min": 0, "focus_sec": 0,
                          "recite_sec": 0, "study_min": 0}
        nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        # 成绩与专题统一走 lecture_done 内核（含 user 过滤与规则开关）
        for lec_no in self.lecture_done(day, nxt, user, "lecture"):
            out[f"lecture:{lec_no}"] = True
            out["lecture"] = True
        for lec_no in self.lecture_done(day, nxt, user, "hw"):
            out[f"hw:{lec_no}"] = True
            out.setdefault("lecture", True)
        # 微练习：当天有该讲成绩（任意 kind）即点亮（沿用成绩口径，带 user）
        exam_path = self._exam_db()
        if exam_path and Path(exam_path).exists():
            try:
                with sqlite3.connect(f"file:{exam_path}?mode=ro", uri=True) as conn:
                    cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(exam_records)")}
                    sql = "SELECT DISTINCT lecture FROM exam_records WHERE date = ?"
                    args: list = [day]
                    if "user" in cols and self._flag(user, "exam_user_scope"):
                        sql += " AND user = ?"
                        args.append(user)
                    for r in conn.execute(sql, args):
                        out[f"micro:{r[0]}"] = True
            except sqlite3.Error:
                pass
        try:
            with self._connect() as conn:
                tabs = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "recite_word_progress" in tabs:
                    # mastered_at 是 UTC ISO。和背词周窗口同一北京日切，
                    # 否则北京时间凌晨新掌握的词不会点亮当天的错词信号。
                    mastered_day = cn_date("mastered_at")
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND {mastered_day} >= date(?)"
                        f" AND {mastered_day} < date(?)",
                        (user, day, nxt),
                    ).fetchone()
                    if row and row["n"]:
                        out["wrong_words"] = True
                if "recite_sessions" in tabs:
                    row = conn.execute(
                        "SELECT COUNT(*) n, COALESCE(SUM(total),0) w,"
                        " COALESCE(SUM(duration_sec),0) dur"
                        " FROM recite_sessions"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchone()
                    if row and row["n"]:
                        out.setdefault("wrong_words", True)
                        self._day_acts["vocab_n"] = row["w"]
                        self._day_acts["vocab_min"] = round(row["dur"] / 60)
                        self._day_acts["recite_sec"] = row["dur"]
                if "focus_sessions" in tabs:
                    # 每日有效学习时长：泛读/精读/专题专注会话的 active_sec
                    # 全量求和（不含离开时间，与批改中心学习日志口径一致）。
                    # 泛读僵尸会话（开着书页无任何阅读证据）不计——曾把
                    # 0%→0% 挂机 27 分钟算进「有效学习」
                    frows = conn.execute(
                        "SELECT module, book_id, lookups, plays, scroll_count,"
                        " chapter_navs, percent_start, percent_end,"
                        " percent_track, active_sec"
                        " FROM focus_sessions"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchall()
                    if frows:
                        self._day_acts["focus_sec"] = sum(
                            r["active_sec"] or 0 for r in frows
                            if not self._zombie_reading(r))
                if "fce_submission" in tabs:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM fce_submission"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchone()
                    if row and row["n"]:
                        out["fce"] = True
                if "vocab_exams" in tabs:
                    # 试卷任务完成：当天有该学生的考试成绩登记（paper_id
                    # 精确匹配；登记记录没有 paper_id 时按「当天有本级
                    # 考试」兜底——线下考完卷面即回收，级别口径够用）。
                    vex_cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(vocab_exams)")}
                    has_pid = "paper_id" in vex_cols
                    rows = conn.execute(
                        ("SELECT level, paper_id FROM vocab_exams"
                         " WHERE user = ? AND exam_date = ?") if has_pid else
                        ("SELECT level, '' AS paper_id FROM vocab_exams"
                         " WHERE user = ? AND exam_date = ?"),
                        (user, day),
                    ).fetchall()
                    plv_rows = conn.execute(
                        "SELECT paper_id, level FROM vocab_paper_history"
                    ).fetchall() if "vocab_paper_history" in tabs else []
                    lv_by_pid = {r["paper_id"]: r["level"] for r in plv_rows}
                    # 本周计划里的试卷（删档卷也包含——级别从 L{n}- 前缀解析）
                    ws = monday_of(date.fromisoformat(day)).isoformat()
                    planned_papers = list((self._get_week(ws, user=user)
                                           .get("tasks") or {}).get("vocab_papers")
                                          or [])
                    for r in rows:
                        pid = r["paper_id"] or ""
                        if pid:
                            out[f"paper:{pid}"] = True
                        # 同级兜底：当天考过该级，视为该级试卷任务完成
                        lv_match = {p2 for p2, lv2 in lv_by_pid.items()
                                    if lv2 == r["level"]}
                        lv_match |= {p2 for p2 in planned_papers
                                     if self._pid_level(p2) == r["level"]}
                        for p2 in lv_match:
                            out[f"paper:{p2}"] = True
                if "reading_recordings" in tabs:
                    rows = conn.execute(
                        "SELECT article_id FROM reading_recordings"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchall()
                    if rows:
                        out["speak"] = True
                        for r in rows:
                            if r["article_id"]:
                                out[f"article:{r['article_id']}"] = True
                                self._day_acts["speak_articles"].append(r["article_id"])
                if "focus_sessions" in tabs:
                    rows = conn.execute(
                        "SELECT module, book_title, book_id, active_sec,"
                        " percent_start, percent_end, lookups, plays,"
                        " scroll_count, chapter_navs, percent_track"
                        " FROM focus_sessions"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)"
                        " AND book_title IS NOT NULL",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchall()
                    read_titles = [r["book_title"] for r in rows]
                    for r in rows:
                        out[f"reading:{(r['book_title'] or '')[:14]}"] = True
                        out.setdefault("reading", True)
                    # 泛读（library 模块）实录：逐书章区间+词数+分钟（见 _reading_day_detail）
                    lib_rows = [r for r in rows if r["module"] == "library"]
                    if lib_rows:
                        self._day_acts["readings"] = self._reading_day_detail(lib_rows)
        except sqlite3.Error:
            pass
        # 每日有效学习总时长（分钟）= 专注会话有效秒数 + 背词练习秒数
        self._day_acts["study_min"] = round(
            (self._day_acts.get("focus_sec", 0)
             + self._day_acts.get("recite_sec", 0)) / 60)
        return out, read_titles

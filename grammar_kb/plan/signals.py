"""当日自动完成信号（成绩、背词、朗读、专题）。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

from ..clock import cn_dt, monday_of
from ..topics import node_keys_from_content
from .ctx import WeekCtx


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

        兼容旧二元组出口（测试/外部直调）。实录 acts 走
        :meth:`_auto_done_full`，不再挂到 Store 单例上。
        """
        auto, read_titles, _acts = self._auto_done_full(user, day)
        return auto, read_titles


    def _auto_done_full(self, user: str, day: str,
                        ctx: WeekCtx | None = None) -> tuple[dict, list, dict]:
        """当天活动信号 → (task_key 完成表, 阅读书名列表, 当日实录 acts)。

        acts 原先写在 ``self._day_acts``（PlanStore 单例字段）——FastAPI
        线程池并发处理两个学生时互相覆盖当日实录。现改为随调用链局部
        返回，不引入任何共享可变状态。

        连接拓扑（单趟）：
        - 讲次成绩：exam.db 一个连接（ctx 路径整周预取，单日直调
          时 ctx 只捞当天窗口）；
        - 背词/专注/朗读/FCE/试卷：fce.db（plan 主库）一个连接一趟查完
          ——focus_sessions 原「时长一遍 + 书名一遍」合并为一趟，SELECT
          只取所需列（不碰 polyline/activity_json 等大列）；
        - 单词试卷只点亮登记里写明、且仍在存档里的那一份。没有试卷编号、
          编号对不上存档、或同级另一份卷，都不算完成。

        各表缺表/缺库均容错返回空。阅读书名列表供 today_tasks 按书模糊
        匹配（focus_sessions.book_title）。
        """
        out: dict[str, bool] = {}
        read_titles: list[str] = []
        acts: dict = {"speak_articles": [], "readings": [],
                      "vocab_n": 0, "vocab_min": 0, "focus_sec": 0,
                      "recite_sec": 0, "study_min": 0}
        nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        if ctx is None:
            ctx = WeekCtx(self, user, monday_of(date.fromisoformat(day)).isoformat())
        # 成绩与专题统一走 lecture_done 内核（含 user 过滤与规则开关）
        for lec_no in ctx.lecture_done(day, nxt, "lecture"):
            out[f"lecture:{lec_no}"] = True
            out["lecture"] = True
        for lec_no in ctx.lecture_done(day, nxt, "hw"):
            out[f"hw:{lec_no}"] = True
            out.setdefault("lecture", True)
        try:
            with self._connect() as conn:
                tabs = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "micro_daily" in tabs:
                    # 微练习（线上功能）：当天该生整组做完（done=1）才点亮
                    # 「micro」信号。部分作答不算完成。微练习不参与完成率，
                    # 只做当日点亮展示。表由微练习模块建，缺表容错。
                    row = conn.execute(
                        "SELECT done FROM micro_daily"
                        " WHERE user = ? AND date = ?", (user, day),
                    ).fetchone()
                    if row and row["done"]:
                        out["micro"] = True
                if "recite_review" in tabs:
                    # 周日「错词再练」：复习了当时到期/逾期的词，或当天攻克了词。
                    # 普通背词会话、当天新掌握，都不点亮这一格。
                    rr_cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(recite_review)")}
                    parts = []
                    args: list = [user]
                    if "due_touch" in rr_cols:
                        parts.append("due_touch = ?")
                        args.append(day)
                    if "conquered_at" in rr_cols:
                        parts.append("conquered_at = ?")
                        args.append(day)
                    if parts:
                        row = conn.execute(
                            "SELECT 1 FROM recite_review WHERE user = ? AND ("
                            + " OR ".join(parts) + ") LIMIT 1",
                            args,
                        ).fetchone()
                        if row:
                            out["wrong_words"] = True
                if "recite_sessions" in tabs:
                    # 会话词数只进学习时长实录，不拿来给「背词 n 词」或周日错词打勾。
                    row = conn.execute(
                        "SELECT COUNT(*) n, COALESCE(SUM(total),0) w,"
                        " COALESCE(SUM(duration_sec),0) dur"
                        " FROM recite_sessions"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchone()
                    if row and row["n"]:
                        acts["vocab_n"] = row["w"]
                        acts["vocab_min"] = round(row["dur"] / 60)
                        acts["recite_sec"] = row["dur"]
                if "focus_sessions" in tabs:
                    # 一趟取回当日全部会话（列裁剪：不含 polyline/
                    # activity_json 等大列）。时长口径看全量行，泛读实录
                    # 只看 book_title 非空的行——与原两趟查询结果一致。
                    # 每日有效学习时长：泛读/精读/专题专注会话的 active_sec
                    # 全量求和（不含离开时间，与批改中心学习日志口径一致）。
                    # 泛读僵尸会话（开着书页无任何阅读证据）不计——曾把
                    # 0%→0% 挂机 27 分钟算进「有效学习」
                    frows = conn.execute(
                        "SELECT module, book_title, book_id, lookups, plays,"
                        " scroll_count, chapter_navs, percent_start,"
                        " percent_end, percent_track, active_sec"
                        " FROM focus_sessions"
                        f" WHERE user = ? AND ({cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?)",
                        (user, f"{day} 00:00:00", f"{nxt} 00:00:00"),
                    ).fetchall()
                    if frows:
                        acts["focus_sec"] = sum(
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
                    # 只点亮「已存档且绑了这份试卷编号」的登记。
                    # 空编号、编号不在存档里、同级另一份卷，都不连坐。
                    vex_cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(vocab_exams)")}
                    if "paper_id" not in vex_cols:
                        rows = []
                    else:
                        rows = conn.execute(
                            "SELECT paper_id FROM vocab_exams"
                            " WHERE user = ? AND exam_date = ?"
                            " AND paper_id IS NOT NULL AND paper_id != ''",
                            (user, day),
                        ).fetchall()
                    pids = [str(r["paper_id"]) for r in rows if r["paper_id"]]
                    archived: set[str] = set()
                    if pids and "vocab_paper_history" in tabs:
                        marks = ",".join("?" * len(pids))
                        archived = {
                            r["paper_id"] for r in conn.execute(
                                "SELECT paper_id FROM vocab_paper_history"
                                f" WHERE paper_id IN ({marks})",
                                pids,
                            )
                        }
                    for pid in pids:
                        if pid in archived:
                            out[f"paper:{pid}"] = True
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
                                acts["speak_articles"].append(r["article_id"])
                if "focus_sessions" in tabs:
                    brows = [r for r in frows
                             if r["book_title"] is not None]
                    read_titles = [r["book_title"] for r in brows]
                    for r in brows:
                        out[f"reading:{(r['book_title'] or '')[:14]}"] = True
                        out.setdefault("reading", True)
                    # 泛读（library 模块）实录：逐书章区间+词数+分钟（见 _reading_day_detail）
                    lib_rows = [r for r in brows if r["module"] == "library"]
                    if lib_rows:
                        acts["readings"] = self._reading_day_detail(lib_rows)
        except sqlite3.Error:
            pass
        # 每日有效学习总时长（分钟）= 专注会话有效秒数 + 背词练习秒数
        acts["study_min"] = round(
            (acts.get("focus_sec", 0) + acts.get("recite_sec", 0)) / 60)
        # 视频复述：当天提交过录音即点亮 video:{vid}（待批也算完成）
        for vid in ctx.retold_video_ids(day, nxt):
            out[f"video:{vid}"] = True
        return out, read_titles, acts

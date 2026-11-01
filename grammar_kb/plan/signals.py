"""当日自动完成信号（成绩、背词、朗读、专题）。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

from ..clock import cn_date, cn_dt, monday_of
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
        - 讲次/micro 成绩：exam.db 一个连接（ctx 路径整周预取，单日直调
          时 ctx 只捞当天窗口），micro 与 lecture_done 共用同一批行；
        - 背词/专注/朗读/FCE/试卷：fce.db（plan 主库）一个连接一趟查完
          ——focus_sessions 原「时长一遍 + 书名一遍」合并为一趟，SELECT
          只取所需列（不碰 polyline/activity_json 等大列）；
        - vocab_paper_history 加周窗口边界，不再全表扫描。试卷级别兜底
          只影响「计划外试卷键」的内部 signals（不进任何响应 JSON）；
          计划内试卷仍由 paper_id 精确匹配 + L{n}- 前缀解析点亮，判定
          输出与全表扫描版本一致。

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
        # 微练习：当天有该讲成绩（任意 kind）即点亮（沿用成绩口径）。
        # 与 lecture_done 同一批 exam_records 行（date 精确相等；规则开关
        # exam_user_scope 的过滤条件也一致）。
        if self._exam_db():
            for lec_no in ctx.exam_lectures_on(day):
                out[f"micro:{lec_no}"] = True
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
                    ws = monday_of(date.fromisoformat(day)).isoformat()
                    # 本周计划里的试卷（删档卷也包含——级别从 L{n}- 前缀解析）
                    planned_papers = list((ctx.week_tasks(ws).get("tasks")
                                           or {}).get("vocab_papers") or [])
                    lv_by_pid: dict = {}
                    if "vocab_paper_history" in tabs and rows:
                        # 原来全表扫描（words 大列随表增长越捞越贵）。收敛为
                        # 考试所在周的生成窗口 [ws, nxt)：计划内试卷的级别
                        # 兜底另有 L{n}- 前缀解析覆盖，判定输出不变。
                        lv_by_pid = {r["paper_id"]: r["level"] for r in
                                     conn.execute(
                                         "SELECT paper_id, level FROM"
                                         " vocab_paper_history"
                                         " WHERE created_at >= ?"
                                         " AND created_at < ?",
                                         (ws, nxt)).fetchall()}
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

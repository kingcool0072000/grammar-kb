"""周历、月历、总览。完成率走学习信号；打卡表不进 done。"""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, datetime, timedelta, timezone as _tz

from ..clock import cn_dt, monday_of, today_cn
from .util import (
    _paper_short, _short, add_days, completion_percent, task_public,
)

_COMPLETION_NOTE = (
    "完成率按学习信号自动计算，不看手动打卡。"
    "仅教师冻结过的日期改用 plan_history_days 快照，那不是每日流水。"
)
class ViewsMixin:

    def week_view(self, week_start: str, user: str = "malin") -> dict:
        """教师周视图：周拆解汇总 + 周一至周日逐日任务（含完成态）。

        周拆解（用户要的「自动拆解」）从 tasks 推导：
        - 阅读篇数（reading 目标书数）
        - 泛读总词数（各书目标% 对应词数 − 已读词数，按剩余摊到天）
        - 题型类数（微练习讲数 + FCE 项数 + 讲次测验，去重类目）
        已到的日子一律 ``today_tasks``（信号；有快照才改走回填）。
        没有计划项时 percent 为 null，不显示成 0%。
        """
        w = self._get_week(week_start, user=user)
        t = w.get("tasks") or {}
        today_iso = today_cn().isoformat()
        in_week = week_start <= today_iso <= add_days(week_start, 6)
        days_left = max(1, (date.fromisoformat(add_days(week_start, 6))
                            - date.fromisoformat(today_iso)).days + 1) if in_week else 7

        # ---- 周拆解 ----
        reading_items = []
        for book_key, goal_val in (t.get("reading") or {}).items():
            row = self._match_book(book_key, user)
            # 章选格式（list）不做全书百分比状态（_book_reading_state 只吃
            # float——此前 list 直接传入会 500，周计划选章后周历/课程表必炸）
            st = (self._book_reading_state(row["id"], float(goal_val), user)
                  if row and not isinstance(goal_val, list) else None)
            # 章选格式补展示章区间（「第6–7章」）——学生课程表摘要直接引用
            chapter_range = None
            if isinstance(goal_val, list) and row:
                nos = self._chapter_nos(row["id"], goal_val)
                if len(nos) > 1:
                    chapter_range = f"第{nos[0]}–{nos[-1]}章"
                elif nos:
                    chapter_range = f"第{nos[0]}章"
            title = (_short(row["title"], 14) if row else _short(str(book_key), 14))
            reading_items.append({
                "book": book_key, "title": title,
                "chapter_range": chapter_range,
                "goal_pct": goal_val if not isinstance(goal_val, list) else None,
                "percent": st["percent"] if st else None,
                "goal_percent": st["goal_percent"] if st else None,
                "goal_words": st["goal_words"] if st else None,
                "configured_chapters": st["configured_chapters"] if st else None,
                "remaining_words": st["remaining_words"] if st else None,
                "goal_chapter": st["goal_chapter"] if st else None,
                "current_chapter": st["current_chapter"] if st else None,
            })
        rem_total = sum(x["remaining_words"] or 0 for x in reading_items)
        drill_types = set()
        if t.get("focus_kps"):
            drill_types.add("微练习")
        if t.get("lectures"):
            drill_types.add("讲次测验")
        if t.get("hw_papers"):
            drill_types.add("作业卷")
        if t.get("fce"):
            drill_types.add("FCE 真题")
        if t.get("videos"):
            drill_types.add("视频复述")
        breakdown = {
            "reading_books": len(reading_items),
            "reading_items": reading_items,
            "reading_words_total": rem_total,
            "reading_words_per_day": round(rem_total / days_left) if rem_total else 0,
            "drill_types": sorted(drill_types),
            "drill_count": len(t.get("focus_kps") or []) * 5 * days_left
                           if t.get("focus_kps") else 0,
            "lecture_count": len(t.get("lectures") or []),
            "hw_paper_count": len(t.get("hw_papers") or []),
            "fce_count": len(t.get("fce") or []),
            "video_count": len(t.get("videos") or []),
        }

        # ---- 逐日矩阵 ----
        # 过去的周不再因为没有 plan_history_days 就变成 0/0。
        # 快照只在教师冻过的那一天替代信号（today_tasks 内部处理）。
        days = []
        for i in range(7):
            d = (date.fromisoformat(week_start) + timedelta(days=i)).isoformat()
            if d > today_iso:
                # 未来日带出均摊后的当日计划（done=False 灰显），教师/学生
                # 都能提前看到「那天该做什么」；total 即当日任务数。
                # 未来日不进完成率，percent 保持 null，避免空打卡被看成 0%。
                fut = self.week_daily_tasks(week_start, for_date=d, user=user)
                days.append({
                    "date": d, "future": True, "done": 0, "total": len(fut),
                    "percent": None, "basis": "signals",
                    "tasks": [task_public(x, future=True) for x in fut],
                })
                continue
            td = self.today_tasks(user=user, day=d)
            days.append({
                "date": d, "future": False,
                "done": td["done"], "total": td["total"],
                "percent": td["percent"], "basis": td["basis"],
                "tasks": [task_public(x) for x in td["tasks"]],
                "acts": td.get("acts") or {},
            })
        past = [d for d in days if not d["future"]]
        done_n = sum(d["done"] for d in past)
        total_n = sum(d["total"] for d in past)
        bases = {d.get("basis") for d in past if d.get("total")}
        if bases == {"history"}:
            basis = "history"
        elif "history" in bases:
            basis = "mixed"
        else:
            basis = "signals"
        return {
            "week_start": week_start, "tasks": t, "notes": w.get("notes") or "",
            "breakdown": breakdown, "days": days, "current_week": in_week,
            # 精读任务补标题（学生课程表摘要显示文章名，不止「N 篇」）
            "article_titles": [
                {"id": aid, "title": self._article_title(aid)}
                for aid in (t.get("articles") or [])
            ],
            # 视频任务补标题（学生课程表/计划卡显示片名；已删除的为 null）
            "video_titles": [
                {"vid": v, "title": self._video_title_any(v)}
                for v in (t.get("videos") or [])
            ],
            "goal_progress": self._week_goal_progress(t, days, user, week_start),
            "completion": {
                "done": done_n, "total": total_n,
                "percent": completion_percent(done_n, total_n),
                "basis": basis,
                "note": _COMPLETION_NOTE,
            },
        }


    def _week_goal_progress(self, t: dict, days: list, user: str,
                            week_start: str) -> dict:
        """周目标的各维度目标+进度（与总目标 goal_progress 的 dims 同构，
        供前端同一张「目标+进度」卡切换展示）。

        维度口径（按周任务配置）：
        - 讲次：计划讲次数 vs 本周考过的讲次数
        - 词汇：vocab_goal（级别百分比折算的累计词数）vs 周内新掌握词数
        - FCE：计划 Part 数 vs 周内提交覆盖数
        - 泛读：按各书周目标%（词数口径）vs 周内已读词数（实录汇总）
        - 精读：计划文章数 vs 已提交录音数
        - 试卷：计划卷数 vs 登记考试数
        """
        dims = []

        def dim(icon, name, ok, total, extra=""):
            if total:
                dims.append({"icon": icon, "name": name,
                             "done": round(ok, 1), "total": total, "extra": extra})

        lecs = t.get("lectures") or []
        if lecs:
            done_lec = len(set(lecs) & self.lecture_done(
                week_start, add_days(week_start, 7), user, "lecture"))
            dim("📚", "语法课程", done_lec, len(lecs))

        hw_papers = t.get("hw_papers") or []
        if hw_papers:
            done_hw = len(set(hw_papers) & self.lecture_done(
                week_start, add_days(week_start, 7), user, "hw"))
            dim("📝", "作业卷", done_hw, len(hw_papers))

        # 词汇差额口径（级别内增量）：分母 = 目标词数 − 周初该级别已掌握
        # 词数；分子 = 周内该级别新掌握词数。目标本身就是「L几 内掌握
        # N 词」，周初已超额的显示满格。
        vg = t.get("vocab_goal")
        if vg:
            lv_words = self._vocab_level_words(t.get("vocab_lv"))
            gained = self._vocab_actuals(week_start, add_days(week_start, 7), user, lv_words)
            base = self._vocab_mastered_before(week_start, user, lv_words)
            if base >= vg:
                dim("🔤", "词汇", vg, vg, extra="周初已达成")
            else:
                denom = vg - base
                dim("🔤", "词汇", min(gained, denom), denom)

        fce = t.get("fce") or []
        if fce:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT test_id, paper, part FROM fce_submission"
                        f" WHERE user = ? AND {cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?",
                        (user, f"{week_start} 00:00:00",
                         f"{add_days(week_start, 7)} 00:00:00")).fetchall()
                    got = {f"Test {r['test_id']} · {_paper_short(r['paper'])} P{r['part']}"
                           for r in rows}
                done_fce = sum(1 for x in fce if x in got)
            except sqlite3.Error:
                done_fce = 0
            dim("🎧", "FCE真题", done_fce, len(fce))

        reading_map = t.get("reading") or {}
        if reading_map:
            # 泛读周目标=「目标章节完成百分比」（用户口径）：
            # 目标=选章序列（按书内顺序）；当前读位（云端进度词位）在
            # 序列内的均摊位置 / 序列总长。例：目标第5章，读到第5章5%
            # → 5%；目标第5-8章，读到第5章50% → (0.5章)/4章=12.5%。
            # 旧 pct 格式换算为等效章序列（全书前 N% 覆盖的章）。
            lib = self._lib_connect()
            done_pct = 0.0
            goal_pct_sum = 0.0   # 各书完成度 × 该书章数权重（多书均摊）
            goal_ch_count = 0
            try:
                if lib is not None:
                    with contextlib.closing(lib):
                        for k, v in reading_map.items():
                            if not str(k).isdigit():
                                continue
                            bid = int(k)
                            chs = lib.execute(
                                "SELECT idx, word_count FROM chapters"
                                " WHERE book_id = ? ORDER BY idx", (bid,)
                            ).fetchall()
                            total = sum(c["word_count"] or 0 for c in chs)
                            if not chs or total <= 0:
                                continue
                            if isinstance(v, list) and v:
                                sel = [c for c in chs if c["idx"] in
                                       {int(x) for x in v}]
                            else:
                                # 旧 pct：全书前 N% 覆盖的章序列
                                cut = (float(v) if not isinstance(v, list)
                                       else 100.0) / 100 * total
                                cum = 0
                                sel = []
                                for c in chs:
                                    cum += c["word_count"] or 0
                                    if cum <= cut:
                                        sel.append(c)
                                    elif (cum - (c["word_count"] or 0)) < cut:
                                        sel.append(c)  # 边界章算入
                            if not sel:
                                continue
                            prog = lib.execute(
                                "SELECT percent FROM reading_progress"
                                " WHERE book_id = ? AND user = ?",
                                (bid, user)).fetchone()
                            pos = ((prog[0] if prog else 0) or 0) / 100 * total
                            # 当前读位在选章序列内的均摊章进度：
                            # 读完的整章各计 1，当前章按章内比例计
                            prog_ch = 0.0
                            cum = 0
                            for c in chs:
                                c1 = cum + (c["word_count"] or 0)
                                if c in sel:
                                    if pos >= c1:
                                        prog_ch += 1.0
                                    elif pos > cum:
                                        prog_ch += (pos - cum) / max(1, c["word_count"] or 1)
                                cum = c1
                            done_pct += prog_ch
                            goal_pct_sum += len(sel)
                            goal_ch_count += len(sel)
            except (sqlite3.Error, TypeError, ValueError):
                pass
            if goal_ch_count:
                done100 = done_pct / goal_ch_count * 100
                dim("📖", "泛读", round(min(done100, 999), 1), 100)

        arts = t.get("articles") or []
        # 周内全部朗读提交（含计划外）：精读朗读 = 提交过录音才算完成。
        # 完成行的 article_id 可能缺字段（快照/占位行只有 key="article:N"），
        # 从 key 解析兜底。
        week_recorded = []
        for d in days:
            for x in (d.get("tasks") or []):
                if x.get("type") == "article" and x.get("done"):
                    aid = x.get("article_id")
                    if aid is None:
                        k = str(x.get("key") or "")
                        if k.startswith("article:") and k[8:].isdigit():
                            aid = int(k[8:])
                    if aid is not None:
                        week_recorded.append(aid)
            for x in (d.get("acts") or {}).get("extra_speaks", []):
                week_recorded.append(-1)  # 计划外朗读（无任务行）
        if arts:
            done_a = sum(1 for a in arts if a in week_recorded)
            extra_n = sum(1 for x in week_recorded if x == -1)
            dim("🎤", "精读朗读", done_a, len(arts),
                extra=f"计划外另朗 {extra_n} 篇" if extra_n else "")
        elif week_recorded:
            dim("🎤", "精读朗读", len(week_recorded), len(week_recorded),
                extra="均为计划外朗读")

        papers = t.get("vocab_papers") or []
        if papers:
            done_p = sum(
                1 for d in days
                for x in (d.get("tasks") or [])
                if x.get("type") == "paper" and x.get("done"))
            dim("📝", "试卷", done_p, len(papers))

        videos = [str(v).strip() for v in (t.get("videos") or []) if str(v).strip()]
        if videos:
            done_ids = self._retold_video_ids(
                week_start, add_days(week_start, 7), user)
            dim("🎬", "视频复述", len(set(videos) & done_ids), len(videos))

        total_items = sum(d["total"] for d in dims)
        # 泛读维度按实际计数（可超 100%）；其余维度封顶（完成即满）
        done_items = sum(
            d["done"] if d["name"] == "泛读" else min(d["done"], d["total"])
            for d in dims)
        pct = round(done_items / total_items * 100) if total_items else None
        return {"dims": dims, "percent": pct,
                "done_items": round(done_items), "total_items": total_items,
                "week_start": week_start}


    def month_view(self, month: str, user: str = "malin") -> dict:
        """月历：某月每天 {date, in_month, future, done, total}，周一为每周之首。"""
        y, m = month.split("-")[:2]
        first = date(int(y), int(m), 1)
        nxt_month = (date(int(y) + 1, 1, 1) if int(m) == 12
                     else date(int(y), int(m) + 1, 1))
        today_iso = today_cn().isoformat()
        grid_start = monday_of(first)
        days = []
        d = grid_start
        while d < nxt_month:
            iso_ = d.isoformat()
            cell = {"date": iso_, "in_month": d.month == first.month,
                    "future": iso_ > today_iso}
            if iso_ > today_iso:
                td = self.week_daily_tasks(
                    monday_of(d).isoformat(), for_date=iso_, user=user)
                cell.update({"done": 0, "total": len(td), "percent": None})
            else:
                td = self.today_tasks(user=user, day=iso_)
                cell.update({
                    "done": td["done"], "total": td["total"],
                    "percent": td["percent"],
                })
            days.append(cell)
            d += timedelta(days=1)
        return {"month": first.isoformat()[:7], "days": days}


    def overview(self, user: str = "malin") -> dict:
        """全览：最早已排计划周（无则回看 2 周）到冲刺线 2027-01-31 的逐周汇总。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT week_start FROM plan_weeks WHERE user = ?"
                " ORDER BY week_start", (user,),
            ).fetchall()
        seeded = [r["week_start"] for r in rows if r["week_start"] <= "2027-01-25"]
        today_iso = today_cn().isoformat()
        cur = monday_of(today_cn()).isoformat()
        earliest = (seeded[0] if seeded
                    else (date.fromisoformat(cur) - timedelta(days=14)).isoformat())
        weeks = []
        ws = earliest
        while ws <= "2027-01-25":
            w = self._get_week(ws, user=user)
            t = w.get("tasks") or {}
            has_plan = bool(t.get("focus_kps") or t.get("lectures")
                            or t.get("hw_papers") or t.get("vocab_goal")
                            or t.get("fce") or t.get("reading")
                            or t.get("videos"))
            done = total = 0
            bases: set[str] = set()
            if ws <= cur:  # 已开始的周才聚合完成度（未来周无信号）
                for i in range(7):
                    d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
                    if d > today_iso:
                        continue
                    # 过去的周也走信号。没有回填快照时不再把整周算成 0/0。
                    td = self.today_tasks(user=user, day=d)
                    done += td["done"]
                    total += td["total"]
                    if td["total"]:
                        bases.add(td["basis"])
            if bases == {"history"}:
                basis = "history"
            elif "history" in bases:
                basis = "mixed"
            else:
                basis = "signals"
            weeks.append({
                "week_start": ws, "has_plan": has_plan,
                "is_current": ws == cur, "past": ws < cur,
                "focus_kps": t.get("focus_kps") or [],
                "lectures": t.get("lectures") or [],
                "hw_papers": t.get("hw_papers") or [],
                "vocab_lv": t.get("vocab_lv"),
                "vocab_pct": t.get("vocab_pct"),
                "vocab_goal": t.get("vocab_goal"),
                "reading": t.get("reading") or {},
                "videos": t.get("videos") or [],
                "notes": w.get("notes") or "",
                "done": done, "total": total,
                "percent": completion_percent(done, total) if ws <= cur else None,
                "basis": basis,
            })
            ws = (date.fromisoformat(ws) + timedelta(days=7)).isoformat()
        return {"weeks": weeks, "current_week": cur}


    def put_day_check(self, user: str, day: str, task_key: str, on: bool) -> None:
        date.fromisoformat(day)  # 非法日期抛 ValueError → 422
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            if on:
                conn.execute(
                    "INSERT OR REPLACE INTO plan_day_checks"
                    " (user, date, task_key, checked_at) VALUES (?,?,?,?)",
                    (user, day, task_key[:80], now),
                )
            else:
                conn.execute(
                    "DELETE FROM plan_day_checks"
                    " WHERE user = ? AND date = ? AND task_key = ?",
                    (user, day, task_key),
                )


    def _day_checks(self, user: str, day: str) -> set[str]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT task_key FROM plan_day_checks"
                    " WHERE user = ? AND date = ?", (user, day),
                ).fetchall()
            return {r["task_key"] for r in rows}
        except sqlite3.Error:
            return set()

"""周历、总览。完成率走学习信号，不按手动打卡计。"""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, timedelta

from ..clock import monday_of, today_cn
from .ctx import WeekCtx
from .util import (
    _short, _task_key, add_days, completion_percent, task_public,
    vocab_progress_detail,
)
from .week_credit import (
    build_week_pool, checkin_progress, focus_percent_before, micro_checkins,
    reading_moved_after,
)

_COMPLETION_NOTE = (
    "完成率按学习信号自动计算，不看手动打卡。"
    "除微练习外，作业按周计：这一周周一到周日任何一天做完都算。"
    "过了周日夜里（北京时间下周一 0 点），这一周的进度就定了，之后再做记在做的那一周。"
    "微练习是当天打卡（周一到周日各一次），过了北京时间当天不能补。"
    "仅教师冻结过的日期改用 plan_history_days 快照，那不是每日流水。"
)
class ViewsMixin:

    def week_view(self, week_start: str, user: str) -> dict:
        """教师周视图：周拆解汇总 + 周一至周日逐日任务（含完成态）。

        周拆解（用户要的「自动拆解」）从 tasks 推导：
        - 阅读篇数（reading 目标书数）
        - 泛读总词数（各书目标% 对应词数 − 已读词数，按剩余摊到天）
        - 题型类数（讲次测验 + 作业卷 + FCE 项数，去重类目；微练习已是
          独立线上功能，不进周拆解）
        已到的日子一律 ``today_tasks``（信号；有快照才改走回填）。
        没有计划项时 percent 为 null，不显示成 0%。

        7 天共用一个 ``WeekCtx``：lecture_done / _vocab_actuals /
        _fce_actuals / _done_article_ids / _retold_video_ids 等周窗口
        信号每周只查一次，逐日前缀在 ctx 内切片（原先每天重扫一遍，
        同一周窗口 O(天²)）。
        """
        w = self._get_week(week_start, user=user)
        t = w.get("tasks") or {}
        today_iso = today_cn().isoformat()
        in_week = week_start <= today_iso <= add_days(week_start, 6)
        days_left = max(1, (date.fromisoformat(add_days(week_start, 6))
                            - date.fromisoformat(today_iso)).days + 1) if in_week else 7
        ctx = WeekCtx(self, user, week_start)

        # ---- 周拆解 ----
        reading_items = []
        for book_key, goal_val in (t.get("reading") or {}).items():
            row = ctx.match_book(book_key)
            # 章选格式（list）不做全书百分比状态（_book_reading_state 只吃
            # float——此前 list 直接传入会 500，周计划选章后周历/课程表必炸）
            st = (ctx.book_reading_state(row["id"], float(goal_val))
                  if row and not isinstance(goal_val, list) else None)
            # 章选格式补展示章区间（「第6–7章」）——学生课程表摘要直接引用
            chapter_range = None
            if isinstance(goal_val, list) and row:
                nos = ctx.chapter_nos(row["id"], goal_val)
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
        # 微练习已是独立线上功能（每天 3 题，不加分不进分母）：
        # 不再算题型、不再按「讲数 × 5 题 × 剩余天」估题数，drill_count 随之删除
        # （前后端均无消费方，grep 确认）。
        drill_types = set()
        if t.get("lectures"):
            drill_types.add("讲次测验")
        if t.get("hw_papers"):
            drill_types.add("作业卷")
        if t.get("fce"):
            drill_types.add("FCE 真题")
        if self._plan_videos(t):
            drill_types.add("视频复述")
        if self._plan_lessons(t):
            drill_types.add("听力跟读")
        breakdown = {
            "reading_books": len(reading_items),
            "reading_items": reading_items,
            "reading_words_total": rem_total,
            "reading_words_per_day": round(rem_total / days_left) if rem_total else 0,
            "drill_types": sorted(drill_types),
            "lecture_count": len(t.get("lectures") or []),
            "hw_paper_count": len(t.get("hw_papers") or []),
            "fce_count": len(t.get("fce") or []),
            "video_count": len(self._plan_videos(t)),
            "nce2_count": len(self._plan_lessons(t)),
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
                fut = self.week_daily_tasks(week_start, for_date=d, user=user,
                                            ctx=ctx)
                days.append({
                    "date": d, "future": True, "done": 0, "total": len(fut),
                    "percent": None, "basis": "signals",
                    "tasks": [task_public(x, future=True) for x in fut],
                })
                continue
            td = self.today_tasks(user=user, day=d, ctx=ctx)
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
            # 微练习门控（/plan/my-week 与教师周视图共用）：tasks.micro
            # 开关透出，前端据此显示/隐藏微练习入口
            "micro_enabled": bool(t.get("micro")),
            "breakdown": breakdown, "days": days, "current_week": in_week,
            # 视频任务补标题（学生课程表/计划卡显示片名；已删除的为 null）
            "video_titles": [
                {"vid": v, "title": self._video_title_any(v)}
                for v in self._plan_videos(t)
            ],
            # NCE2 听力任务补标题（manifest 缺课/缺文件为 null，不阻塞周视图）
            "nce2_titles": [
                {"lesson_id": lid, "title": ctx.nce2_title(lid)}
                for lid in self._plan_lessons(t)
            ],
            "goal_progress": self._week_goal_progress(t, days, user, week_start,
                                                      ctx=ctx),
            # 本周作业每项一次。日格子是同一套摊法的建议，不按星期几锁完成。
            "week_pool": build_week_pool(self, user, week_start),
            "micro_checkins": micro_checkins(
                self, user, week_start, bool(t.get("micro"))),
            "completion": {
                "done": done_n, "total": total_n,
                "percent": completion_percent(done_n, total_n),
                "basis": basis,
                "note": _COMPLETION_NOTE,
            },
        }


    def _week_goal_progress(self, t: dict, days: list, user: str,
                            week_start: str,
                            ctx: WeekCtx | None = None) -> dict:
        """周目标的各维度目标+进度（与总目标 goal_progress 的 dims 同构，
        供前端同一张「目标+进度」卡切换展示）。

        维度口径（按周任务配置）：
        - 讲次：计划讲次数 vs 本周考过的讲次数
        - 词汇：本周新掌握 = vocab_goal − 周初该级已会；分子是周内该级新 mastered_at
        - FCE：计划 Part 数 vs 周内提交覆盖数
        - 泛读：按各书周目标%（词数口径）vs 周内已读词数（实录汇总）
        - 试卷：计划卷数 vs 登记考试数
        - 视频复述：计划 vid 数 vs 周内提交复述数
        - 微练习：tasks.micro 开着才有；周一到周日打卡天数 / 7（漏打留在分母里）
        """
        dims = []

        def dim(icon, name, ok, total, extra=""):
            if total:
                dims.append({"icon": icon, "name": name,
                             "done": round(ok, 1), "total": total, "extra": extra})

        week_end = add_days(week_start, 7)
        lecs = t.get("lectures") or []
        if lecs:
            done_lec = len(set(lecs) & self.lecture_done(
                week_start, week_end, user, "lecture"))
            dim("📚", "语法课程", done_lec, len(lecs))

        hw_papers = t.get("hw_papers") or []
        if hw_papers:
            done_hw = len(set(hw_papers) & self.lecture_done(
                week_start, week_end, user, "hw"))
            dim("📝", "作业卷", done_hw, len(hw_papers))

        # 词汇差额口径（级别内增量）：分母 = 目标词数 − 周初该级别已掌握
        # 词数；分子 = 周内该级别新掌握词数。目标本身就是「L几 内掌握
        # N 词」，周初已超额的显示满格。
        vg = t.get("vocab_goal")
        if vg:
            lv = t.get("vocab_lv")
            lv_words = (ctx.level_words(lv) if ctx is not None
                        else self._vocab_level_words(lv))
            gained = (ctx.vocab_actuals(week_start, add_days(week_start, 7),
                                        lv_words) if ctx is not None else
                      self._vocab_actuals(week_start, add_days(week_start, 7),
                                          user, lv_words))
            base = (ctx.vocab_mastered_before(week_start, lv_words)
                    if ctx is not None else
                    self._vocab_mastered_before(week_start, user, lv_words))
            # 分母就是「本周新掌握」个数。编辑器按 base + N 写入 vocab_goal，
            # 老师看到的 N 和这里的 total 是同一个数。
            if lv is None:
                fresh = "本周新掌握"
                done_note = "周初已达成"
            else:
                fresh = f"L{int(lv)} 本周新掌握"
                done_note = f"L{int(lv)} · 周初已达成"
            if base >= vg:
                dim("🔤", "词汇", vg, vg, extra=done_note)
            else:
                denom = vg - base
                dim("🔤", "词汇", min(gained, denom), denom, extra=fresh)

        fce = t.get("fce") or []
        if fce:
            # 与 actuals._fce_actuals 同一窗口与标签构造（ctx 有预取则切片）
            got = set(self._fce_actuals(week_start, week_end, user))
            done_fce = sum(1 for x in fce if x in got)
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
                            live_pct = (prog[0] if prog else 0) or 0
                            if reading_moved_after(self, user, bid, week_end):
                                live_pct = focus_percent_before(
                                    self, user, bid, week_end)
                            pos = live_pct / 100 * total
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

        papers = t.get("vocab_papers") or []
        if papers:
            # 登记日落在本周周一到周日才算。下一周再登记不回写这一周。
            from .week_credit import WeekCredit
            done_ids = WeekCredit(self, user, week_start).papers()
            done_p = sum(1 for pid in papers if str(pid) in done_ids)
            dim("📝", "试卷", done_p, len(papers))

        videos = self._plan_videos(t)
        if videos:
            done_ids = self._retold_video_ids(week_start, week_end, user)
            dim("🎬", "视频复述", len(set(videos) & done_ids), len(videos))

        # NCE2 听力跟读：计划课数 vs 周内有提交（精听或跟读）的课数。
        # 周目标维度只表「动过了」，行级 listen/shadow 满足差异不在这判。
        lessons = self._plan_lessons(t)
        if lessons:
            done_lids = (
                self._nce2_kind_ids(week_start, week_end, user, "shadow")
                | self._nce2_kind_ids(week_start, week_end, user, "listen"))
            dim("🗣", "听力跟读", len(set(lessons) & done_lids), len(lessons))

        from . import boost as plan_boost
        boost_refs = [plan_boost.item_ref_of(x) for x in (t.get("boost_items") or [])
                      if isinstance(x, dict)]
        boost_refs = [r for r in boost_refs if r]
        if boost_refs:
            states = plan_boost.pass_state_map(user, week_start, week_end)
            done_b = sum(
                1 for r in boost_refs
                if (states.get(r) or {}).get("state") in ("passed", "pending", "graded_low"))
            dim("⭐", "提升档", done_b, len(boost_refs), extra="本周哪天做都算")

        # 微练习：周一到周日各一次打卡。分母 7；过了当天没打的算漏打，不能补。
        if t.get("micro"):
            rows = micro_checkins(self, user, week_start, True)
            done_days, missed, total_days = checkin_progress(rows)
            dim("✏️", "微练习", done_days, total_days or 7,
                extra=f"已打卡 {done_days} · 漏打 {missed}")

        total_items = sum(d["total"] for d in dims)
        # 泛读维度按实际计数（可超 100%）；其余维度封顶（完成即满）
        done_items = sum(
            d["done"] if d["name"] == "泛读" else min(d["done"], d["total"])
            for d in dims)
        pct = round(done_items / total_items * 100) if total_items else None
        return {"dims": dims, "percent": pct,
                "done_items": round(done_items), "total_items": total_items,
                "week_start": week_start}

    def _micro_done_days(self, user: str, lo: str, hi: str) -> int:
        """[lo, hi) 内 micro_daily 整组做完（done=1）的天数（周目标微练习维度）。

        micro_daily 由微练习模块建（fce.db 同库），这里只读：缺表/缺列
        一律容错 0，与 schedule._micro_daily_state 同一容错风格。
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n FROM micro_daily"
                    " WHERE user = ? AND date >= ? AND date < ? AND done = 1",
                    (user, lo, hi),
                ).fetchone()
                return int(row["n"] or 0) if row else 0
        except sqlite3.Error:
            return 0


    def week_preview(self, week_start: str, tasks: dict, user: str) -> dict:
        """未保存的周计划 → 学生课表那 7 天。摊法与 week_daily_tasks 相同。

        已冻结的日子仍用 plan_history_days，和正式课表一致。
        不写 plan_weeks。7 天共用一个 WeekCtx（同 week_view 的批量口径）。
        """
        if not isinstance(tasks, dict):
            raise ValueError("tasks 须为对象")
        day0 = date.fromisoformat(week_start)
        if week_start != monday_of(day0).isoformat():
            raise ValueError("week_start 须为周一日期")
        today_iso = today_cn().isoformat()
        ctx = WeekCtx(self, user, week_start)
        days = []
        for i in range(7):
            d = (day0 + timedelta(days=i)).isoformat()
            future = d > today_iso
            historical = None if future else ctx.history_day(d)
            if historical is not None:
                derived = historical
            else:
                derived = self.week_daily_tasks(
                    week_start, for_date=d, user=user, planned=tasks, ctx=ctx)
            if future:
                days.append({
                    "date": d, "future": True, "done": 0, "total": len(derived),
                    "percent": None,
                    "tasks": [task_public(x, future=True) for x in derived],
                })
                continue
            comp = self.day_completion(
                user, d, derived, historical=historical is not None, ctx=ctx)
            out = []
            for row in derived:
                key = _task_key(row)
                info = comp["keys"].get(key) or {"done": False, "why": "no_signal"}
                t2 = dict(row)
                t2["key"] = key
                t2["done"] = bool(info["done"])
                t2["why"] = info["why"]
                if (historical is None and t2.get("type") == "vocab"
                        and t2.get("quota")):
                    if t2.get("why") == "week_credit":
                        t2["detail"] = (
                            f"本周新掌握 {info.get('week_gained')}/{info.get('week_target')}"
                            "，哪一天背都算 ✓")
                    else:
                        t2["detail"] = vocab_progress_detail(
                            t2["done"], info.get("gained") or 0, t2.get("detail"))
                out.append(t2)
            done_n = sum(1 for x in out if x["done"])
            days.append({
                "date": d, "future": False,
                "done": done_n, "total": len(out),
                "percent": completion_percent(done_n, len(out)),
                "tasks": [task_public(x) for x in out],
            })
        return {"week_start": week_start, "days": days}


    def overview(self, user: str) -> dict:
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
                            or t.get("videos") or t.get("article_units")
                            or t.get("video_units") or t.get("micro")
                            or t.get("nce2_units"))
            done = total = 0
            bases: set[str] = set()
            if ws <= cur:  # 已开始的周才聚合完成度（未来周无信号）
                wctx = WeekCtx(self, user, ws)
                for i in range(7):
                    d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
                    if d > today_iso:
                        continue
                    # 过去的周也走信号。没有回填快照时不再把整周算成 0/0。
                    td = self.today_tasks(user=user, day=d, ctx=wctx)
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
                "vocab_new": t.get("vocab_new"),
                "reading": t.get("reading") or {},
                # 读契约同款：units 优先、旧 videos 平铺回退
                "videos": self._plan_videos(t),
                # NCE2 听力跟读（nce2_units 唯一来源，无旧键兜底）
                "nce2": self._plan_lessons(t),
                "notes": w.get("notes") or "",
                "done": done, "total": total,
                "percent": completion_percent(done, total) if ws <= cur else None,
                "basis": basis,
            })
            ws = (date.fromisoformat(ws) + timedelta(days=7)).isoformat()
        return {"weeks": weeks, "current_week": cur}

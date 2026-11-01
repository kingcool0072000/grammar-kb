"""周计划逐日均摊、今日任务与周清单。"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Optional

from ..clock import cn_dt, monday_of, today_cn, week_bounds
from .ctx import WeekCtx
from .util import _short, _task_key, add_days, completion_percent, task_public


def _cn_bound(value: str) -> str:
    """日期或带时间的边界 → ``YYYY-MM-DD HH:MM:SS``（配合 cn_dt 比较）。"""
    text = str(value).strip().replace("T", " ")
    if len(text) <= 10:
        return f"{text} 00:00:00"
    return text[:19]


def paper_task_detail(level: Optional[int]) -> str:
    """周日试卷任务说明。混合卷 level<0 写成「已学混合卷」，避免「L-1 级试卷」。"""
    if level is None:
        head = "试卷资产"
    elif int(level) < 0:
        head = "已学混合卷"
    else:
        head = f"L{int(level)} 级试卷"
    return head + " · 线下完成后到词汇考试登记"


class ScheduleMixin:

    def week_daily_tasks(self, week_start: str, user: str,
                         for_date: Optional[str] = None,
                         planned: Optional[dict] = None,
                         ctx: Optional[WeekCtx] = None) -> list[dict]:
        """周目标 → for_date 当日的任务清单（逐日均摊，外循环第③步）。

        均摊节奏按家长 10-04 定的模板：工作日=讲次+背词+泛读，
        周六=FCE 大题+精读朗读，周日=错词再练+试卷+补泛读。
        剩余量动态重摊：按「周目标 − 截至 for_date 已完成」重算后摊给
        剩余天——错过的日子欠账自动滚入后面的天。纯派生不落库。
        for_date 缺省=今天；不在本周时以本周一为锚（整周预览）。
        ``ctx``：周级信号缓存（week_view/overview 逐日复用一次预取），
        缺省自建单日 ctx，行为不变。
        """
        if ctx is None:
            ctx = WeekCtx(self, user, week_start)
        if planned is None:
            w = ctx.week_tasks(week_start)
            t = w.get("tasks") or {}
        else:
            t = planned
        ref = for_date or today_cn().isoformat()
        if not (week_start <= ref <= add_days(week_start, 6)):
            ref = week_start
        rd = date.fromisoformat(ref)
        ws_d = date.fromisoformat(week_start)
        dow = rd.weekday()  # 0=周一 … 5=周六 6=周日
        days_left = max(1, (ws_d + timedelta(days=6) - rd).days + 1)
        # ref..周日里剩余的工作日/周六数（均摊分母）
        rest = [ws_d + timedelta(days=i) for i in range((rd - ws_d).days, 7)]
        wd_left = sum(1 for x in rest if x.weekday() < 5)
        sat_left = sum(1 for x in rest if x.weekday() == 5)

        # ---- 截至 ref（含）的已完成——欠账重摊的基数 ----
        lo, _ = week_bounds(week_start)
        upto = (rd + timedelta(days=1)).isoformat()
        done_lecs = ctx.lecture_done(lo, upto, "lecture")
        done_hw = ctx.lecture_done(lo, upto, "hw")
        # 今天已完成的讲次/卷——保留出行为（完成信号会点亮 ✓，
        # 当天清单=今天做完的 ✓ + 今天新分配的灰）
        today_lecs = ctx.lecture_done(ref, upto, "lecture")
        today_hw = ctx.lecture_done(ref, upto, "hw")
        fce_done = set(ctx.fce_actuals(lo, upto))
        tasks: list[dict] = []

        def hw_row(lec: int) -> dict:
            return {
                "type": "hw_paper", "key": f"hw:{lec}",
                # 文案不带 emoji：行首类型图标已表达，重复占宽
                "text": f"作业卷（第{lec}讲卷）",
                "detail": "线下完成考卷，成绩登记类型选「作业卷」后自动勾",
            }

        if dow < 5:
            # ---- 工作日：攻坚微练习 + 讲次/作业卷 + 背词 + 泛读 ----
            for lec in (t.get("focus_kps") or []):
                tasks.append({
                    "type": "micro_drill", "key": f"micro:{lec}",
                    "lecture": lec, "text": f"第{lec}讲 微练习 5 题",
                })
            # 讲次（课程测验口径）与作业卷（hw 口径）各自按 kind 判剩余，
            # 剩余量摊给剩余工作日；今天考过的行照常出（自动 ✓）
            lec_rest = [x for x in (t.get("lectures") or []) if x not in done_lecs]
            hw_rest = [x for x in (t.get("hw_papers") or []) if x not in done_hw]
            if lec_rest and wd_left:
                per = max(1, -(-len(lec_rest) // wd_left))
                for lec in lec_rest[:per]:
                    tasks.append({
                        "type": "lecture", "key": f"lecture:{lec}",
                        "text": f"第{lec}讲 学+测",
                        "detail": f"本周讲次剩 {len(lec_rest)} 讲 · 摊 {wd_left} 个工作日",
                    })
                for lec in hw_rest:
                    # 跟讲次排程同行；讲次不在本周课程清单的独立卷也直接出
                    if (lec in lec_rest[:per]
                            or lec not in (t.get("lectures") or [])):
                        tasks.append(hw_row(lec))
            # 今天已完成但不在新排程里的行补上（含今天考过但已非剩余的）
            have = {x.get("key") for x in tasks}
            for lec in sorted(today_lecs):
                if f"lecture:{lec}" not in have:
                    tasks.append({
                        "type": "lecture", "key": f"lecture:{lec}",
                        "text": f"第{lec}讲 学+测",
                        "detail": "今天已完成 ✓",
                    })
            for lec in sorted(today_hw):
                if f"hw:{lec}" not in have:
                    tasks.append(hw_row(lec))
            vg = t.get("vocab_goal")
            if vg and wd_left:
                rem = self._vocab_week_remaining(t, lo, upto, user, ctx=ctx)
                if rem > 0:
                    n = -(-rem // wd_left)
                    lv = t.get("vocab_lv")
                    detail = (
                        f"本周这一级还要新掌握 {rem} 词 · 摊 {wd_left} 个工作日"
                        if lv is not None
                        else f"周目标剩 {rem} 词 · 摊 {wd_left} 个工作日"
                    )
                    tasks.append({
                        "type": "vocab", "key": "vocabday", "quota": n,
                        "text": f"背词 {n} 词",
                        "detail": detail,
                    })
            for book_key, pct in (t.get("reading") or {}).items():
                tasks.append(self._reading_task(book_key, pct, user, days_left,
                                                ctx=ctx))
            # 周中已提前提交朗读的精读篇目：当天清单补 ✓ 行（否则只进
            # 「计划外朗读」实录，计划内完成度看不见——孩子提前做完
            # 反而显示没完成）
            today_arts = ctx.done_article_ids(ref, upto)
            for aid in (t.get("articles") or []):
                if aid in today_arts:
                    title = ctx.article_title(aid)
                    if title:
                        tasks.append({
                            "type": "article", "article_id": aid,
                            "key": f"article:{aid}",
                            "text": f"精读+朗读《{_short(title, 14)}》",
                            "detail": "今天已提交朗读 ✓",
                        })
        elif dow == 5:
            # ---- 周六：FCE 大题 + 精读朗读（模板：周六整块给输出型任务）----
            fce_rest = [x for x in (t.get("fce") or []) if x not in fce_done]
            per = max(1, -(-len(fce_rest) // max(1, sat_left))) if fce_rest else 0
            for item in fce_rest[:per]:
                tasks.append({
                    "type": "fce", "key": "fce",
                    "text": f"FCE {item}",
                    "detail": "周六大题 · 先看预习词表再限时做",
                })
            arts = [a for a in (t.get("articles") or [])
                    if a not in (ctx.done_article_ids(lo, upto))]
            for aid in arts[:2]:
                title = ctx.article_title(aid)
                if title:
                    tasks.append({
                        "type": "article", "article_id": aid,
                        "key": f"article:{aid}",
                        "text": f"精读+朗读《{_short(title, 14)}》",
                        "detail": "阅读训练里读全文 + 提交朗读录音",
                    })
            # 今天刚提交朗读的计划内文章：上面按「周内已朗」滤剩余时把它也
            # 滤掉了，清单无行 → 计划表看不见 ✓（孩子做完了反而像没做）。
            # 仿讲次 today_lecs 补行：完成的也要看得见。
            today_arts = ctx.done_article_ids(ref, upto)
            have_arts = {x.get("article_id") for x in tasks}
            for aid in (t.get("articles") or []):
                if aid in today_arts and aid not in have_arts:
                    title = ctx.article_title(aid)
                    if title:
                        tasks.append({
                            "type": "article", "article_id": aid,
                            "key": f"article:{aid}",
                            "text": f"精读+朗读《{_short(title, 14)}》",
                            "detail": "今天已提交朗读 ✓",
                        })
            # 周六补一段泛读（周中没读完的量周六兜底，量小自然显示小目标）
            for book_key, pct in (t.get("reading") or {}).items():
                tasks.append(self._reading_task(book_key, pct, user, days_left))
        else:
            # ---- 周日：错词再练 + 试卷 + 补泛读（周日晚留白复盘）----
            wrong_words = ctx.wrong_words_count()
            if wrong_words:
                n = min(30, wrong_words)
                tasks.append({
                    "type": "wrong_words", "key": "wrong_words",
                    "text": f"错词再练 {n} 词",
                    "detail": f"云端错题本共 {wrong_words} 词，周日清一档",
                })
            for pid in (t.get("vocab_papers") or []):
                plv = ctx.paper_level(pid)
                tasks.append({
                    "type": "paper", "key": f"paper:{pid}",
                    "text": f"单词试卷练习（{pid}）",
                    "detail": paper_task_detail(plv),
                })
            # 周日泛读兜底：周六前（含周六）仍未读完的剩余量周日一天清
            for book_key, pct in (t.get("reading") or {}).items():
                tasks.append(self._reading_task(book_key, pct, user, 1, ctx=ctx))
            # 今天刚提交朗读的计划内文章也要出 ✓ 行（周日同样：孩子做完了
            # 反而像没做，与周六分支同修）
            today_arts = ctx.done_article_ids(ref, upto)
            have_arts = {x.get("article_id") for x in tasks}
            for aid in (t.get("articles") or []):
                if aid in today_arts and aid not in have_arts:
                    title = ctx.article_title(aid)
                    if title:
                        tasks.append({
                            "type": "article", "article_id": aid,
                            "key": f"article:{aid}",
                            "text": f"精读+朗读《{_short(title, 14)}》",
                            "detail": "今天已提交朗读 ✓",
                        })
        # 讲次/FCE 剩余但本日类型无槽位（如 ref 已周六、讲次还没排完）→ 兜底行；
        # 今天考过的讲次/作业卷在周末也要有 ✓ 行（完成即视，不再隐形）
        if dow >= 5:
            lec_rest = [x for x in (t.get("lectures") or []) if x not in done_lecs]
            for lec in lec_rest[:1]:
                tasks.append({
                    "type": "lecture", "key": f"lecture:{lec}",
                    "text": f"第{lec}讲 学+测（补）",
                    "detail": "本周剩余讲次，周末补上",
                })
            have = {x.get("key") for x in tasks}
            for lec in sorted(today_lecs | today_hw):
                if f"lecture:{lec}" in have or f"hw:{lec}" in have:
                    continue
                if lec in today_lecs:
                    tasks.append({
                        "type": "lecture", "key": f"lecture:{lec}",
                        "text": f"第{lec}讲 学+测",
                        "detail": "今天已完成 ✓",
                    })
                if lec in today_hw:
                    tasks.append(hw_row(lec))
        self._append_video_tasks(tasks, t, user, lo, ref, upto, dow, wd_left,
                                 ctx=ctx)
        return tasks

    def _vocab_week_remaining(self, t: dict, lo: str, upto: str, user: str,
                              ctx: Optional[WeekCtx] = None) -> int:
        """本周还要背的词数，摊到剩余工作日。

        没标级别时，vocab_goal 仍是本周词数预算（旧周、旧测试）。
        标了 vocab_lv 时与周进度同一口径：先算「目标词数 − 周初该级已会」，
        再减去本周该级已经新掌握的。教师填的「本周新掌握 N 个」
        会摊到工作日，而不是把级别里的累计目标整段摊出去。
        """
        try:
            vg = int(t.get("vocab_goal") or 0)
        except (TypeError, ValueError):
            return 0
        if vg <= 0:
            return 0
        lv = t.get("vocab_lv")
        if lv is None:
            gained = self._vocab_actuals(lo, upto, user) if ctx is None \
                else ctx.vocab_actuals(lo, upto)
            return max(0, vg - gained)
        try:
            level = int(lv)
        except (TypeError, ValueError):
            gained = self._vocab_actuals(lo, upto, user) if ctx is None \
                else ctx.vocab_actuals(lo, upto)
            return max(0, vg - gained)
        words = (self._vocab_level_words(level) if ctx is None
                 else ctx.level_words(level))
        base = (self._vocab_mastered_before(lo, user, words) if ctx is None
                else ctx.vocab_mastered_before(lo, words))
        if base >= vg:
            return 0
        gained = (self._vocab_actuals(lo, upto, user, words) if ctx is None
                  else ctx.vocab_actuals(lo, upto, words))
        return max(0, (vg - base) - gained)

    def _append_video_tasks(self, tasks: list[dict], t: dict, user: str,
                            lo: str, ref: str, upto: str, dow: int,
                            wd_left: int,
                            ctx: Optional[WeekCtx] = None) -> None:
        """工作日每天 1 个看+复述；周末每天至多补 1 个。当天提交的行保留 ✓。"""
        if ctx is None:
            ctx = WeekCtx(self, user, monday_of(date.fromisoformat(ref)).isoformat())
        vids = [str(v).strip() for v in (t.get("videos") or []) if str(v).strip()]
        if not vids:
            return
        done_vids = ctx.retold_video_ids(lo, upto)
        today_vids = ctx.retold_video_ids(ref, upto)
        rest = [v for v in vids if v not in done_vids]
        # 欠账滚到后面的天，不在同一天堆多条（复述任务重）。
        if rest and (dow >= 5 or wd_left):
            detail = (
                f"本周视频剩 {len(rest)} 个 · 周末补上" if dow >= 5
                else f"本周视频剩 {len(rest)} 个 · 工作日每天 1 个"
            )
            for vid in rest:
                row = self._video_task(vid, detail=detail, ctx=ctx)
                if row:
                    tasks.append(row)
                    break
        have = {x.get("key") for x in tasks}
        for vid in vids:
            if vid in today_vids and f"video:{vid}" not in have:
                row = self._video_task(vid, detail="今天已提交复述 ✓", ctx=ctx)
                if row:
                    tasks.append(row)

    def _video_task(self, vid: str, *, detail: str,
                    ctx: Optional[WeekCtx] = None) -> Optional[dict]:
        title = (ctx.video_title(vid) if ctx is not None
                 else self._video_title(vid))
        if not title:
            return None  # 已删除或下架：不出行（与学生可见清单一致）
        return {
            "type": "video", "video_id": vid, "vid": vid,
            "key": f"video:{vid}",
            "text": f"看视频+复述《{_short(title, 14)}》",
            "detail": detail,
        }

    def _video_title(self, vid: str) -> Optional[str]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT title FROM video_item WHERE vid = ? AND enabled = 1",
                    (str(vid),)).fetchone()
                title = (row["title"] or "").strip() if row else ""
                return title or None
        except sqlite3.Error:
            return None

    def _video_title_any(self, vid: str) -> Optional[str]:
        """视频标题（含已下架；课程表/周视图回显用，无 enabled 过滤）。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT title FROM video_item WHERE vid = ?",
                    (str(vid),)).fetchone()
                return row["title"] if row else None
        except sqlite3.Error:
            return None

    def _retold_video_ids(self, lo: str, hi: str, user: str) -> set[str]:
        """[lo, hi) 内该生提交过复述的视频 vid。缺表时返回空集。"""
        try:
            with self._connect() as conn:
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "video_retell" not in names:
                    return set()
                rows = conn.execute(
                    "SELECT DISTINCT vid FROM video_retell"
                    f" WHERE user = ? AND {cn_dt('created_at')} >= ?"
                    f" AND {cn_dt('created_at')} < ?",
                    (user, _cn_bound(lo), _cn_bound(hi)),
                ).fetchall()
                return {r["vid"] for r in rows if r["vid"]}
        except sqlite3.Error:
            return set()


    def _wrong_words_count(self, user: str) -> int:
        """错词本在周期词数（艾宾浩斯口径，与 /recite/wrongbook 一致：
        recite_review 里 conquered_at 为空的行；表不在/缺列容错 0）。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n FROM recite_review"
                    " WHERE user = ? AND conquered_at IS NULL", (user,),
                ).fetchone()
                return row["n"] if row else 0
        except sqlite3.Error:
            return 0


    def _done_article_ids(self, lo: str, hi: str, user: str) -> set:
        """[lo, hi) 内已提交朗读录音的精读文章 id（剩余精读排程用）。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT article_id FROM reading_recordings"
                    " WHERE user = ? AND created_at >= ? AND created_at < ?",
                    (user, lo, hi),
                ).fetchall()
                return {r["article_id"] for r in rows}
        except sqlite3.Error:
            return set()


    def _paper_level(self, paper_id: str) -> Optional[int]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT level FROM vocab_paper_history WHERE paper_id = ?",
                    (str(paper_id)[:64],)).fetchone()
                return row["level"] if row else None
        except sqlite3.Error:
            return None


    @staticmethod
    def _pid_level(paper_id: str) -> Optional[int]:
        """paper_id 的 L{n}- 前缀解析级别（生成命名的固定格式；删档卷
        历史表查不到时的兜底）。"""
        m = re.match(r"^L(\d)-", str(paper_id or ""))
        return int(m.group(1)) if m else None


    def _article_title(self, article_id: int) -> Optional[str]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT title FROM reading_article WHERE id = ?", (int(article_id),),
                ).fetchone()
                return row["title"] if row else None
        except sqlite3.Error:
            return None


    # ---- 内循环：学生今日任务 / 周清单（完成只看学习信号） ----

    def today_tasks(self, user: str, day: Optional[str] = None,
                    ctx: Optional[WeekCtx] = None) -> dict:
        """学生某日的任务清单（内循环核心）。

        任务由本周 plan_weeks.tasks 派生（与教师驾驶舱「每日任务展开」
        同一规则）。完成态只认 ``day_completion``——学生做了即亮。
        ``plan_day_checks`` 不读、不写。
        某日若已有 ``plan_history_days`` 快照，用快照代替派生清单
        （可选回填，不是每日流水）。
        ``ctx``：周级信号缓存；缺省自建（单日语义不变）。
        """
        d = day or today_cn().isoformat()
        ws = monday_of(date.fromisoformat(d)).isoformat()
        if ctx is None:
            ctx = WeekCtx(self, user, ws)
        historical = ctx.history_day(d)
        derived = historical if historical is not None else self.week_daily_tasks(ws, for_date=d, user=user, ctx=ctx)
        comp = self.day_completion(
            user, d, derived, historical=historical is not None, ctx=ctx)
        acts_out = comp["acts"]
        out = []
        for t in derived:
            key = _task_key(t)
            info = comp["keys"].get(key) or {"done": False, "why": "no_signal"}
            t2 = dict(t)
            t2["key"] = key
            t2["auto_done"] = bool(info["done"])
            t2["done"] = t2["auto_done"]
            t2["why"] = info["why"]
            # 展示用的补充文案不是完成规则，留在清单出口。
            # 历史回填只认快照，不改写 detail。
            if historical is None and t.get("type") == "vocab" and t.get("quota") and t2["done"]:
                t2["detail"] = f"已背 {acts_out.get('vocab_n', 0)} 词 ✓"
            if historical is None and t.get("type") == "speak" and t2["done"]:
                names = []
                for aid in acts_out.get("all_speak_articles", []):
                    ti = (ctx.article_title(aid) if ctx is not None
                          else self._article_title(aid))
                    if ti:
                        names.append(ti[:16])
                if names:
                    t2["detail"] = "读了：" + "、".join(dict.fromkeys(names))
            out.append(t2)
        done_n = sum(1 for x in out if x["done"])
        frozen = historical is not None
        return {"date": d, "week_start": ws, "tasks": out,
                "done": done_n, "total": len(out),
                "percent": completion_percent(done_n, len(out)),
                "basis": "history" if frozen else "signals",
                "historical": frozen,
                "acts": acts_out}


    def _signals_and_acts(self, user: str, day: str,
                          planned_articles: set,
                          ctx: Optional[WeekCtx] = None) -> tuple[dict, list, dict]:
        """当天活动信号 + 量化实录（历史日快照路径同样调用）。

        计划内任务已按信号打勾；实录补两类信息：
        1) 计划外的朗读提交（计划没排该篇，但学生做了——原逻辑直接不可见）
        2) 量化实录：泛读分钟+词数 / 背单词个数+分钟（无论是否在计划内）

        acts 由 ``_auto_done_full`` 随返回值带回（原先写在 Store 单例的
        ``_day_acts`` 字段上，并发请求互相覆盖，已废除）。
        """
        auto, read_titles, acts = self._auto_done_full(user, day, ctx=ctx)
        extra_speaks = []
        for aid in acts.get("speak_articles", []):
            if aid not in planned_articles:
                title = (ctx.article_title(aid) if ctx is not None
                         else self._article_title(aid))
                extra_speaks.append({
                    "type": "speak", "done": True,
                    "text": f"朗读《{title[:16]}》" if title else f"朗读提交（文章{aid}）",
                    "detail": "计划外提交，录音已进批改中心",
                })
        acts_out = {
            "extra_speaks": extra_speaks,
            "all_speak_articles": acts.get("speak_articles", []),
            "readings": acts.get("readings", []),
            "vocab_n": acts.get("vocab_n", 0),
            "vocab_min": acts.get("vocab_min", 0),
            "study_min": acts.get("study_min", 0),
            "focus_min": round(acts.get("focus_sec", 0) / 60),
        }
        return auto, read_titles, acts_out


    def week_todo(self, user: str) -> dict:
        """学生周清单：本周目标 + 周一至周日逐日任务完成矩阵。"""
        today_iso = today_cn().isoformat()
        ws = monday_of(today_cn()).isoformat()
        w = self._get_week(ws, user=user)
        t = w.get("tasks") or {}
        ctx = WeekCtx(self, user, ws)
        days = []
        for i in range(7):
            d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
            if d > today_iso:
                fut = self.week_daily_tasks(ws, for_date=d, user=user, ctx=ctx)
                days.append({
                    "date": d, "future": True,
                    "done": 0, "total": len(fut),
                    "percent": None,
                    "basis": "signals",
                    "tasks": [task_public(x, future=True) for x in fut],
                })
                continue
            td = self.today_tasks(user=user, day=d, ctx=ctx)
            days.append({
                "date": d, "future": False,
                "done": td["done"], "total": td["total"],
                "percent": td["percent"],
                "basis": td["basis"],
                "tasks": [task_public(x) for x in td["tasks"]],
            })
        past = [x for x in days if not x["future"]]
        done_n = sum(x["done"] for x in past)
        total_n = sum(x["total"] for x in past)
        return {
            "week_start": ws,
            "goals": {
                "lectures": t.get("lectures") or [],
                "hw_papers": t.get("hw_papers") or [],
                "vocab_goal": t.get("vocab_goal"),
                "fce": t.get("fce") or [],
                "reading": t.get("reading") or {},
                "focus_kps": t.get("focus_kps") or [],
                "videos": t.get("videos") or [],
            },
            "days": days,
            "done": done_n,
            "total": total_n,
            "percent": completion_percent(done_n, total_n),
            "completion_note": (
                "完成率按学习信号自动计算，不看手动打卡。"
                "plan_history_days 只在教师冻结过的日期替代信号，不是每日流水。"
            ),
        }

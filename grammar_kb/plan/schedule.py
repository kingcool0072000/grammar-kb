"""周计划逐日均摊、今日任务与周清单。"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Optional

from ..clock import monday_of, today_cn, week_bounds
from .util import _short, _task_key, add_days


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

    def week_daily_tasks(self, week_start: str, for_date: Optional[str] = None,
                         user: str = "malin") -> list[dict]:
        """周目标 → for_date 当日的任务清单（逐日均摊，外循环第③步）。

        均摊节奏按家长 10-04 定的模板：工作日=讲次+背词+泛读，
        周六=FCE 大题+精读朗读，周日=错词再练+试卷+补泛读。
        剩余量动态重摊：按「周目标 − 截至 for_date 已完成」重算后摊给
        剩余天——错过的日子欠账自动滚入后面的天。纯派生不落库。
        for_date 缺省=今天；不在本周时以本周一为锚（整周预览）。
        """
        w = self._get_week(week_start, user=user)
        t = w.get("tasks") or {}
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
        done_lecs = self.lecture_done(lo, upto, user, "lecture")
        done_hw = self.lecture_done(lo, upto, user, "hw")
        # 今天已完成的讲次/卷——保留出行为（完成信号会点亮 ✓，
        # 当天清单=今天做完的 ✓ + 今天新分配的灰）
        today_lecs = self.lecture_done(ref, upto, user, "lecture")
        today_hw = self.lecture_done(ref, upto, user, "hw")
        vocab_done = self._vocab_actuals(lo, upto, user)
        fce_done = set(self._fce_actuals(lo, upto, user))
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
                rem = max(0, int(vg) - vocab_done)
                if rem > 0:
                    n = -(-rem // wd_left)
                    tasks.append({
                        "type": "vocab", "key": "vocabday", "quota": n,
                        "text": f"背词 {n} 词",
                        "detail": f"周目标剩 {rem} 词 · 摊 {wd_left} 个工作日",
                    })
            for book_key, pct in (t.get("reading") or {}).items():
                tasks.append(self._reading_task(book_key, pct, user, days_left))
            # 周中已提前提交朗读的精读篇目：当天清单补 ✓ 行（否则只进
            # 「计划外朗读」实录，计划内完成度看不见——孩子提前做完
            # 反而显示没完成）
            today_arts = self._done_article_ids(ref, upto, user)
            for aid in (t.get("articles") or []):
                if aid in today_arts:
                    title = self._article_title(aid)
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
                    if a not in (self._done_article_ids(lo, upto, user))]
            for aid in arts[:2]:
                title = self._article_title(aid)
                if title:
                    tasks.append({
                        "type": "article", "article_id": aid,
                        "key": f"article:{aid}",
                        "text": f"精读+朗读《{_short(title, 14)}》",
                        "detail": "阅读训练里读全文 + 提交朗读录音",
                    })
            # 周六补一段泛读（周中没读完的量周六兜底，量小自然显示小目标）
            for book_key, pct in (t.get("reading") or {}).items():
                tasks.append(self._reading_task(book_key, pct, user, days_left))
        else:
            # ---- 周日：错词再练 + 试卷 + 补泛读（周日晚留白复盘）----
            wrong_words = self._wrong_words_count(user)
            if wrong_words:
                n = min(30, wrong_words)
                tasks.append({
                    "type": "wrong_words", "key": "wrong_words",
                    "text": f"错词再练 {n} 词",
                    "detail": f"云端错题本共 {wrong_words} 词，周日清一档",
                })
            for pid in (t.get("vocab_papers") or []):
                plv = self._paper_level(pid)
                tasks.append({
                    "type": "paper", "key": f"paper:{pid}",
                    "text": f"单词试卷练习（{pid}）",
                    "detail": paper_task_detail(plv),
                })
            # 周日泛读兜底：周六前（含周六）仍未读完的剩余量周日一天清
            for book_key, pct in (t.get("reading") or {}).items():
                tasks.append(self._reading_task(book_key, pct, user, 1))
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
        return tasks


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


    # ---- 内循环：学生今日任务 / 周清单（自动检测 + 手动打卡） ----

    def today_tasks(self, user: str = "malin", day: Optional[str] = None) -> dict:
        """学生某日的任务清单（内循环核心）。

        任务由本周 plan_weeks.tasks 派生（与教师驾驶舱「每日任务展开」
        同一规则）。完成态只认 ``day_completion``——学生做了即亮，
        不提供手动打卡（plan_day_checks 保留给教师端干预，不进 done）。
        """
        d = day or today_cn().isoformat()
        ws = monday_of(date.fromisoformat(d)).isoformat()
        historical = self.history_day(user, d)
        derived = historical if historical is not None else self.week_daily_tasks(ws, for_date=d, user=user)
        comp = self.day_completion(
            user, d, derived, historical=historical is not None)
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
                    ti = self._article_title(aid)
                    if ti:
                        names.append(ti[:16])
                if names:
                    t2["detail"] = "读了：" + "、".join(dict.fromkeys(names))
            out.append(t2)
        done_n = sum(1 for x in out if x["done"])
        return {"date": d, "week_start": ws, "tasks": out,
                "done": done_n, "total": len(out), "acts": acts_out}


    def _signals_and_acts(self, user: str, day: str,
                          planned_articles: set) -> tuple[dict, list, dict]:
        """当天活动信号 + 量化实录（历史日快照路径同样调用）。

        计划内任务已按信号打勾；实录补两类信息：
        1) 计划外的朗读提交（计划没排该篇，但学生做了——原逻辑直接不可见）
        2) 量化实录：泛读分钟+词数 / 背单词个数+分钟（无论是否在计划内）
        """
        auto, read_titles = self._auto_done(user, day)
        acts = getattr(self, "_day_acts", None) or {}
        extra_speaks = []
        for aid in acts.get("speak_articles", []):
            if aid not in planned_articles:
                title = self._article_title(aid)
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


    def week_todo(self, user: str = "malin") -> dict:
        """学生周清单：本周目标 + 周一至周日逐日任务完成矩阵。"""
        today_iso = today_cn().isoformat()
        ws = monday_of(today_cn()).isoformat()
        w = self._get_week(ws, user=user)
        t = w.get("tasks") or {}
        days = []
        for i in range(7):
            d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
            if d > today_iso:
                fut = self.week_daily_tasks(ws, for_date=d, user=user)
                days.append({"date": d, "future": True,
                             "done": 0, "total": len(fut)})
                continue
            td = self.today_tasks(user=user, day=d)
            days.append({
                "date": d, "future": False,
                "done": td["done"], "total": td["total"],
            })
        return {
            "week_start": ws,
            "goals": {
                "lectures": t.get("lectures") or [],
                "hw_papers": t.get("hw_papers") or [],
                "vocab_goal": t.get("vocab_goal"),
                "fce": t.get("fce") or [],
                "reading": t.get("reading") or {},
                "focus_kps": t.get("focus_kps") or [],
            },
            "days": days,
        }

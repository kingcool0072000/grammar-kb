"""上周回顾、错题诊断、讲次标题。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .. import dbreg
from ..clock import today_cn, week_bounds
from ..paths import data_path
class ReviewMixin:

    def last_week_review(self, week_start: str, user: str) -> dict:
        """上周回顾：自动摘要 + 上周的 notes（编辑当前周时作参照）。"""
        prev = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
        lo, hi = week_bounds(prev)
        lectures = self._exam_actuals(lo, hi)
        avg = round(sum(x["score"] for x in lectures) / len(lectures), 1) if lectures else None
        low = [x for x in lectures if x["score"] < 70]
        return {
            "week_start": prev,
            "summary": {
                "lecture_count": len(lectures),
                "avg_score": avg,
                "low_scores": low,
                "vocab_mastered": self._vocab_actuals(lo, hi, user),
                "fce_parts": self._fce_actuals(lo, hi, user),
            },
            "notes": self.get_week_notes(prev, user=user),
        }


    def get_week_notes(self, week_start: str, user: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT notes FROM plan_weeks WHERE user = ? AND week_start = ?",
                (user, week_start),
            ).fetchone()
        return row["notes"] if row else ""


    # ---- 外循环：诊断聚类 + 周验收 ----
    # 双循环设计：外循环（教师，周尺度）= 诊断感知 → 周目标 → 任务展开 → 验收；
    # 内循环（学生，日尺度）执行任务并自动回收信号（exam_records / recite /
    # fce_submission / reading_progress 都已是自动回流，此处只做聚合）。

    def diagnosis(self, weeks_back: int = 8) -> list[dict]:
        """错题信号按讲次聚类（外循环第①步：诊断感知）。

        口径与教师手工《错题清单_20-28讲》一致：以讲次为单位聚合近期
        （默认 8 周）所有作答记录——错题数、出现次数、最近日期、最近得分。
        讲次标题/分类从 grammar.db lecture 表补全（读不到就留空，聚类仍成立）。
        排序：错题总数降序（主）+ 最近出现时间降序（次），无错题的讲次不出现。
        """
        try:
            with dbreg.connect(self._exam_db(), readonly=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT lecture, date, score, wrong FROM exam_records"
                    " WHERE date >= ? ORDER BY date",
                    ((today_cn() - timedelta(weeks=weeks_back)).isoformat(),),
                ).fetchall()
        except sqlite3.Error:
            return []
        agg: dict[int, dict] = {}
        for r in rows:
            try:
                wrong = json.loads(r["wrong"] or "[]")
            except (ValueError, TypeError):
                wrong = []
            a = agg.setdefault(r["lecture"], {
                "lecture": r["lecture"], "total_wrong": 0, "attempts": 0,
                "last_date": r["date"], "last_score": r["score"],
            })
            a["total_wrong"] += len(wrong)
            a["attempts"] += 1
            if r["date"] >= a["last_date"]:
                a["last_date"] = r["date"]
                a["last_score"] = r["score"]
        items = [a for a in agg.values() if a["total_wrong"] > 0]
        titles = self._lecture_titles()
        for a in items:
            t = titles.get(a["lecture"])
            a["title"] = t["title"] if t else ""
            a["category"] = t["category"] if t else ""
        # 主键降序 + 日期降序：先按错题数排，同数取更近的
        items.sort(key=lambda x: (x["total_wrong"], x["last_date"]), reverse=True)
        return items


    def _lecture_titles(self) -> dict[int, dict]:
        """lecture_number → {title, category}（grammar.db 只读；失败返回空）。"""
        path = self.grammar_db_path or str(
            data_path("grammar.db")
        )
        if not Path(path).exists():
            return {}
        try:
            with dbreg.connect(path, readonly=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT number, title, category FROM lecture"
                ).fetchall()
            return {r["number"]: {"title": r["title"], "category": r["category"]}
                    for r in rows}
        except sqlite3.Error:
            return {}


    def week_review(self, week_start: str, user: str) -> dict:
        """周验收（外循环第④步）：对照上周 focus_kps 判收敛/滚动/升级。

        判据（讲次级，数据可得性优先）：
        - converged：周内该讲次有 ≥1 次作答且最近一次错题数为 0（或未再错）
        - escalate：该讲次出现在 focus_kps 已连续 ≥2 周且仍未收敛 → 建议转专题
        - rolling：其余（默认路径，下周继续）
        """
        prev = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
        prev_week = self._get_week(prev, user=user)
        focus = (prev_week.get("tasks") or {}).get("focus_kps") or []
        lo, hi = week_bounds(prev)
        try:
            with dbreg.connect(self._exam_db(), readonly=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT lecture, date, wrong FROM exam_records"
                    " WHERE date >= date(?) AND date < date(?) ORDER BY date",
                    (lo, hi),
                ).fetchall()
        except sqlite3.Error:
            rows = []
        # 讲次 → 周内最近一次错题数
        last_wrong: dict[int, int] = {}
        for r in rows:
            try:
                last_wrong[r["lecture"]] = len(json.loads(r["wrong"] or "[]"))
            except (ValueError, TypeError):
                last_wrong[r["lecture"]] = 0
        # 连续在焦周数：往前逐周数 focus_kps 里含该讲次的连续周数
        streak = {}
        for lec in focus:
            n, ws = 0, prev
            while True:
                w = self._get_week(ws, user=user)
                if lec in ((w.get("tasks") or {}).get("focus_kps") or []):
                    n += 1
                    ws = (date.fromisoformat(ws) - timedelta(days=7)).isoformat()
                else:
                    break
            streak[lec] = n
        items = []
        for lec in focus:
            tried = lec in last_wrong
            converged = tried and last_wrong[lec] == 0
            escalate = streak[lec] >= 2 and not converged
            items.append({
                "lecture": lec,
                "status": "converged" if converged else
                          ("escalate" if escalate else "rolling"),
                "retested": tried,
                "last_wrong": last_wrong.get(lec),
                "focus_streak": streak[lec],
            })
        titles = self._lecture_titles()
        for it in items:
            t = titles.get(it["lecture"])
            it["title"] = t["title"] if t else ""
        return {"week_start": prev, "items": items}

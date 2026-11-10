"""上周回顾（编辑当前周计划时的参照摘要）。"""
from __future__ import annotations

from datetime import date, timedelta

from ..clock import week_bounds
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

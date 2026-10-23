"""某学生某一天：哪些计划键算完成，以及原因。

``today_tasks`` 只负责把这里的结果贴回当日清单。讲次内核仍是
``lecture_done``（``_auto_done`` 已改走它）；阅读达成态和背词配额
这些「键上的完成规则」以前写在 ``today_tasks`` 的循环里，现在只留
这一处。
"""
from __future__ import annotations

from typing import Optional

from .util import _task_key


class CompletionMixin:
    """PlanStore 回答「学生 X 在日 D 哪些计划键完成了」。"""

    def day_completion(
        self,
        user: str,
        day: str,
        tasks: Optional[list] = None,
        *,
        historical: bool = False,
    ) -> dict:
        """返回 ``{date, keys, read_titles, acts, signals}``。

        ``keys`` 是 ``{task_key: {"done": bool, "why": str}}``。
        ``tasks`` 为当日派生清单（含配额、书名）；缺省时只回活动信号键。
        ``historical`` 时清单行一律完成（历史回填快照），实录仍按日算。
        """
        rows = list(tasks or [])
        planned = {
            t.get("article_id") for t in rows if t.get("article_id") is not None
        }
        auto, read_titles, acts = self._signals_and_acts(user, day, planned)
        keys: dict[str, dict] = {}
        if historical:
            for t in rows:
                keys[_task_key(t)] = {"done": True, "why": "history"}
        else:
            for t in rows:
                key = _task_key(t)
                keys[key] = self._why_done(t, key, auto, read_titles, acts)
            for key, on in auto.items():
                keys.setdefault(
                    key,
                    {"done": bool(on), "why": "signal" if on else "no_signal"},
                )
        return {
            "date": day,
            "keys": keys,
            "read_titles": read_titles,
            "acts": acts,
            "signals": auto,
        }

    def _why_done(
        self,
        task: dict,
        key: str,
        auto: dict,
        read_titles: list,
        acts: dict,
    ) -> dict:
        """单条派生任务的完成原因。规则与原先 today_tasks 循环一致。"""
        if task.get("type") == "reading":
            text = task.get("text") or ""
            # 百分比目标文案是「目标已达成」；按章目标达成文案是「已读完」。
            # 两句都是后端已经算好的达成态，只认其中一句会让按章读完的书
            # 当天没有会话时一直灰着。
            if "目标已达成" in text or "已读完" in text:
                return {"done": True, "why": "goal_reached"}
            words = self._key_words(task.get("book") or "")
            matched = bool(words) and any(
                all(w in (title or "").lower() for w in words)
                for title in read_titles
            )
            if matched:
                return {"done": True, "why": "session_match"}
            return {"done": False, "why": "not_read"}
        if task.get("type") == "vocab" and task.get("quota"):
            met = acts.get("vocab_n", 0) >= int(task["quota"])
            return {"done": met, "why": "quota_met" if met else "below_quota"}
        on = bool(auto.get(key, False))
        return {"done": on, "why": "signal" if on else "no_signal"}

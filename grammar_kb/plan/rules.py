"""完成判定规则开关，以及讲次完成内核（成绩 ∪ 自学专题）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .. import dbreg
from ..clock import now_cn
from .schema import RULES, SCHEMA
class RulesMixin:

    # ---- 完成判定规则（注册表 + 按学生开关） ----

    def _flag(self, user: str, rule_id: str) -> int:
        """读某学生某规则的开关值（缺行=注册表默认）。

        只读：plan_rule_flags 由 PlanStore.__init__ 的 SCHEMA 保证存在
        （懒补写逻辑保留在 set_rule 写路径）。原先每次读都 executescript
        ——建表语句走一遍还落一个写事务，week_view 一次要开几十次。
        """
        for r in RULES:
            if r["id"] == rule_id:
                dft = int(r["default"])
                break
        else:
            return 0
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM plan_rule_flags"
                    " WHERE user = ? AND rule_id = ?",
                    (user, rule_id)).fetchone()
                return int(row["value"]) if row else dft
        except sqlite3.Error:
            return dft


    def get_rules(self, user: str) -> list[dict]:
        """规则清单（含当前值）——教师版配置面板与前端展示共用。"""
        return [dict(r, value=self._flag(user, r["id"])) for r in RULES]


    def set_rule(self, user: str, rule_id: str, value: int) -> Optional[dict]:
        """教师改某学生某规则；未知规则返回 None。"""
        if not any(r["id"] == rule_id for r in RULES):
            return None
        v = 1 if value else 0
        now = now_cn().replace(tzinfo=None).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO plan_rule_flags"
                " (user, rule_id, value, updated_at) VALUES (?,?,?,?)",
                (user, rule_id, v, now))
        return next(dict(r, value=v) for r in RULES if r["id"] == rule_id)


    # ---- 讲次完成判定内核：成绩 ∪ 自学专题（全系统唯一定义处） ----
    # 之前同一问题（第 N 讲完成了吗）散在 _auto_done / week_daily_tasks /
    # _week_goal_progress / _completion_status 四处各写一套，口径漂移是
    # 「计划表老不对」的根源。现在全部改走这里。

    def _exam_done_lectures(self, lo: Optional[str], hi: Optional[str],
                            user: str, kind: str = "lecture") -> set[int]:
        """某窗口内（None=不限时）有成绩登记的讲次集合。
        kind: lecture=课程测验 / hw=作业卷。按学生过滤（可关）。"""
        path = self._exam_db()
        if not path or not Path(path).exists():
            return set()
        scope = self._flag(user, "exam_user_scope")
        try:
            with dbreg.connect(path, readonly=True) as conn:
                cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(exam_records)")}
                kind_col = ("COALESCE(kind, 'lecture')" if "kind" in cols
                            else "'lecture'")
                sql = (f"SELECT DISTINCT lecture FROM exam_records"
                       f" WHERE {kind_col} = ?")
                args: list = [kind]
                if "user" in cols and scope:
                    sql += " AND user = ?"
                    args.append(user)
                if lo is not None:
                    sql += " AND date >= date(?)"
                    args.append(lo)
                if hi is not None:
                    sql += " AND date < date(?)"
                    args.append(hi)
                return {r[0] for r in conn.execute(sql, args)}
        except sqlite3.Error:
            return set()


    def lecture_done(self, lo: Optional[str], hi: Optional[str], user: str,
                     kind: str = "lecture") -> set[int]:
        """讲次完成集合（唯一口径）：成绩登记 ∪ 自学专题学完（后者仅
        lecture 口径且规则开启时）。hw 口径只认作业卷成绩。"""
        out = self._exam_done_lectures(lo, hi, user, kind)
        if kind == "lecture" and self._flag(user, "topic_counts_lecture"):
            out |= self._topic_done_lectures(lo or "0000-01-01",
                                             hi or "9999-12-31", user)
        return out

"""学情分析 · AI 周报。

手动触发：每次分析「上一周」（自然周，周一 00:00 — 周日 24:00；若触发当天
恰为周日，本周已收官，则分析本周）。聚合四类作业数据（哈一作业成绩 / FCE
练习 / 阅读朗读 / 背单词）+ 前四周趋势基线，交给泛读馆设置里配置的智谱
GLM 模型生成 Markdown 分析报告，按周存档可反复查看。

数据源复用 server.py 已装配的各 store 实例（与批改中心/学情分析页同源）；
报告表挂 exam.db（学情数据本就住那）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional

from . import glm

REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_weekly_reports (
    week_key   TEXT PRIMARY KEY,   -- 周一日期 YYYY-MM-DD
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,      -- 周日日期
    model      TEXT,
    content    TEXT NOT NULL,      -- Markdown 报告
    stats_json TEXT,               -- 生成时的聚合数据快照
    created_at TEXT NOT NULL
)
"""

SYSTEM_PROMPT = (
    "你是一位负责中国初中生英语学习的学情分析师，服务对象是一位对孩子要求务实、"
    "看重可执行建议的家长（也是你的用户）。你会收到一份 JSON：上周四类作业的明细与汇总，"
    "以及前四周的基线数据。请输出一篇 Markdown 周报，直接以正文开始，不要寒暄。结构：\n"
    "## 总体态势\n2-3 句话：上周学习量与成绩相对基线是升是降，节奏是否稳定。\n"
    "## 亮点\n上周做得好的具体事（引用讲次/正确率/分数），1-3 条。\n"
    "## 薄弱环节\n数据支撑的弱点（低分讲次、反复错的题、正确率下滑的题型），1-3 条，"
    "没有就明说没有。\n"
    "## 下周建议\n3 条以内、可直接执行的安排（如「复刷第 X 讲」「每天一组单词」），"
    "避免空话。\n"
    "要求：所有结论必须基于给出的数据，不编造；数字引用要准确；"
    "语气客观简洁，篇幅 400 字以内。"
)


def last_week_window(today: Optional[date] = None) -> tuple[date, date]:
    """上一自然周（周一→周日）。今天若是周日，本周刚收官，返回本周。

    返回 (start, end) 且 start 恒为周一、end 恒为周日。
    """
    d = today or date.today()
    # 周一=0 … 周日=6
    monday = d - timedelta(days=d.weekday())
    if d.weekday() == 6:  # 周日：本周数据已完整
        return monday, d
    return monday - timedelta(days=7), monday - timedelta(days=1)


def _week_bounds(monday: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(monday, time.min),
        datetime.combine(monday + timedelta(days=7), time.min),
    )


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """时间戳解析（容错）：ISO 时间 / 纯日期 / 带 Z 或 +00:00 偏移（统一转 naive）。"""
    if not ts:
        return None
    raw = str(ts).strip()
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            t = datetime.combine(date.fromisoformat(raw[:10]), time.min)
        except ValueError:
            return None
    if t.tzinfo is not None:
        # 库里混存 +00:00 后缀（FCE/背单词）与 naive（本地 now），统一按本地墙钟比较
        t = t.astimezone().replace(tzinfo=None)
    return t


def _week_key_of(iso_like: str) -> Optional[str]:
    """YYYY-MM-DD（或含时间）→ 所在周周一日期；解析失败返回 None。"""
    try:
        d = date.fromisoformat((iso_like or "")[:10])
    except ValueError:
        return None
    return (d - timedelta(days=d.weekday())).isoformat()


class AnalyticsAI:
    def __init__(self, exam_db_path: str | Path):
        self.db_path = str(exam_db_path)
        with self._conn() as con:
            con.execute(REPORT_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    # ---- 数据聚合 ----

    def collect_stats(
        self,
        exams: Any,
        fce_submissions: Any,
        reading: Any,
        recite: Any,
        window: tuple[date, date],
    ) -> dict:
        """聚合 [start, end] 闭区间一周的四类数据 + 前四周基线。"""
        start, end = window
        lo, hi = _week_bounds(start)  # hi 为下周一 0 点（开区间上界）

        def in_week(ts: Optional[str]) -> bool:
            t = _parse_ts(ts)
            return t is not None and lo <= t < hi

        exam_rows = [r for r in exams.list() if in_week(r.get("date"))]
        fce_rows = [r for r in fce_submissions.list(limit=1000) if in_week(r.get("created_at"))]
        rec_rows = [r for r in reading.list_recordings(limit=1000) if in_week(r.get("created_at"))]
        recite_rows = [r for r in recite.list(limit=1000) if in_week(r.get("created_at"))]

        def _avg(xs: list[float]) -> Optional[float]:
            return round(sum(xs) / len(xs), 1) if xs else None

        fce_auto = [r for r in fce_rows if r.get("status") == "auto"]
        rec_graded = [r for r in rec_rows if r.get("status") == "graded"]

        week = {
            "range": f"{start.isoformat()} ~ {end.isoformat()}",
            "exams": {
                "count": len(exam_rows),
                "avg": _avg([float(r["score"]) for r in exam_rows]),
                "items": [
                    {"lecture": r["lecture"], "date": r["date"], "score": r["score"],
                     "wrong": r.get("wrong") or []}
                    for r in exam_rows
                ],
            },
            "fce": {
                "count": len(fce_rows),
                "auto_acc_pct": _avg([
                    round(r["auto_score"] / r["total"] * 100)
                    for r in fce_auto if r.get("total")
                ]),
                "graded": [
                    {"test": f"T{r['test_id']}P{r['part']}", "teacher_score": r.get("teacher_score")}
                    for r in fce_rows if r.get("status") == "graded"
                ],
                "items": [
                    {"test": f"T{r['test_id']}P{r['part']}", "paper": r.get("paper"),
                     "status": r["status"], "auto": r.get("auto_score"), "total": r.get("total"),
                     "at": (r.get("created_at") or "")[:10]}
                    for r in fce_rows
                ],
            },
            "reading_aloud": {
                "count": len(rec_rows),
                "avg_score_10": _avg([
                    float(r["teacher_score"]) for r in rec_graded
                ]),
                "items": [
                    {"title": (r.get("article_title") or f"#{r.get('article_id')}")[:24],
                     "status": r["status"], "score": r.get("teacher_score"),
                     "duration_sec": r.get("duration_sec"), "at": (r.get("created_at") or "")[:10]}
                    for r in rec_rows
                ],
            },
            "recite": {
                "count": len(recite_rows),
                "avg_acc_pct": _avg([float(r["acc"]) for r in recite_rows]),
                "total_word_attempts": sum(r.get("total") or 0 for r in recite_rows),
                "items": [
                    {"scope": r.get("scope"), "mode": r.get("mode"), "acc": r.get("acc"),
                     "total": r.get("total"), "wrong": r.get("wrong"),
                     "at": (r.get("created_at") or "")[:10]}
                    for r in recite_rows
                ],
            },
        }

        # 前四周基线（不含本周）：给模型做对比
        base = []
        all_exams = exams.list()
        all_fce = fce_submissions.list(limit=1000)
        all_recite = recite.list(limit=1000)
        for i in range(1, 5):  # 紧邻前 4 周（start-7 … start-28）
            mon = start - timedelta(days=7 * i)
            b_lo, b_hi = _week_bounds(mon)

            def _in(ts: Optional[str]) -> bool:
                t = _parse_ts(ts)
                return t is not None and b_lo <= t < b_hi
            exs = [r for r in all_exams if _in(r.get("date"))]
            fcs = [r for r in all_fce if _in(r.get("created_at")) and r.get("status") == "auto"]
            rts = [r for r in all_recite if _in(r.get("created_at"))]
            base.append({
                "week": mon.isoformat(),
                "exams": len(exs),
                "exam_avg": _avg([float(r["score"]) for r in exs]),
                "fce_auto_acc": _avg([
                    round(r["auto_score"] / r["total"] * 100) for r in fcs if r.get("total")
                ]),
                "recite_acc": _avg([float(r["acc"]) for r in rts]),
                "recite_count": len(rts),
            })
        base.reverse()  # 时间正序

        return {"week": week, "baseline_prev4": base}

    # ---- 生成与存档 ----

    def generate_weekly(
        self,
        library_settings: dict,
        stats: Optional[dict] = None,
        window: Optional[tuple[date, date]] = None,
        stores: Optional[dict] = None,
    ) -> dict:
        """读取 library ai 配置 → 聚合上周 → 调 GLM → 存档并返回报告。

        stats/stores 二选一传入（stores 需含 exams/fce_submissions/reading/recite）。
        """
        ai = (library_settings or {}).get("ai") or {}
        api_key = (ai.get("apiKey") or "").strip()
        if not api_key:
            raise glm.GlmError("尚未配置 AI：请在教师版「泛读馆设置 → AI 学习」填写智谱 API Key", 400)
        model = ai.get("model") or "glm-4-flash"

        win = window or last_week_window()
        data = stats or self.collect_stats(
            stores["exams"], stores["fce_submissions"], stores["reading"], stores["recite"], win
        )

        user_prompt = (
            f"分析对象：{data['week']['range']}（上周）的作业数据。\n\n"
            f"```json\n{json.dumps(data, ensure_ascii=False, indent=1)[:16000]}\n```"
        )
        content = glm.chat_prose(api_key, model, SYSTEM_PROMPT, user_prompt)

        start, end = win
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO ai_weekly_reports "
                "(week_key, start_date, end_date, model, content, stats_json, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (start.isoformat(), start.isoformat(), end.isoformat(), model,
                 content, json.dumps(data, ensure_ascii=False), now),
            )
        return self.get_report(start.isoformat())

    # ---- 读取 ----

    def _row_out(self, r: sqlite3.Row) -> dict:
        return {
            "weekKey": r["week_key"], "startDate": r["start_date"], "endDate": r["end_date"],
            "model": r["model"], "content": r["content"], "createdAt": r["created_at"],
        }

    def get_report(self, week_key: str) -> Optional[dict]:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM ai_weekly_reports WHERE week_key = ?", (week_key,)
            ).fetchone()
        return self._row_out(row) if row else None

    def list_reports(self, limit: int = 12) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM ai_weekly_reports ORDER BY week_key DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._row_out(r) for r in rows]

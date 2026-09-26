#!/usr/bin/env python3
"""把四目标冲刺计划自动排进 plan_weeks（可重复跑，只补未编辑过的周）。

排程依据（2026-09-26 基线）：
- 课程：malin 学到 28 讲，48 讲全完成 → 每周 1-2 讲，综合复习讲（33/38/43/48）
  单独占一周轻量推进；1 月留 3 周重测+错题清零。
- 词汇：L0-L5 共 6070 词，已掌握 245 → 每周新掌握目标按匀速+前置递增
  （前期低年级词快，后期 L2/L3 词多压速），考试节点（考卷周）标出。
- FCE：8 Test，Test1 从 10 月中开始每 2 周一套；12 月底 Test5-8 入库后
  改整卷模考。
- 阅读：波西1（60%→10 月中 100%）→ HP1（11-12 月）→ Wonder（12 月下-1 月）。

用法：
    uv run python scripts/seed_plan_weeks.py [--dry-run] [--force]
    # --force 覆盖已编辑过的周（默认跳过 updated_at 晚于本脚本首跑的周）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grammar_kb.plan import PlanStore  # noqa: E402

START = date(2026, 9, 28)   # 冲刺起点周一
END = date(2027, 2, 1)      # 最后一个周一（含 1/25-31 收官周）

# (week_start, 课程讲次, 词汇累计目标, FCE 任务, 阅读目标, notes)
# 词汇目标为「累计掌握」绝对值；FCE 为文本列表；阅读为 {书名: 周末目标%}
PLAN: list[tuple[str, dict, str]] = [
    ("2026-09-28", {"lectures": [26, 27, 28], "vocab_goal": 320,
                    "fce": [], "reading": {"Percy": 100}},
     "时态三讲重测到 70+（专题已学完，本周验证）；波西 1 收尾读完；每天 2 组词"),
    ("2026-10-05", {"lectures": [29], "vocab_goal": 450, "fce": [],
                    "reading": {"Harry Potter": 15}},
     "开 29 讲被动语态；HP1 启动每天 25 分钟；周末做 L0 词汇考试（80 分解锁 L1）"),
    ("2026-10-12", {"lectures": [30, 31], "vocab_goal": 580,
                    "fce": ["Test1 RUE P1-P4"], "reading": {"Harry Potter": 30}},
     "FCE Test1 开始分部练（先 RUE 前半）；L0 考试通过后进 L1"),
    ("2026-10-19", {"lectures": [32], "vocab_goal": 710,
                    "fce": ["Test1 RUE P5-P7", "Test1 作文"], "reading": {"Harry Potter": 45}},
     "33 综合复习前收尾 32 讲"),
    ("2026-10-26", {"lectures": [33], "vocab_goal": 840, "fce": ["Test1 听力"],
                    "reading": {"Harry Potter": 60}},
     "综合复习七（33 讲）整卷测；Test1 收尾"),
    ("2026-11-02", {"lectures": [34], "vocab_goal": 970,
                    "fce": ["Test2 RUE 全部"], "reading": {"Harry Potter": 75}},
     "11 月开 34 讲；Test2 一次刷完 RUE"),
    ("2026-11-09", {"lectures": [35, 36], "vocab_goal": 1100,
                    "fce": ["Test2 作文+听力"], "reading": {"Harry Potter": 90}},
     "Test2 收尾；周末 L1 词汇考试（解锁 L2）"),
    ("2026-11-16", {"lectures": [37], "vocab_goal": 1230,
                    "fce": ["Test3 RUE 全部"], "reading": {"Harry Potter": 100}},
     "HP1 本周读完；L2 开背"),
    ("2026-11-23", {"lectures": [38], "vocab_goal": 1360,
                    "fce": ["Test3 作文+听力"], "reading": {"Wonder": 20}},
     "综合复习八（38 讲）；Wonder 启动"),
    ("2026-11-30", {"lectures": [39], "vocab_goal": 1490,
                    "fce": ["Test4 RUE 全部"], "reading": {"Wonder": 35}},
     "12 月冲刺开始；错词本每周清一次"),
    ("2026-12-07", {"lectures": [40, 41], "vocab_goal": 1620,
                    "fce": ["Test4 作文+听力"], "reading": {"Wonder": 50}},
     "41 讲特殊疑问句词量大，匀速推"),
    ("2026-12-14", {"lectures": [42], "vocab_goal": 1750,
                    "fce": ["Test5 整卷模考"], "reading": {"Wonder": 65}},
     "校园版 Test5-8 上线；Test5 整卷计时模考（RUE+听力+作文）"),
    ("2026-12-21", {"lectures": [43], "vocab_goal": 1880,
                    "fce": ["Test6 整卷模考"], "reading": {"Wonder": 80}},
     "综合复习九（43 讲）；周末 L2 词汇考试（解锁 L3）"),
    ("2026-12-28", {"lectures": [44, 45], "vocab_goal": 2010,
                    "fce": ["错题复盘"], "reading": {"Wonder": 100}},
     "元旦假期集中冲课；Wonder 读完；FCE 做已做卷的错题回顾"),
    ("2027-01-04", {"lectures": [46, 47], "vocab_goal": 2140,
                    "fce": ["Test7 整卷模考"], "reading": {}},
     "46-47 连推，为 48 讲收官留整周"),
    ("2027-01-11", {"lectures": [48], "vocab_goal": 2270,
                    "fce": ["Test8 整卷模考"], "reading": {}},
     "48 讲综合复习十=完课测；Test8 模考；周末 L3 词汇考试（解锁 L4）"),
    ("2027-01-18", {"lectures": [], "vocab_goal": 2420,
                    "fce": ["FCE 全卷错题清零"], "reading": {}},
     "薄弱讲重测到 70+；顽固错题全部复练"),
    ("2027-01-25", {"lectures": [], "vocab_goal": 2545,
                    "fce": ["查漏补缺"], "reading": {}},
     "收官周：L4 考试（解锁 L5）→ L5 254 词一周背完；四目标复盘"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fce-db", default=None, help="fce.db 路径（默认走 PlanStore 解析）")
    ap.add_argument("--exam-db", default=None, help="exam.db 路径（进度聚合用）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="覆盖已有周（默认跳过）")
    args = ap.parse_args()

    store = PlanStore(args.fce_db, exam_db_path=args.exam_db)
    existing = {w["week_start"]: w for w in store.list_weeks()}

    # 周表覆盖校验：从 START 到 END 每周一都要有
    want = []
    d = START
    while d <= END:
        want.append(d.isoformat())
        d += timedelta(days=7)
    have = {ws for ws in dict.fromkeys([p[0] for p in PLAN])}
    missing = [w for w in want if w not in have]
    if missing:
        print(f"警告：周表缺口 {missing}（按 PLAN 现有周执行）")

    n_written = n_skip = 0
    for week_start, tasks, notes in PLAN:
        if week_start in existing and not args.force:
            n_skip += 1
            continue
        if args.dry_run:
            print(f"[dry] {week_start}: {tasks.get('lectures')} 讲 / 词{tasks.get('vocab_goal')}"
                  f" / {tasks.get('fce') or '—'} / {notes[:18]}")
            n_written += 1
            continue
        store.put_week(week_start, tasks, notes)
        n_written += 1
    print(f"完成：写入 {n_written} 周，跳过已有 {n_skip} 周"
          f"{'（dry-run 未落库）' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""计划表 SQLite schema 与完成判定规则注册表。

规则是单一事实源：后端各判定路径读它执行，教师看板读它展示。
改判定逻辑先在 RULES 立项（写明人话规则）。
"""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_weeks (
    user       TEXT NOT NULL DEFAULT 'malin',  -- 周计划归属学生
    week_start TEXT NOT NULL,      -- 周一日期 YYYY-MM-DD
    tasks      TEXT NOT NULL DEFAULT '{}',  -- 结构化任务 JSON
    notes      TEXT DEFAULT '',    -- 改善项/备注（自由文本）
    updated_at TEXT,
    PRIMARY KEY (user, week_start)
);
-- 教师干预留痕。不参与 today_tasks.done，也不参与完成率。
CREATE TABLE IF NOT EXISTS plan_day_checks (
    user       TEXT NOT NULL,
    date       TEXT NOT NULL,        -- YYYY-MM-DD
    task_key   TEXT NOT NULL,        -- micro:26 / wrong_words / lecture:29 / fce / reading:书名
    checked_at TEXT,
    PRIMARY KEY (user, date, task_key)
);
CREATE TABLE IF NOT EXISTS plan_goals (
    user       TEXT PRIMARY KEY,
    goals      TEXT NOT NULL DEFAULT '{}',  -- 总目标 JSON（见 _goals_default）
    updated_at TEXT
);
-- 可选回填快照，不是每日完成流水。无记录的日期按 day_completion 信号计完成。
CREATE TABLE IF NOT EXISTS plan_history_days (
    user       TEXT NOT NULL,
    date       TEXT NOT NULL,
    tasks      TEXT NOT NULL DEFAULT '[]',
    created_at TEXT,
    PRIMARY KEY (user, date)
);
CREATE TABLE IF NOT EXISTS plan_rule_flags (
    user       TEXT NOT NULL,        -- 规则按学生配置
    rule_id    TEXT NOT NULL,
    value      INTEGER NOT NULL DEFAULT 1,   -- 1=开 0=关
    updated_at TEXT,
    PRIMARY KEY (user, rule_id)
);
"""

# 完成判定规则注册表：单一事实源——后端各判定路径读它执行，
# 前端「计划看板 · 完成判定规则」卡读它展示/配置。改判定逻辑必须
# 先在这里立项（写明人话规则），杜绝同一问题多处各写一套口径。
RULES: list[dict] = [
    {"id": "topic_counts_lecture", "name": "专题学完＝讲次完成", "default": 1,
     "desc": "计划讲次若配有自学专题（如「第29讲·被动语态」），专题全部知识点"
             "完成后即视为该讲「学+测」完成，无需等卷面成绩"},
    {"id": "speak_early_credit", "name": "朗读提前完成即点亮", "default": 1,
     "desc": "精读朗读默认排在周六；周中任意一天提交录音即算该篇完成，"
             "当天清单显示 ✓"},
    {"id": "zombie_filter", "name": "剔除泛读挂机会话", "default": 1,
     "desc": "无轨迹、无交互、无位移的阅读会话（开着书页人不在）不计入"
             "有效学习时长与泛读词数"},
    {"id": "exam_user_scope", "name": "成绩只认该学生名下", "default": 1,
     "desc": "完成判定只统计归属为该学生的成绩登记（教师替孩子登记时"
             "请选对「学生」）；关闭则不过滤归属（兼容遗留数据）"},
]

# tasks JSON 结构（全部字段可缺省）：
# {
#   "lectures": [29, 30],        # 本周计划学/测的讲次
#   "vocab_goal": 320,           # 本周目标掌握词数（绝对值，非增量）
#   "fce": [["Test1","P1"], ...] # 简化为 ["Test1 P1", "Test2 RUE"] 文本即可
#   "reading": {"波西杰克逊1": 100} # 书名 → 周末目标进度 %
#   "videos": ["xxxxxxxxxxx", ...]  # 本周安排的视频 vid；提交复述即完成
# }

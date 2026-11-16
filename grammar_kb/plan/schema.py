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
    {"id": "speak_early_credit", "name": "朗读记录只留在日志", "default": 1,
     "desc": "精读已结束，课表和计划表不再排新的朗读篇目。以前交过的录音"
             "仍在学习日志里，不删除。这个开关不再往课表加任务。"},
    {"id": "zombie_filter", "name": "剔除泛读挂机会话", "default": 1,
     "desc": "无轨迹、无交互、无位移的阅读会话（开着书页人不在）不计入"
             "有效学习时长与泛读词数"},
    {"id": "exam_user_scope", "name": "成绩只认该学生名下", "default": 1,
     "desc": "完成判定只统计归属为该学生的成绩登记（教师替孩子登记时"
             "请选对「学生」）；关闭则不过滤归属（兼容遗留数据）"},
    {"id": "boost_gate_lecture", "name": "提升档过关才算讲次完成", "default": 0,
     "desc": "开启后：讲次完成 = 成绩登记 ∪ 专题学完 ∪ 该讲当周提升档过关"},
]

# tasks JSON 结构（全部字段可缺省）：
# {
#   "lectures": [29, 30],        # 本周计划学/测的讲次
#   "vocab_lv": 1,               # 本周词汇看哪一级；缺省=不按级过滤（旧周）
#   "vocab_goal": 140,           # 该级累计目标。进度分母 = goal − 周初已会
#   "vocab_new": 40,             # 老师填的「本周新掌握」个数（= 保存时的分母）
#   "vocab_pct": 14,             # 兼容旧界面的百分比，编辑器不再让人填
#   "fce": [["Test1","P1"], ...] # 简化为 ["Test1 P1", "Test2 RUE"] 文本即可
#   "reading": {"波西杰克逊1": 100} # 书名 → 本周目标进度 %
#   "videos": ["xxxxxxxxxxx", ...]  # 旧字段：平铺 vid，由一次性脚本转成 video_units
#   "article_units": [{"ids": [58], "mode": "daily" | "weekend"}],
#                                   # 历史精读分组，只保留不排课（0.3.0 起课表不再出
#                                   # 精读行）。编辑器不再新增；保存时原样留在 JSON 里。
#                                   # mode 不再表示周末/工作日。
#   "video_units":   [{"vids": ["abc", "def"], "mode": "daily" | "weekend"}],
#                                   # 视频分组。mode 是旧字段，排课忽略：全部视频
#                                   # 合成一条队列，每天 1 个，周一到周日一样。
#   "nce2_units":    [{"lessons": [1, 2], "mode": "daily" | "weekend",
#                     "task_mode": "listen" | "shadow"}],
#                                   # NCE2 听力分组（nce2_units 是唯一来源）。
#                                   # mode 忽略，不丢课。每天队首 1 课。
#                                   # task_mode 缺省 shadow（跟读）；listen=精听。
#   "micro": 1                      # 微练习开关：开启后该周周一到周日各一次打卡。
#                                   # 不排进其他作业的完成率分母。过了北京日不能补。
#   "boost_days": [2, 4],           # 旧字段：保存时原样留下，排课和完成都不再读。
#   "boost_items": [                # 提升档任务（每天一件难事，v3.0.0 设计 §2.3）：
#     {"type": "memory",            #   A) 专题节点口诀卡翻面自评
#      "topic_id": "lecture-30-passive-2", "node_key": "k2"} |
#     {"type": "retell", "pattern_id": 12}   #   B) 句型跟读复述
#   ]
# }

"""主题讲义加载器：数据在 ``data/theme_notes.json``（以中学教师视角为每个主题写的结构化总结）。

原则：简洁、好记、有例子（例句由 /taxonomy 从教材语料自动提取，
尽力使用教材原文，见 Query.taxonomy 的 examples 字段）。

统一讲解形式（JSON 字段）：
  summary  一句话定位（这个主题考什么）
  points   核心规则条目（3-6 条，每条一行，公式化表述）
  formula  构成公式（可直接背）
  mnemonic 记忆口诀（可选）
  tips     常考易错点（1-3 条）

此前 556 行数据内嵌在本模块；数据出代码后迁到 data/，这里只负责
加载并适配成消费方（query.py）使用的 ``(group, theme) → note`` 形态。
"""
from __future__ import annotations

import json

from .paths import data_path


def _load() -> dict[tuple[str, str], dict]:
    """data/theme_notes.json → {(词法类, 主题): 讲义 dict}。"""
    try:
        raw = json.loads(data_path("theme_notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {(item["group"], item["theme"]): item for item in raw.get("themes", [])}


THEME_NOTES: dict[tuple[str, str], dict] = _load()

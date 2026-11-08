"""句型条目（fce.db 同库）：提升档「句型跟读复述」的素材表。

设计意图：
- pattern_item 存讲义卷句型（sentence=英文原句、translation=中文、
  hints=复述脚手架问题），教师经 /patterns-admin/import 从
  data/patterns/*.json 批量导入；同 lecture+sentence 已存在则跳过
  （幂等，重导不产生重复行）。
- 提交链路复用 video_retell（ref_kind='pattern'），见 video_study.py；
  本模块只管素材的增查，不管录音。
- 连接即用即关（dbreg），与 plan 包一致；schema 每个库路径进程内
  执行一次（CREATE TABLE IF NOT EXISTS 幂等）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone as _tz
from typing import Optional

from . import dbreg

SCHEMA = """
CREATE TABLE IF NOT EXISTS pattern_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture     INTEGER NOT NULL,
    sentence    TEXT NOT NULL,
    translation TEXT DEFAULT '',
    hints_json  TEXT DEFAULT '[]',
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pattern_item_lecture ON pattern_item(lecture);
"""

# 已建过表的库路径（模块级去重；换库路径会重新建，测试隔离库互不影响）
_inited: set[str] = set()


def _now() -> str:
    return datetime.now(_tz.utc).isoformat(timespec="seconds")


def _ensure_schema() -> None:
    path = dbreg.fce_db_path()
    if path in _inited:
        return
    with dbreg.connect(path) as conn:
        conn.executescript(SCHEMA)
    _inited.add(path)


def _out(r) -> dict:
    try:
        hints = json.loads(r["hints_json"] or "[]")
    except (ValueError, TypeError):
        hints = []
    return {
        "id": r["id"],
        "lecture": r["lecture"],
        "sentence": r["sentence"],
        "translation": r["translation"] or "",
        "hints": hints if isinstance(hints, list) else [],
    }


def get(item_id: int) -> Optional[dict]:
    """单个句型（按 id）；不存在返回 None。"""
    _ensure_schema()
    with dbreg.connect(dbreg.fce_db_path()) as conn:
        row = conn.execute(
            "SELECT * FROM pattern_item WHERE id = ?", (int(item_id),)).fetchone()
    return _out(row) if row else None


def list_all(lecture: Optional[int] = None) -> list[dict]:
    """句型清单（可按讲次过滤），按讲次、导入顺序排。"""
    _ensure_schema()
    sql = "SELECT * FROM pattern_item"
    args: list = []
    if lecture is not None:
        sql += " WHERE lecture = ?"
        args.append(int(lecture))
    sql += " ORDER BY lecture, id"
    with dbreg.connect(dbreg.fce_db_path()) as conn:
        return [_out(r) for r in conn.execute(sql, args).fetchall()]


def import_items(items: list[dict]) -> int:
    """批量导入句型，返回实际入库条数。

    校验：lecture 须为 1-99 整数、sentence 非空（两项必填）、hints 须为
    字符串数组（可缺省）；任一条不合法整批拒绝（ValueError，路由层转 422），
    与专题 JSON 导入的「先校验后写入」同一口径。幂等：同 lecture+sentence
    已存在则跳过。
    """
    _ensure_schema()
    cleaned: list[tuple[int, str, str, str]] = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            raise ValueError(f"items[{i}] 须为对象")
        lecture = it.get("lecture")
        if isinstance(lecture, bool) or not isinstance(lecture, int) \
                or not 1 <= lecture <= 99:
            raise ValueError(f"items[{i}].lecture 须为 1-99 的整数")
        sentence = str(it.get("sentence") or "").strip()
        if not sentence:
            raise ValueError(f"items[{i}].sentence 不能为空")
        hints = it.get("hints") or []
        if not isinstance(hints, list) \
                or not all(isinstance(h, str) for h in hints):
            raise ValueError(f"items[{i}].hints 须为字符串数组")
        cleaned.append((
            lecture,
            sentence[:500],
            str(it.get("translation") or "").strip()[:500],
            json.dumps([h.strip()[:200] for h in hints if h.strip()],
                       ensure_ascii=False),
        ))
    n = 0
    with dbreg.connect(dbreg.fce_db_path()) as conn:
        for lecture, sentence, translation, hints_json in cleaned:
            dup = conn.execute(
                "SELECT 1 FROM pattern_item WHERE lecture = ? AND sentence = ?",
                (lecture, sentence)).fetchone()
            if dup:
                continue
            conn.execute(
                "INSERT INTO pattern_item (lecture, sentence, translation,"
                " hints_json, created_at) VALUES (?,?,?,?,?)",
                (lecture, sentence, translation, hints_json, _now()),
            )
            n += 1
    return n

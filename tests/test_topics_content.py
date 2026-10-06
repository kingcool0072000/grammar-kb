"""v2 结构化专题：内容导入/展开行为。

覆盖：JSON 文件导入校验通过、题目全部自含展开、qs_label 透传到展开结果
（前端练习区标题）。

内容政策（2026-10-05 用户定调）：作业卷是对孩子的考试，专题里不允许出现
作业卷真题——homework_question 表即作业卷题库，因此 ref 引用必须为 0；
题目只允许讲义卷原题 / 基于讲义的自拟拓展。
"""
from __future__ import annotations

import json
from pathlib import Path

from grammar_kb.topics import TopicStore

_PROJECT = Path(__file__).resolve().parent.parent
_TOPIC_JSON = _PROJECT / "data" / "topics" / "lecture-29-passive-1.json"
_GRAMMAR_DB = _PROJECT / "data" / "grammar.db"


def _store(tmp_path):
    return TopicStore(db_path=str(tmp_path / "fce.db"),
                      grammar_db_path=str(_GRAMMAR_DB))


def test_import_lecture29_json_and_expand(tmp_path):
    data = json.loads(_TOPIC_JSON.read_text(encoding="utf-8"))
    store = _store(tmp_path)
    got = store.upsert_topic(data["meta"], data["content"])
    assert got["topic_id"] == "lecture-29-passive-1"
    keys = [n["key"] for n in got["nodes"]]
    assert keys == ["what", "transitive", "steps", "tenses", "usage", "final"]

    all_qs = [q for n in got["nodes"] for q in n["general_qs"]]
    # 内容政策：不允许 ref 引用作业卷题库（homework_question），题目全部自含
    refs = [q for q in all_qs if q["kind"] == "ref"]
    assert refs == [], f"专题不得引用作业卷题库，发现 {len(refs)} 道 ref"
    # 自含题必须有题干与答案
    assert all(q["stem"] and q["answer"] for q in all_qs)

    # qs_label 透传（不丢字段）
    labels = {n["key"]: n["qs_label"] for n in got["nodes"]}
    assert labels["what"] == "热身自测（先想再点开）"
    assert all(labels.values()), "每个节点的练习区标题都应有值"

    # 总题量：6 关共 43 题
    total = sum(len(n["wrong_qs"]) + len(n["general_qs"])
                for n in got["nodes"])
    assert total == 43


def test_student_visibility_and_progress(tmp_path):
    data = json.loads(_TOPIC_JSON.read_text(encoding="utf-8"))
    store = _store(tmp_path)
    store.upsert_topic(data["meta"], data["content"])
    # assigned_to=malin：malin 可见，别的学生不可见
    ids = [t["topic_id"] for t in store.list_topics(viewer="malin", role="student")]
    assert "lecture-29-passive-1" in ids
    ids_other = [t["topic_id"] for t in store.list_topics(viewer="mxy", role="student")]
    assert "lecture-29-passive-1" not in ids_other
    # 进度落行
    rec = store.put("malin", "lecture-29-passive-1", ["what", "steps"],
                    assigned_user="malin")
    assert rec["done_keys"] == ["steps", "what"]
    assert rec["completed_at"]

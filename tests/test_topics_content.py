"""v2 结构化专题：内容导入/展开行为。

覆盖：JSON 文件导入校验通过、ref 引用题库能展开出题干与选项、
qs_label 透传到展开结果（前端练习区标题）。
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
    assert keys == ["what", "transitive", "steps", "tenses", "usage", "exam"]

    # ref 引用题必须从 grammar.db 题库展开出题干与答案（lecture=29 均有货）
    refs = [q for n in got["nodes"] for q in n["general_qs"]
            if q["kind"] == "ref"]
    assert refs, "应存在题库引用题"
    for q in refs:
        assert q["stem"], f"第{q['lecture']}讲第{q['qnum']}题未展开出题干"
        assert q["answer"], f"第{q['lecture']}讲第{q['qnum']}题无答案"

    # 选择题引用展开出选项（前端 qBlock 渲染 A/B/C/D 用）
    mc = [q for q in refs if q["options"]]
    assert mc and all(len(q["options"]) >= 2 for q in mc)

    # 自含题原样透传（选项/解析/答案）
    self_qs = [q for n in got["nodes"] for q in n["general_qs"]
               if q["kind"] == "self"]
    assert self_qs and all(q["stem"] for q in self_qs)

    # qs_label 透传（不丢字段）
    labels = {n["key"]: n["qs_label"] for n in got["nodes"]}
    assert labels["what"] == "热身自测（先想再点开）"
    assert all(labels.values()), "每个节点的练习区标题都应有值"

    # 总题量：6 关共 56 题
    total = sum(len(n["wrong_qs"]) + len(n["general_qs"])
                for n in got["nodes"])
    assert total == 56


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

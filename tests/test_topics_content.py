"""v2 结构化专题：内容导入/展开行为。

覆盖：JSON 文件导入校验通过、题目全部自含展开、qs_label 透传到展开结果
（前端练习区标题）。

内容政策（2026-10-05 用户定调）：作业卷是对孩子的考试，专题里不允许出现
作业卷真题——homework_question 表即作业卷题库，因此 ref 引用必须为 0；
题目只允许讲义卷原题 / 基于讲义的自拟拓展。
"""
from __future__ import annotations

import json
import sqlite3
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

    listed = store.list_topics(viewer="teacher", role="teacher")
    row = next(t for t in listed if t["topic_id"] == "lecture-29-passive-1")
    assert row["node_keys"] == ["final", "steps", "tenses", "transitive", "usage", "what"]
    assert row["node_count"] == len(row["node_keys"])


def test_student_visibility_and_progress(tmp_path):
    data = json.loads(_TOPIC_JSON.read_text(encoding="utf-8"))
    store = _store(tmp_path)
    store.upsert_topic(data["meta"], data["content"])
    # assigned_to=malin：malin 可见，别的学生不可见
    ids = [t["topic_id"] for t in store.list_topics(viewer="malin", role="student")]
    assert "lecture-29-passive-1" in ids
    ids_other = [t["topic_id"] for t in store.list_topics(viewer="mxy", role="student")]
    assert "lecture-29-passive-1" not in ids_other
    # 进度落行：只做了一部分不算完成
    rec = store.put("malin", "lecture-29-passive-1", ["what", "steps"],
                    assigned_user="malin")
    assert rec["done_keys"] == ["steps", "what"]
    assert rec["completed_at"] == ""
    all_keys = ["what", "transitive", "steps", "tenses", "usage", "final"]
    done = store.put("malin", "lecture-29-passive-1", all_keys,
                     assigned_user="malin")
    assert done["completed_at"]
    stamp = done["completed_at"]
    again = store.put("malin", "lecture-29-passive-1", all_keys,
                      assigned_user="malin")
    assert again["completed_at"] == stamp
    partial = store.put("malin", "lecture-29-passive-1", all_keys[:-1],
                        assigned_user="malin")
    assert partial["completed_at"] == ""
    redone = store.put("malin", "lecture-29-passive-1", all_keys,
                       assigned_user="malin")
    # 清空后再盖满会重新记时间（同一秒内两次 now 可能相同，只要求非空）
    assert redone["completed_at"]


def test_shared_assignment_progress_shows_on_list(tmp_path):
    """开放给 malin,mxy 时，详情能读到的 done_keys 列表也要能读到。

    旧前端把整份名单写进 assigned_user，列表按整串精确匹配会丢掉这行，
    详情仍返回 3 个 key，学生就看到详情 3/N、列表 0/N。
    """
    store = _store(tmp_path)
    rec = store.put(
        "malin", "tense-22-28", ["n3", "n1", "n2"],
        assigned_user="malin,mxy",
    )
    assert rec["done_keys"] == ["n1", "n2", "n3"]
    assert rec["assigned_user"] == "malin"
    listed = store.list_for("malin", "student")
    assert [r["topic_id"] for r in listed] == ["tense-22-28"]
    assert listed[0]["done_keys"] == store.get("malin", "tense-22-28")["done_keys"]

    # 库里已经是多人名单的旧行：不改一次进度，刷新列表也看得到
    con = sqlite3.connect(tmp_path / "fce.db")
    con.execute(
        "UPDATE topic_progress SET assigned_user = 'malin,mxy'"
        " WHERE user = 'malin' AND topic_id = 'tense-22-28'"
    )
    con.commit()
    con.close()
    again = _store(tmp_path)
    listed = again.list_for("malin", "student")
    assert listed[0]["done_keys"] == ["n1", "n2", "n3"]
    assert again.get("malin", "tense-22-28")["done_keys"] == listed[0]["done_keys"]

    # 另一名被点名的学生只看见自己的行
    mine = again.put("mxy", "tense-22-28", ["n1"], assigned_user="malin，mxy")
    assert mine["assigned_user"] == "mxy"
    assert [r["user"] for r in again.list_for("mxy", "student")] == ["mxy"]
    assert all(r["user"] == "malin" for r in again.list_for("malin", "student"))

    # 单人归属写成别人：仍不出现在自己的列表里
    again.put("malin", "other-topic", ["x"], assigned_user="someone")
    ids = [r["topic_id"] for r in again.list_for("malin", "student")]
    assert "tense-22-28" in ids
    assert "other-topic" not in ids


def test_legacy_partial_stamp_cleared_full_stamp_kept(tmp_path):
    """旧行：只做一部分却有 completed_at 的，打开库时清掉。

    已经做完全部节点的行保留原时间戳，周学分不搬家。
    """
    data = json.loads(_TOPIC_JSON.read_text(encoding="utf-8"))
    store = _store(tmp_path)
    store.upsert_topic(data["meta"], data["content"])
    all_keys = ["what", "transitive", "steps", "tenses", "usage", "final"]
    old_full = "2026-09-01T08:00:00+00:00"
    old_partial = "2026-09-02T08:00:00+00:00"
    con = sqlite3.connect(tmp_path / "fce.db")
    con.execute(
        "INSERT INTO topic_progress"
        " (user, topic_id, done_keys, completed_at, updated_at)"
        " VALUES ('malin', 'lecture-29-passive-1', ?, ?, ?)",
        (json.dumps(all_keys), old_full, old_full),
    )
    con.execute(
        "INSERT INTO topic_progress"
        " (user, topic_id, done_keys, completed_at, updated_at)"
        " VALUES ('mxy', 'lecture-29-passive-1', ?, ?, ?)",
        (json.dumps(["what"]), old_partial, old_partial),
    )
    con.commit()
    con.close()

    reopened = _store(tmp_path)
    kept = reopened.get("malin", "lecture-29-passive-1")
    cleared = reopened.get("mxy", "lecture-29-passive-1")
    assert kept["completed_at"] == old_full
    assert cleared["completed_at"] == ""
    assert cleared["done_keys"] == ["what"]

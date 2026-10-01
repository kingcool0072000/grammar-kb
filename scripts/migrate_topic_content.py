"""把前端静态手册（topics.js 的 TOPICS）迁移进 topic_content 表（v2 结构化专题）。

用法：
    .venv/bin/python scripts/migrate_topic_content.py [fce.db路径]

迁移映射：theme → node{key, title, explain=lead}（卡片正文以讲解块追加进
explain，保留原 HTML）；assignedTo → assigned_to。幂等：重复执行覆盖。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from grammar_kb.topics import TopicStore  # noqa: E402


def strip_static_topics() -> list[dict]:
    """从前端 topics.js 提取 TOPICS 数组（内容含 HTML/反引号串，正则转
    JSON 不可靠——交给 node 本体 eval 解析后输出 JSON）。"""
    import subprocess
    node_script = (
        "const fs=require('fs');"
        "const src=fs.readFileSync('web/src/views/topics.js','utf8');"
        "const m=src.match(/const TOPICS = (\\[[\\s\\S]*?\\n\\])\\n/);"
        "eval('var TOPICS = '+m[1]);"
        "console.log(JSON.stringify(TOPICS))"
    )
    out = subprocess.run(["node", "-e", node_script], cwd=str(ROOT),
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"node 解析失败：{out.stderr[:300]}")
    return json.loads(out.stdout)


def cards_to_explain(theme: dict) -> str:
    """theme.cards（公式卡/讲解卡）→ explain 追加块（保留内嵌 HTML 表格）。"""
    parts = [theme.get("lead") or ""]
    for c in theme.get("cards") or []:
        h = c.get("h") or ""
        body = c.get("body") or ""
        if h or body:
            parts.append(f"<h5>{h}</h5>{body}")
    return "\n\n".join(p for p in parts if p.strip())


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "fce.db")
    store = TopicStore(db)
    topics = strip_static_topics()
    for t in topics:
        nodes = []
        for th in t.get("themes") or []:
            node = {
                "key": th.get("key") or "",
                "title": (th.get("no", "") + " " + (th.get("title") or "")).strip(),
                "explain": cards_to_explain(th),
                "wrong_qs": [],   # 静态手册未结构化题目：错题仍以卡片正文承载
                "general_qs": [],
                "children": [],
            }
            nodes.append(node)
        content = {"nodes": nodes}
        meta = {
            "topic_id": t["id"],
            "title": t.get("title") or t["id"],
            "subtitle": t.get("subtitle") or "",
            "emoji": t.get("emoji") or "📘",
            "assigned_to": t.get("assignedTo") or "",
        }
        got = store.upsert_topic(meta, content)
        print(f"迁移 {got['topic_id']}: {got['title']} · {len(nodes)} 节点 · assigned={got['assigned_to'] or '全员'}")


if __name__ == "__main__":
    main()

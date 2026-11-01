"""讲义 / 知识点 / 作业 / 词典 / 词库只读查询。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException, Query as FQuery

from ..atlas import lookup_public
from .http import _ok, _request_user

def register_query(app, ctx):
    kbq = ctx.kbq
    vocab_exam = ctx.vocab_exam


    @app.get("/stats")
    def stats():
        return _ok(kbq.stats())

    @app.get("/lectures")
    def lectures():
        return _ok([asdict(l) for l in kbq.list_lectures()])

    @app.get("/lectures/{number}")
    def lecture(number: int, format: str = "markdown"):
        content = kbq.lecture_html(number) if format == "html" else kbq.lecture_markdown(number)
        if content is None:
            raise HTTPException(status_code=404, detail=f"第 {number} 讲不存在")
        lec = kbq.get_lecture(number)
        return _ok(
            {
                "number": number,
                "title": lec.title if lec else "",
                "category": lec.category if lec else "",
                "format": format,
                "content": content,
            }
        )

    @app.get("/kp/{kp_id}")
    def kp(kp_id: int, format: str = "markdown"):
        content = kbq.kp_html(kp_id) if format == "html" else kbq.kp_markdown(kp_id)
        if content is None:
            raise HTTPException(status_code=404, detail=f"知识点 id={kp_id} 不存在")
        return _ok({"id": kp_id, "format": format, "content": content})

    @app.get("/points")
    def points():
        """全量知识点（教师备课中心全量列表；不在学生白名单，学生 403）。"""
        return _ok({"items": [asdict(k) for k in kbq.points()]})

    @app.get("/search")
    def search(
        q: str = FQuery(..., description="关键词"),
        category: Optional[str] = None,
        limit: int = 20,
    ):
        items = kbq.search_kps(q, category=category, limit=limit)
        return _ok(
            {"query": q, "category": category, "count": len(items), "items": [asdict(k) for k in items]}
        )

    @app.get("/markers")
    def markers(category: str = "时态", tense: Optional[str] = None):
        rows = kbq.markers_by_tense(tense) if tense else kbq.markers_by_category(category)
        return _ok({"category": category, "tense": tense, "count": len(rows), "items": rows})

    @app.get("/relation")
    def relation(type: str = "主将从现"):
        items = kbq.kps_by_relation(type)
        return _ok({"type": type, "count": len(items), "items": [asdict(k) for k in items]})

    @app.get("/exam-signals")
    def exam_signals():
        """列出数据中实际出现的所有考点信号维度。"""
        return _ok(kbq.list_exam_signals())

    @app.get("/exam-signal")
    def exam_signal(signal: str = "时态"):
        """反查：给定考点信号，返回会考该信号的知识点（反之亦然）。"""
        items = kbq.kps_by_exam_signal(signal)
        return _ok({"signal": signal, "count": len(items), "items": [asdict(k) for k in items]})

    @app.get("/taxonomy")
    def taxonomy():
        """知识点主题体系树（大类 → 主题 → 知识点），聚合零散知识点。"""
        return _ok(kbq.taxonomy())

    @app.get("/homework")
    def homework_list(lectures: Optional[str] = None):
        """已导入作业卷的讲次列表；?lectures=2,3 批量取多讲题目（题干+选项）。"""
        if not lectures:
            return _ok(kbq.homework_list())
        nums = []
        for part in lectures.split(","):
            part = part.strip()
            if part.isdigit():
                nums.append(int(part))
        return _ok({n: (kbq.homework(n) or {}).get("items", []) for n in nums})

    @app.get("/homework/{lecture}")
    def homework(lecture: int):
        """某讲作业卷全部题目（题干 + 选项，题号与测验平台一致）。"""
        data = kbq.homework(lecture)
        if data is None:
            raise HTTPException(status_code=404, detail=f"第 {lecture} 讲的作业卷未导入")
        return _ok(data)

    @app.get("/dict/{word}")
    def dict_lookup(word: str):
        """查任意单词（ECDICT 全量词典，不限于讲义语料）。

        所有格归一：children's / childrens' / dogs' → 原词查询。
        """
        import re as _re

        w = word.strip()
        w = _re.sub(r"(?:['’]s|s['’])$", lambda m: "s" if m.group(0).startswith("s") else "", w)
        entry = kbq.dict_lookup(w)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"词典未收录：{word}")
        atlas = lookup_public(kbq.db.conn, w, full=True)
        if atlas:
            entry = dict(entry)
            entry["atlas"] = atlas
        return _ok(entry)

    @app.get("/vocabulary")
    def vocabulary(limit: int = 300, min_freq: int = 2):
        """基于讲义语料的单词表（释义/词性/词形变化/来源）。"""
        return _ok(kbq.vocabulary(limit=limit, min_freq=min_freq))

    @app.get("/vocab-levels")
    def vocab_levels(request: "fastapi.Request", max_level: int = 7,
                    counts_only: int = 0, level: int = -1):
        """分层词库（vocab_word 表）：单词 + L0-L7 级别 + 释义/例句。

        L0=哈一课本高频词（与 /vocabulary min_freq=2 同源，带语料增强）；
        L1-L7 为外部词表增量（孩子侧只显示 L 编号，级别名不下发）。
        max_level 截断层数（如 5 = L0-L5）。
        counts_only=1 只返回 counts+unlocked（备课中心级别卡——避免 1.9MB
        全量词库拖慢打开）；level=n 只返回该级词条（级别详情页单级拉取）。
        """
        req_level = max(0, min(7, int(max_level)))
        # 学生可见级别 = 教师设定的单词级别（不要求考试 80 分）。
        # 该生从未被单独设定时，才回落：L0 恒开，L{n} 考试 ≥80 解锁 L{n+1}。
        # counts 始终按 req_level 给全量（锁定层也要显示词数），items 才截断。
        # level=n 只取该级，且不能用这个参数越过解锁上限（此前第二段赋值
        # 用 req_level 覆盖了学生上限，?level=7 能把未解锁词库整页拉走）。
        unlocked = req_level
        if request.state.role != "teacher":
            unlocked = min(req_level, vocab_exam.unlocked_level(_request_user(request)))
        level_filter: Optional[int] = None
        if level >= 0:
            level_filter = max(0, int(level))
        import json as _json

        def _j(s, dft):
            try:
                return _json.loads(s or dft)
            except (ValueError, TypeError):
                return _json.loads(dft)

        counts_all = kbq.db.vocab_level_counts(req_level)
        if counts_only:
            return _ok({"counts": counts_all, "unlocked": unlocked,
                        "items": []})
        if level_filter is not None and level_filter > unlocked:
            rows: list = []
        elif level_filter is not None:
            rows = kbq.db.vocab_level_words(level_filter, unlocked)
        else:
            rows = kbq.db.vocab_level_words(None, unlocked)
        atlas_by_word = kbq.db.vocab_atlas_compact([r["word"] for r in rows])
        items = []
        for r in rows:
            item = {
                "word": r["word"],
                "level": r["level"],
                "pos": _j(r["pos"], "[]"),
                "gloss": r["gloss"] or "",
                "meanings": _j(r["meanings"], "[]"),
                "example": _j(r["example"], "{}"),
                "extra": _j(r["extra"], "{}"),
            }
            atlas = atlas_by_word.get((r["word"] or "").strip().lower())
            if atlas:
                item["atlas"] = {
                    key: value for key, value in atlas.items()
                    if key not in ("gloss", "example")
                }
                # 词光是释义和例句的优先来源；没有词光条目时仍用词表原文。
                if atlas.get("gloss"):
                    item["gloss"] = atlas["gloss"]
                    item["from_atlas"] = True
                if atlas.get("example"):
                    item["example"] = atlas["example"]
            items.append(item)
        # L0 按语料词频降序（跟「背单词」原有顺序一致），L1+ 按字母序
        items.sort(key=lambda x: (
            x["level"],
            -(int(x["extra"].get("freq") or 0)) if x["level"] == 0 else x["word"],
            x["word"],
        ))
        return _ok({"counts": counts_all, "unlocked": unlocked, "items": items})

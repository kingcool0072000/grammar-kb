"""词汇级别考试：试卷生成、存档、解锁与成绩登记。"""
from __future__ import annotations

from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from ..vocab_exam import build_paper, render_paper_html
from .http import _ok, _html_response, _require_teacher

def register_vocab(app, ctx):
    vocab_exam = ctx.vocab_exam
    # 试卷历史与背词进度同库（fce.db）。路径从 store 取，不闭包
    # create_app 的局部变量。
    paper_db = vocab_exam.db_path

    # ---- 词汇级别考试（线下考卷 + 成绩登记；考试驱动解锁） ----

    # ---- 单词试卷生成（考试资产，与学生无关） ----

    @app.post("/vocab-papers/generate")
    def vocab_paper_generate(rec: dict, request: "fastapi.Request"):
        """生成一份考试卷（三题型+历史去重）。save=0 仅预览。教师专属。

        scope="level"（默认）本级出题；scope="learned" 按某学生已学词
        （进度里出现过的词，含未掌握）跨级别混合出题，须带 user。
        learned 仍要求 body.level 为 0-7（占位，不决定词池）。
        """
        _require_teacher(request)
        from ..vocab_paper import generate_paper, render_new_paper_html
        level = rec.get("level") if isinstance(rec, dict) else None
        if not isinstance(level, int) or not (0 <= level <= 7):
            raise HTTPException(status_code=422, detail="level 须为 0-7 整数")
        save = bool(rec.get("save", True)) if isinstance(rec, dict) else True
        scope = str(rec.get("scope") or "level") if isinstance(rec, dict) else "level"
        user = str(rec.get("user") or "").strip() if isinstance(rec, dict) else ""
        if scope not in ("level", "learned"):
            raise HTTPException(status_code=422, detail="scope 须为 level 或 learned")
        if scope == "learned" and not user:
            raise HTTPException(status_code=422, detail="learned 范围须指定 user（学生账号）")
        try:
            paper = generate_paper(level, save=save, scope=scope,
                                   user=user or None, fce_db_path=paper_db)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"paper": paper,
                    "html": render_new_paper_html(paper)})

    @app.post("/vocab-papers/save")
    def vocab_paper_save(rec: dict, request: "fastapi.Request"):
        """保存一份预览过的试卷（生成→预览→保存 的第三步）。教师专属。"""
        _require_teacher(request)
        from ..vocab_paper import save_paper
        try:
            return _ok(save_paper(rec if isinstance(rec, dict) else {},
                                  fce_db_path=paper_db))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.delete("/vocab-papers/{paper_id}")
    def vocab_paper_delete(paper_id: str, request: "fastapi.Request"):
        """删除一份试卷资产。教师专属。"""
        _require_teacher(request)
        from ..vocab_paper import delete_paper
        if not delete_paper(paper_id, fce_db_path=paper_db):
            raise HTTPException(status_code=404, detail="试卷不存在")
        return _ok({"deleted": paper_id})

    @app.get("/vocab-papers/{paper_id}/preview")
    def vocab_paper_preview(paper_id: str, request: "fastapi.Request"):
        """预览一份历史卷（完整 HTML）。教师专属。"""
        _require_teacher(request)
        from ..vocab_paper import get_paper, render_new_paper_html
        paper = get_paper(paper_id, fce_db_path=paper_db)
        if not paper:
            raise HTTPException(status_code=404, detail="试卷不存在")
        return _ok({"paper": {
            "paper_id": paper["paper_id"], "level": paper["level"],
            "zh2en": len(paper["zh2en"]), "en2zh": len(paper["en2zh"]),
            "spell": len(paper["spell"]), "words": len(paper["words"]),
            "created_at": paper["created_at"]},
            "html": render_new_paper_html(paper)})

    @app.get("/vocab-papers/history")
    def vocab_paper_history(request: "fastapi.Request", level: int = -1,
                            limit: int = 80, include_mix: bool = False):
        """已生成试卷历史（去重追溯/复用）。教师专属。

        level>=0 且 include_mix=1：返回该级 + 混合卷（level<0），
        两桶各自按 limit 取，避免本级卷被其它级别挤出窗口。
        """
        _require_teacher(request)
        from ..vocab_paper import list_paper_history
        items = list_paper_history(
            None if level < 0 else level, limit=limit,
            fce_db_path=paper_db,
            include_mix=bool(include_mix) and level >= 0)
        return _ok({"papers": items})

    @app.get("/vocab-exams/unlock")
    def vocab_unlock_get(request: "fastapi.Request"):
        """已单独设定的单词级别：{学生: 级别}。未出现的学生表示还没设定。教师专属。"""
        _require_teacher(request)
        return _ok(vocab_exam.list_unlock_all())

    @app.put("/vocab-exams/unlock")
    def vocab_unlock_put(rec: dict, request: "fastapi.Request"):
        """设置某学生的单词级别（0-5）。教师专属。

        设定后立即生效，不要求该生词汇考试达到 80 分，
        也可以设得比考试推算更低或更高。
        """
        _require_teacher(request)
        body = rec if isinstance(rec, dict) else {}
        user = str(body.get("user") or "").strip()
        level = body.get("level")
        if not user or not isinstance(level, int) or isinstance(level, bool) or not (0 <= level <= 5):
            raise HTTPException(status_code=422, detail="user 必填且 level 须为 0-5 整数")
        vocab_exam.put_unlock(user, level)
        return _ok({"user": user, "level": level,
                    "unlocked": vocab_exam.unlocked_level(user),
                    "configured": True})

    @app.get("/vocab-exams")
    def vocab_exams_list(request: "fastapi.Request", user: Optional[str] = None,
                         include_all: int = 0):
        """词汇考试记录。

        学生只看自己的。教师默认看自己的；?user= 看指定学生；
        ?include_all=1 看全部学生（学生传这个参数无效）。
        """
        if request.state.role == "teacher" and include_all:
            return _ok({
                "exams": vocab_exam.list(None),
                "pass_score": 80,
            })
        u = user if (request.state.role == "teacher" and user) else request.state.user
        return _ok({
            "exams": vocab_exam.list(u),
            "passed_levels": sorted(vocab_exam.passed_levels(u)),
            "unlocked_level": vocab_exam.unlocked_level(u),
            "level_configured": vocab_exam.level_configured(u),
            "pass_score": 80,
        })

    @app.post("/vocab-exams")
    def vocab_exam_add(payload: dict, request: "fastapi.Request"):
        """登记一次词汇考试成绩（教师专属）。

        该生若已由教师单独设定单词级别，成绩只作记录，不改写级别。
        尚未单独设定时，≥80 分仍会解锁下一级。
        paper_id 可选：从批改中心考试打分卡登记时带上，关联具体试卷。
        """
        _require_teacher(request)
        try:
            user = str(payload.get("user", "")).strip()
            level = int(payload.get("level"))
            score = int(payload.get("score"))
            exam_date = str(payload.get("exam_date", "")).strip()
            note = str(payload.get("note", "") or "")
            paper_id = str(payload.get("paper_id", "") or "").strip()
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="user/level/score/exam_date 须齐全")
        if not user:
            raise HTTPException(status_code=422, detail="user 须指定学生账号")
        try:
            rec = vocab_exam.record(user, level, score, exam_date, note, paper_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({
            "exam": rec,
            "unlocked_level": vocab_exam.unlocked_level(user),
            "level_configured": vocab_exam.level_configured(user),
            "passed": score >= 80,
        })

    @app.put("/vocab-exams/{exam_id}")
    def vocab_exam_update(exam_id: int, payload: dict, request: "fastapi.Request"):
        """修改一条词汇考试记录（教师专属）。未提交的字段保持原值。"""
        _require_teacher(request)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="请求体须为对象")
        current = vocab_exam.get(exam_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        fields = ("user", "level", "score", "exam_date", "note", "paper_id")
        if not any(k in payload for k in fields):
            raise HTTPException(status_code=422, detail="至少提供一项要修改的字段")
        merged = {
            "user": current["user"],
            "level": current["level"],
            "score": current["score"],
            "exam_date": current["exam_date"],
            "note": current.get("note") or "",
            "paper_id": current.get("paper_id") or "",
        }
        for key in fields:
            if key in payload:
                merged[key] = payload[key]
        try:
            user = str(merged["user"] or "").strip()
            level = int(merged["level"])
            score = int(merged["score"])
            exam_date = str(merged["exam_date"] or "").strip()
            note = str(merged["note"] or "")
            paper_id = str(merged["paper_id"] or "").strip()
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="user/level/score/exam_date 须合法")
        if not user:
            raise HTTPException(status_code=422, detail="user 须指定学生账号")
        try:
            rec = vocab_exam.update(
                exam_id, user, level, score, exam_date, note, paper_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if rec is None:
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        out = {
            "exam": rec,
            "unlocked_level": vocab_exam.unlocked_level(rec["user"]),
            "level_configured": vocab_exam.level_configured(rec["user"]),
            "passed": int(rec["score"]) >= 80,
        }
        if rec["user"] != current["user"]:
            out["previous_user"] = current["user"]
            out["previous_unlocked_level"] = vocab_exam.unlocked_level(current["user"])
        return _ok(out)

    @app.get("/vocab-papers/grading")
    def vocab_papers_grading(request: "fastapi.Request", level: int = -1):
        """批改中心·考试打分：已存档试卷 + 每卷已登记成绩（教师专属）。"""
        _require_teacher(request)
        from ..vocab_paper import list_paper_history
        papers = list_paper_history(
            None if level < 0 else level, limit=50, fce_db_path=paper_db)
        by_paper = vocab_exam.exams_for_papers([p["paper_id"] for p in papers])
        out = []
        for p in papers:
            out.append({**p, "exams": by_paper.get(p["paper_id"], [])})
        return _ok({"papers": out})

    @app.delete("/vocab-exams/{exam_id}")
    def vocab_exam_del(exam_id: int, request: "fastapi.Request"):
        """删一条考试记录（教师专属，误登记用）。"""
        _require_teacher(request)
        if not vocab_exam.delete_exam(exam_id):
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        return _ok({"id": exam_id})

    @app.get("/vocab-exams/paper", response_class=None)
    def vocab_exam_paper(request: "fastapi.Request", level: int = 0, seed: Optional[int] = None):
        """生成 L{level} 级考卷 HTML（教师打印用；学生 403）。"""
        _require_teacher(request)
        try:
            paper = build_paper(max(0, min(5, int(level))), seed=seed)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        user = request.query_params.get("student", "")
        return _html_response(render_paper_html(paper, student=user))

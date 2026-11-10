"""词汇级别考试：试卷生成、存档、解锁、成绩登记与按题错词明细。"""
from __future__ import annotations

from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from ..vocab_exam import MAX_UNLOCK_LEVEL, PASS_SCORE
from ..vocab_paper import generate_paper, render_new_paper_html
from .http import _ok, _require_teacher


def _clean_wrong_words(raw) -> list[str]:
    """请求体里的错词数组清洗：字符串化、去空白、小写、保序去重。"""
    if raw is None or not isinstance(raw, list):
        return []
    out: list[str] = []
    for w in raw:
        s = str(w or "").strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def _wrong_words_from_answers(answers) -> list[str]:
    """错词明细行 → 词表。只计答错行，保序去重，与入库口径一致（小写、截 60）。"""
    out: list[str] = []
    for a in answers or []:
        if not isinstance(a, dict):
            continue
        if a.get("correct") not in (0, False):
            continue
        s = str(a.get("word") or "").strip().lower()[:60]
        if s and s not in out:
            out.append(s)
    return out


def register_vocab(app, ctx):
    vocab_exam = ctx.vocab_exam
    vocab_paper = ctx.vocab_paper
    recite = ctx.recite
    # 试卷历史与背词进度同库（fce.db）。路径从 store 取，不闭包
    # create_app 的局部变量。
    paper_db = vocab_exam.db_path

    # ---- 单词试卷生成（考试资产，与学生无关） ----

    @app.post("/vocab-papers/generate")
    def vocab_paper_generate(rec: dict, request: "fastapi.Request"):
        """生成一份考试卷（三题型+历史去重）。save=0 仅预览。教师专属。

        scope="level"（默认）本级出题；scope="learned" 按某学生已学词
        （进度里出现过的词，含未掌握）跨级别混合出题，须带 user。
        learned 仍要求 body.level 为有效级别（占位，不决定词池）。
        可选 seed（复现同一份卷）/ preset（选词来源预设）/
        ratios（各题型数量配比 dict）/ total（总题量 10-200，按比例
        缩放配比），原样透传给出题器。
        """
        _require_teacher(request)
        body = rec if isinstance(rec, dict) else {}
        level = body.get("level")
        if not isinstance(level, int) or isinstance(level, bool) \
                or not (0 <= level <= MAX_UNLOCK_LEVEL):
            raise HTTPException(
                status_code=422, detail=f"level 须为 0-{MAX_UNLOCK_LEVEL} 整数")
        save = bool(body.get("save", True))
        scope = str(body.get("scope") or "level")
        user = str(body.get("user") or "").strip()
        if scope not in ("level", "learned"):
            raise HTTPException(status_code=422, detail="scope 须为 level 或 learned")
        if scope == "learned" and not user:
            raise HTTPException(status_code=422, detail="learned 范围须指定 user（学生账号）")
        seed = body.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            try:
                seed = int(seed) if seed is not None and str(seed).strip() else None
            except (TypeError, ValueError):
                seed = None
        preset = str(body.get("preset") or "standard")
        ratios = body.get("ratios") if isinstance(body.get("ratios"), dict) else None
        total = body.get("total")
        if isinstance(total, bool) or total is not None \
                and not isinstance(total, int):
            total = None
        try:
            paper = generate_paper(level, save=save, scope=scope,
                                   user=user or None, fce_db_path=paper_db,
                                   seed=seed, preset=preset, ratios=ratios,
                                   total=total)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"paper": paper,
                    "html": render_new_paper_html(paper)})

    @app.post("/vocab-papers/save")
    def vocab_paper_save(rec: dict, request: "fastapi.Request"):
        """保存一份预览过的试卷（生成→预览→保存 的第三步）。教师专属。"""
        _require_teacher(request)
        try:
            return _ok(vocab_paper.save_paper(rec if isinstance(rec, dict) else {}))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.delete("/vocab-papers/{paper_id}")
    def vocab_paper_delete(paper_id: str, request: "fastapi.Request"):
        """删除一份试卷资产。教师专属。"""
        _require_teacher(request)
        if not vocab_paper.delete_paper(paper_id):
            raise HTTPException(status_code=404, detail="试卷不存在")
        return _ok({"deleted": paper_id})

    @app.get("/vocab-papers/{paper_id}/preview")
    def vocab_paper_preview(paper_id: str, request: "fastapi.Request"):
        """预览一份历史卷（完整 HTML）。教师专属。"""
        _require_teacher(request)
        paper = vocab_paper.get_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="试卷不存在")
        meta: dict = {
            "paper_id": paper.get("paper_id"), "level": paper.get("level"),
            "words": len(paper.get("words") or []),
            "created_at": paper.get("created_at"),
        }
        sections = paper.get("sections")
        if isinstance(sections, list):
            meta["sections"] = [
                {"type": s.get("type"), "title": s.get("title"),
                 "count": len(s.get("items") or [])}
                for s in sections if isinstance(s, dict)]
        return _ok({"paper": meta,
                    "html": render_new_paper_html(paper)})

    @app.get("/vocab-papers/history")
    def vocab_paper_history(request: "fastapi.Request", level: int = -1,
                            limit: int = 80, include_mix: bool = False):
        """已生成试卷历史（去重追溯/复用）。教师专属。

        level>=0 且 include_mix=1：返回该级 + 混合卷（level<0），
        两桶各自按 limit 取，避免本级卷被其它级别挤出窗口。
        """
        _require_teacher(request)
        items = vocab_paper.list_history(
            None if level < 0 else level, limit=limit,
            include_mix=bool(include_mix) and level >= 0)
        return _ok({"papers": items})

    # ---- 词汇级别考试（线下考卷 + 成绩登记；考试驱动解锁） ----

    @app.put("/vocab-exams/unlock")
    def vocab_unlock_put(rec: dict, request: "fastapi.Request"):
        """设置某学生的单词级别（0-7，上限随 MAX_UNLOCK_LEVEL）。教师专属。

        设定后立即生效，不要求该生词汇考试达到及格分，
        也可以设得比考试推算更低或更高。
        """
        _require_teacher(request)
        body = rec if isinstance(rec, dict) else {}
        user = str(body.get("user") or "").strip()
        level = body.get("level")
        if not user or not isinstance(level, int) or isinstance(level, bool) \
                or not (0 <= level <= MAX_UNLOCK_LEVEL):
            raise HTTPException(
                status_code=422,
                detail=f"user 必填且 level 须为 0-{MAX_UNLOCK_LEVEL} 整数")
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
                "pass_score": PASS_SCORE,
            })
        u = user if (request.state.role == "teacher" and user) else request.state.user
        return _ok({
            "exams": vocab_exam.list(u),
            "passed_levels": sorted(vocab_exam.passed_levels(u)),
            "unlocked_level": vocab_exam.unlocked_level(u),
            "level_configured": vocab_exam.level_configured(u),
            "pass_score": PASS_SCORE,
        })

    @app.get("/vocab-exams/{exam_id}")
    def vocab_exam_detail(exam_id: int, request: "fastapi.Request"):
        """单条成绩 + 按题错词明细（教师专属，批改卡回看 / 编辑回填用）。

        answers 是行级来源（只存错行；对的题 = 该卷词表减错词）。
        exam.wrong_words 从这些行投影（保序），编辑预选读这一份。
        """
        _require_teacher(request)
        exam = vocab_exam.get(exam_id)
        if exam is None:
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        answers = vocab_exam.answers_for_exam(exam_id)
        exam = {**exam, "wrong_words": _wrong_words_from_answers(answers)}
        return _ok({"exam": exam, "answers": answers})

    @app.post("/vocab-exams")
    def vocab_exam_add(payload: dict, request: "fastapi.Request"):
        """登记一次词汇考试成绩（教师专属）。

        该生若已由教师单独设定单词级别，成绩只作记录，不改写级别。
        尚未单独设定时，≥及格分仍会解锁下一级。
        paper_id 可选：从批改中心考试打分卡登记时带上，关联具体试卷。
        wrong_words 可选：勾选的错词词表——落明细（vocab_exam_answers）
        并回流进该生的背词艾宾浩斯复习队列。
        """
        _require_teacher(request)
        body = payload if isinstance(payload, dict) else {}
        try:
            user = str(body.get("user", "")).strip()
            level = int(body.get("level"))
            score = int(body.get("score"))
            exam_date = str(body.get("exam_date", "")).strip()
            note = str(body.get("note", "") or "")
            paper_id = str(body.get("paper_id", "") or "").strip()
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="user/level/score/exam_date 须齐全")
        if not user:
            raise HTTPException(status_code=422, detail="user 须指定学生账号")
        raw_wrong = body.get("wrong_words")
        if raw_wrong is not None and not isinstance(raw_wrong, list):
            raise HTTPException(status_code=422, detail="wrong_words 须为字符串数组")
        wrong_words = _clean_wrong_words(raw_wrong)
        try:
            rec = vocab_exam.record(user, level, score, exam_date, note, paper_id,
                                    wrong_words=wrong_words if raw_wrong is not None else None)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if wrong_words:
            # 错词回流背词复习队列；卷级别即词级别（混合卷 level<0 不标级）
            recite.enter_review(user, wrong_words,
                                levels={w: level for w in wrong_words}
                                if level >= 0 else None)
        return _ok({
            "exam": rec,
            "unlocked_level": vocab_exam.unlocked_level(user),
            "level_configured": vocab_exam.level_configured(user),
            "passed": score >= PASS_SCORE,
            "wrong_count": len(vocab_exam.answers_for_exam(int(rec["id"]))),
        })

    @app.put("/vocab-exams/{exam_id}")
    def vocab_exam_update(exam_id: int, payload: dict, request: "fastapi.Request"):
        """修改一条词汇考试记录（教师专属）。未提交的字段保持原值。

        wrong_words：传数组=重写错词明细（空数组清空）；不传/传 null=明细与
        复习队列都不动。回流只喂相对旧明细新增的词——重叠词不再
        wrong_count+1，也不重置 stage / 攻克 / 掌握。换归属学生时，
        当前错词对该生是新登记，整表回流一次。移出名单的词留在原队列。
        """
        _require_teacher(request)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="请求体须为对象")
        current = vocab_exam.get(exam_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        fields = ("user", "level", "score", "exam_date", "note", "paper_id")
        if not any(k in payload for k in (*fields, "wrong_words")):
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
        raw_wrong = payload.get("wrong_words")
        if raw_wrong is not None and not isinstance(raw_wrong, list):
            raise HTTPException(status_code=422, detail="wrong_words 须为字符串数组")
        wrong_words = None if raw_wrong is None else _clean_wrong_words(raw_wrong)
        # 先读旧行再重写。enter_review 每次调用都 +1 并清 stage，
        # 所以只能把「这次才变成错」的词送进去。
        prev_wrong = (
            _wrong_words_from_answers(vocab_exam.answers_for_exam(exam_id))
            if wrong_words is not None else []
        )
        try:
            rec = vocab_exam.update(
                exam_id, user, level, score, exam_date, note, paper_id,
                wrong_words=wrong_words)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if rec is None:
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        if wrong_words is not None:
            if rec["user"] != current["user"]:
                fresh = list(wrong_words)
            else:
                prev_set = set(prev_wrong)
                fresh = [w for w in wrong_words if w[:60] not in prev_set]
            if fresh:
                # 回流按明细现归属的学生（PUT 换人时用新 user）
                recite.enter_review(rec["user"], fresh,
                                    levels={w: int(rec["level"]) for w in fresh}
                                    if int(rec["level"]) >= 0 else None)
        out = {
            "exam": rec,
            "unlocked_level": vocab_exam.unlocked_level(rec["user"]),
            "level_configured": vocab_exam.level_configured(rec["user"]),
            "passed": int(rec["score"]) >= PASS_SCORE,
            "wrong_count": len(vocab_exam.answers_for_exam(int(rec["id"]))),
        }
        if rec["user"] != current["user"]:
            out["previous_user"] = current["user"]
            out["previous_unlocked_level"] = vocab_exam.unlocked_level(current["user"])
        return _ok(out)

    @app.get("/vocab-papers/grading")
    def vocab_papers_grading(request: "fastapi.Request", level: int = -1):
        """批改中心·考试打分：已存档试卷 + 每卷已登记成绩（教师专属）。

        已登记的成绩顺带 wrong_words（按 exam 批量一次查，防 N+1），
        供打分卡勾选错词时回显上次登记的明细。
        """
        _require_teacher(request)
        papers = vocab_paper.list_history(None if level < 0 else level, limit=50)
        by_paper = vocab_exam.exams_for_papers(
            [p.get("paper_id") for p in papers if p.get("paper_id")])
        exam_ids = [e["id"] for rows in by_paper.values() for e in rows]
        wrong_map = vocab_exam.answers_for_exams(exam_ids)
        out = []
        for p in papers:
            exams = [{**e, "wrong_words": wrong_map.get(e["id"], [])}
                     for e in by_paper.get(p.get("paper_id") or "", [])]
            out.append({**p, "exams": exams})
        return _ok({"papers": out, "pass_score": PASS_SCORE})

    @app.delete("/vocab-exams/{exam_id}")
    def vocab_exam_del(exam_id: int, request: "fastapi.Request"):
        """删一条考试记录（教师专属，误登记用；错词明细一并删）。"""
        _require_teacher(request)
        if not vocab_exam.delete_exam(exam_id):
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        return _ok({"id": exam_id})

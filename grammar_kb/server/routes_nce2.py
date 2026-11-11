"""NCE2 听力跟读：课文清单 + 听力打点 + 跟读录音提交与教师批改。

学生宇宙（任务制）：只看「被安排过」的课文——安排 = 出现在该生任意一周
plan_weeks.tasks 的 NCE2 课次里（plan._plan_lessons，由计划表车道提供）
——∪ 自己提交过的课文（listen 打点也算）；本周计划里的标 week。
未安排也没做过的课文不可见、不可提交、mp3 拒放（403）。
教师：96 课全量，批改队列只收跟读（shadow）行。

课文清单与 mp3 都是静态文件（nce2_study 顶部两个路径函数），不在库里。
"""
from __future__ import annotations

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse

from .. import nce2_study
from ..clock import current_monday
from .http import _ok, _request_user, _require_teacher
from .schemas import Nce2GradeIn, Nce2SubmitIn

_QUEUE_STATUS = ("pending", "graded", "all")


def register_nce2(app, ctx):
    nce2 = ctx.nce2
    plan = ctx.plan

    # ---- 学生课文宇宙：安排并集 + 本周集合 + 本周完成方式 ----

    def _scope(user: str) -> tuple[set, set, dict]:
        assigned: set = set()
        week: set = set()
        modes: dict = {}  # 本周课次 → task_mode（listen=打卡 / shadow=跟读）
        try:
            cur = current_monday().isoformat()
            for w in plan.list_weeks(user=user) or []:
                tasks = w.get("tasks") or {}
                # 本周组的完成方式供学生端动作区分支（跟读录音 vs 精听打卡）
                if w.get("week_start") == cur:
                    for u in plan._nce2_units(tasks):
                        for lid in u["lessons"]:
                            modes[int(lid)] = u.get("task_mode") or "shadow"
                # 与排程同口径：课次由计划表车道的 _plan_lessons 解出
                # （tasks 里 NCE2 周计划 → 课次 id 列表，去重保序）
                for lid in plan._plan_lessons(tasks):
                    if lid:
                        assigned.add(int(lid))
                        if w.get("week_start") == cur:
                            week.add(int(lid))
        except Exception:  # noqa: BLE001  计划表异常时退化为「只看做过的」
            pass
        return assigned, week, modes

    def _student_can_access(user: str, lid: int) -> bool:
        assigned, _, _ = _scope(user)
        return lid in assigned or lid in nce2.lessons_status(user)

    def _with_title(rows: list[dict]) -> list[dict]:
        """批改队列/历史行补 manifest 课标题（课文删出清单时空串兜底）。"""
        for r in rows:
            les = nce2_study.lesson(r["lesson_id"])
            r["lesson_title"] = str(les.get("title") or "") if les else ""
        return rows

    # ---- 课文清单与课文音频 ----

    @app.get("/nce2/lessons")
    def nce2_lessons(request: "fastapi.Request"):
        """学生：宇宙内课文 + 听力/跟读状态。教师：全量 96 课。

        ?with_sentences=1 时每课附 manifest 原样的逐句双语时间轴
        ``sentences``（[{t, en, zh}]，单课播放/跟读页用）；默认不带——
        列表页只要元信息，控制响应体积。学生范围规则不变（仍限宇宙内）。
        listen_today=今天已存在 listen 提交（完成态，非「今天排了听」）；
        nce2_mode=本周计划行对该课的完成方式（listen/shadow，不在本周为
        null——学生端动作区按它分支，历史课回听一律按自由跟读）。
        """
        all_lessons = nce2_study.lessons()
        with_sents = request.query_params.get("with_sentences") == "1"

        def _row(les: dict, **extra) -> dict:
            row = {**nce2_study.lesson_meta(les), **extra}
            if with_sents:
                row["sentences"] = les.get("sentences") or []  # manifest 原样
            return row

        if getattr(request.state, "role", "") == "teacher":
            return _ok([_row(les) for les in all_lessons])
        assigned, week, modes = _scope(request.state.user)
        status = nce2.lessons_status(request.state.user)
        items = []
        for les in all_lessons:
            lid = les["id"]
            # 未安排且没做过：不可见（与视频学习同口径）
            if lid not in assigned and lid not in status:
                continue
            st = status.get(lid)
            items.append(_row(
                les,
                week=lid in week,
                planned=lid in assigned,
                nce2_mode=modes.get(lid),
                listen_today=bool(st and st["listen_today"]),
                listen_done_ever=bool(st and st["listen_done_ever"]),
                shadow=st["shadow"] if st else None,
            ))
        return _ok(items)

    @app.get("/nce2/lessons/{lesson_id}/audio")
    def nce2_lesson_audio(lesson_id: int, request: "fastapi.Request"):
        """课文 mp3（<audio src> 直连，token 走查询参数；路径以 /audio 结尾）。

        404=无此课/文件未放置；403=学生不在宇宙内。
        """
        les = nce2_study.lesson(lesson_id)
        if les is None:
            raise HTTPException(status_code=404, detail=f"课文 {lesson_id} 不存在")
        if getattr(request.state, "role", "") != "teacher" \
                and not _student_can_access(request.state.user, lesson_id):
            raise HTTPException(status_code=403, detail="该课未安排给你")
        path = nce2_study.lesson_audio_path(lesson_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"课文 {lesson_id} 音频未放置")
        return FileResponse(path, media_type="audio/mpeg")

    # ---- 提交（学生）与批改（教师） ----

    @app.post("/nce2/submissions")
    def nce2_submit(rec: Nce2SubmitIn, request: "fastapi.Request"):
        """学生提交：kind=listen 打点（幂等）/ kind=shadow 跟读录音（待批改）。

        与视频复述同门禁：只能提交被安排给自己（或此前做过）的课文。
        """
        user = _request_user(request)
        if getattr(request.state, "role", "") != "teacher" \
                and not _student_can_access(user, rec.lesson_id):
            raise HTTPException(status_code=403, detail="该课未安排给你，不能提交")
        try:
            row = nce2.submit(
                user, rec.lesson_id, rec.kind, audio_b64=rec.audio_b64,
                mime=rec.mime or "", duration_sec=rec.duration_sec)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"id": row["id"], "status": row["status"]})

    @app.get("/nce2/submissions")
    def nce2_submissions(
        request: "fastapi.Request", status: str = "pending", limit: int = 200,
    ):
        """提交流水。教师=批改队列（?status=pending|graded|all，默认 pending）；
        学生=只返回自己的（同型结构，含 listen 打点）。身份在端点内分流。"""
        if getattr(request.state, "role", "") == "teacher":
            st = (status or "pending").strip()
            if st not in _QUEUE_STATUS:
                st = "pending"
            rows = nce2.list_all(
                status=None if st == "all" else st,
                limit=max(1, min(int(limit or 200), 500)))
        else:
            rows = nce2.list_by_user(request.state.user)
        return _ok(_with_title(rows))

    @app.get("/nce2/submissions/{submission_id}/audio")
    def nce2_submission_audio(submission_id: int, request: "fastapi.Request"):
        """跟读录音二进制流（<audio src> 直连，token 走查询参数）。
        教师可听全部，学生只能听自己的。"""
        data = nce2.get_audio(submission_id)
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"提交 id={submission_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能播放自己的录音")
        import base64 as _b64
        try:
            raw = _b64.b64decode(data.get("audio_b64") or "")
        except Exception:
            raise HTTPException(status_code=500, detail="音频数据损坏")
        return Response(content=raw, media_type=data.get("mime") or "audio/mp4")

    @app.put("/nce2/submissions/{submission_id}")
    def nce2_grade(
        submission_id: int, rec: Nce2GradeIn, request: "fastapi.Request"
    ):
        """教师批改跟读录音（10 分制，越界由 Store 夹取）。"""
        _require_teacher(request)
        try:
            data = nce2.grade(submission_id, rec.teacher_score, rec.teacher_comment)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"提交 id={submission_id} 不存在")
        return _ok(data)

    @app.delete("/nce2/submissions/{submission_id}")
    def nce2_delete(submission_id: int, request: "fastapi.Request"):
        """教师删除一条提交（打点或跟读录音）。"""
        _require_teacher(request)
        if not nce2.delete(submission_id):
            raise HTTPException(
                status_code=404, detail=f"提交 id={submission_id} 不存在")
        return _ok({"id": submission_id})

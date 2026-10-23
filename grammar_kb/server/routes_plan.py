"""计划表、作业卷管理、用户、AI 周报与作业成绩。"""
from __future__ import annotations

from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from .http import _ok, _request_user, _require_teacher
from .schemas import ExamRecordIn

def register_plan(app, ctx):
    exams = ctx.exams
    users = ctx.users
    fce_submissions = ctx.fce_submissions
    reading = ctx.reading
    analytics_ai = ctx.analytics_ai
    recite = ctx.recite
    plan = ctx.plan
    homework_admin = ctx.homework_admin
    library = ctx.library

    # ---- 计划表（教师专属：周维度计划 + 实时完成度 + 上周回顾） ----

    @app.get("/plan/weeks")
    def plan_weeks(request: "fastapi.Request", user: str = "malin"):
        """全部周计划 + 每周实时完成度 + 当前周的上周回顾。教师专属。"""
        _require_teacher(request)
        weeks = plan.list_weeks(user=user)
        for w in weeks:
            w["actuals"] = plan.week_actuals(w["week_start"], user=user)
        from ..clock import current_monday
        cur_mon = current_monday().isoformat()
        return _ok({
            "weeks": weeks,
            "current_week": cur_mon,
            "last_week_review": plan.last_week_review(cur_mon, user=user),
        })

    @app.put("/plan/weeks/{week_start}")
    def plan_week_put(week_start: str, payload: dict, request: "fastapi.Request"):
        """保存一周的 tasks/notes（教师专属，幂等覆盖；user 归属学生）。"""
        _require_teacher(request)
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        notes = payload.get("notes", "") if isinstance(payload, dict) else ""
        user = (payload.get("user") if isinstance(payload, dict) else None) or "malin"
        if tasks is None or not isinstance(tasks, dict):
            raise HTTPException(status_code=422, detail="payload.tasks 须为对象")
        try:
            return _ok(plan.put_week(week_start, tasks, str(notes or ""),
                                     user=str(user)[:32]))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # ---- 外循环（双循环教学）：诊断聚类 / 周验收 / 日任务展开 ----

    @app.get("/plan/diagnosis")
    def plan_diagnosis(request: "fastapi.Request", weeks_back: int = 8):
        """错题信号按讲次聚类（外循环①：诊断感知）。教师专属。"""
        _require_teacher(request)
        weeks_back = min(max(weeks_back, 1), 52)
        return _ok({"items": plan.diagnosis(weeks_back=weeks_back)})

    @app.get("/plan/review")
    def plan_review(request: "fastapi.Request", user: str = "malin"):
        """上周攻坚验收（外循环④：收敛/滚动/升级）。教师专属。"""
        _require_teacher(request)
        from ..clock import current_monday
        cur_mon = current_monday().isoformat()
        return _ok(plan.week_review(cur_mon, user=user))

    @app.get("/plan/daily-preview")
    def plan_daily_preview(request: "fastapi.Request"):
        """本周任务 → 每日学生任务建议（外循环③：任务展开预览）。教师专属。"""
        _require_teacher(request)
        from ..clock import current_monday
        cur_mon = current_monday().isoformat()
        return _ok({"week_start": cur_mon, "tasks": plan.week_daily_tasks(cur_mon)})

    @app.get("/plan/week-view")
    def plan_week_view(request: "fastapi.Request", week_start: str = "",
                       user: str = "malin"):
        """教师周视图：周拆解（阅读篇数/泛读词数/题型类数）+ 逐日任务矩阵。"""
        _require_teacher(request)
        from ..clock import current_monday
        if not week_start:
            week_start = current_monday().isoformat()
        try:
            return _ok(plan.week_view(week_start, user=user))
        except ValueError:
            raise HTTPException(status_code=422, detail="week_start 须为 YYYY-MM-DD")

    @app.get("/plan/my-week")
    def plan_my_week(request: "fastapi.Request"):
        """学生课程表：自己本周的周计划只读视图（week_view 同源数据）。

        学生只能看自己（教师身份给 403，教师看学生走 /plan/week-view）。
        """
        if getattr(request.state, "role", "student") != "student":
            raise HTTPException(status_code=403, detail="课程表仅学生账号使用")
        from ..clock import current_monday
        week_start = current_monday().isoformat()
        return _ok(plan.week_view(week_start, user=request.state.user))

    @app.get("/plan/rules")
    def plan_rules(request: "fastapi.Request", user: str = "malin"):
        """完成判定规则清单（师生可读；学生只能读自己的）。"""
        if (getattr(request.state, "role", "") != "teacher"
                and request.state.user != user):
            user = request.state.user
        return _ok({"user": user, "rules": plan.get_rules(user)})

    @app.put("/plan/rules/{rule_id}")
    def plan_rule_set(rule_id: str, rec: dict, request: "fastapi.Request"):
        """教师修改某学生的完成判定规则开关。body: {value: 0|1, user}"""
        _require_teacher(request)
        val = int((rec or {}).get("value", 1))
        target = (rec or {}).get("user") or "malin"
        updated = plan.set_rule(target, rule_id, val)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"未知规则 {rule_id}")
        return _ok(updated)

    # ---- 作业卷管理（教师）：题目 + 答案（爱问云已批改测验的逐题对错） ----

    @app.get("/homework-admin/papers")
    def hw_papers(request: "fastapi.Request"):
        """全部作业卷总览：讲次 × 题数 × 已同步历次覆盖。教师专属。"""
        _require_teacher(request)
        return _ok({"papers": homework_admin.papers()})

    @app.get("/homework-admin/paper/{lecture}")
    def hw_paper_detail(lecture: int, request: "fastapi.Request"):
        """单讲次作业卷：题目 + 答案 + 历次逐题对错。教师专属。"""
        _require_teacher(request)
        return _ok(homework_admin.paper_detail(lecture))

    @app.put("/homework-admin/answer")
    def hw_answer_put(rec: dict, request: "fastapi.Request"):
        """单题答案编辑（教师人工补录）。教师专属。"""
        _require_teacher(request)
        body = rec if isinstance(rec, dict) else {}
        lecture = body.get("lecture")
        qnum = body.get("qnum")
        answer = body.get("answer")
        if not isinstance(lecture, int) or not isinstance(qnum, int) or answer is None:
            raise HTTPException(status_code=422, detail="lecture/qnum/answer 必填")
        if not homework_admin.put_answer(lecture, qnum, str(answer)):
            raise HTTPException(status_code=404, detail="题目不存在")
        return _ok({"lecture": lecture, "qnum": qnum, "answer": answer})

    @app.post("/homework-admin/sync-aicloud")
    async def hw_sync_aicloud(request: "fastapi.Request"):
        """从爱问云拉取已出分测验的逐题对错（未批改跳过）。教师专属。"""
        _require_teacher(request)
        import os as _os
        phone = (_os.environ.get("GRAMMAR_KB_AICLOUD_PHONE") or "").strip()
        password = (_os.environ.get("AICLOUD_PASSWORD") or "").strip()
        if not phone or not password:
            raise HTTPException(
                status_code=503,
                detail="未配置爱问云账号（GRAMMAR_KB_AICLOUD_PHONE / AICLOUD_PASSWORD）",
            )
        try:
            import anyio
            rep = await anyio.to_thread.run_sync(
                lambda: homework_admin.sync_aicloud(phone, password))
            return _ok(rep)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"爱问云同步失败: {e}")

    # ---- 学生管理（教师）：增删学生；目标配置走 /plan/goals ----

    @app.get("/users")
    def users_list(request: "fastapi.Request"):
        """全部账号（教师专属；学生管理页数据源）。"""
        _require_teacher(request)
        return _ok({"users": users.list_users()})

    @app.post("/users")
    def users_add(rec: dict, request: "fastapi.Request"):
        """新增学生（教师专属）。"""
        _require_teacher(request)
        user = str(rec.get("user") or "").strip() if isinstance(rec, dict) else ""
        password = str(rec.get("password") or "") if isinstance(rec, dict) else ""
        try:
            users.add_user(user, password, "student")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"user": user, "role": "student"})

    @app.delete("/users/{name}")
    def users_del(name: str, request: "fastapi.Request"):
        """删除学生（教师专属；教师账号拒删）。"""
        _require_teacher(request)
        try:
            users.delete_user(name)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"deleted": name})

    @app.get("/plan/goal-progress")
    def plan_goal_progress(request: "fastapi.Request", user: str = "malin"):
        """总目标进度（六维度 + 总%）。教师专属。"""
        _require_teacher(request)
        return _ok(plan.goal_progress(user=user))

    @app.get("/plan/goals")
    def plan_goals_get(request: "fastapi.Request", user: str = "malin"):
        """学生总目标（冲刺目标：讲次/FCE/词汇/泛读书）。教师专属。"""
        _require_teacher(request)
        return _ok({"user": user, "goals": plan.get_goals(user)})

    @app.put("/plan/goals")
    def plan_goals_put(request: "fastapi.Request", payload: dict):
        """保存学生总目标（教师；goals 为自由 JSON，前端结构化生成）。"""
        _require_teacher(request)
        user = (payload.get("user") or "").strip() if isinstance(payload, dict) else ""
        goals = payload.get("goals") if isinstance(payload, dict) else None
        if not user or not isinstance(goals, dict):
            raise HTTPException(status_code=422, detail="user/goals 必填且 goals 须为对象")
        return _ok({"user": user, "goals": plan.put_goals(user, goals)})

    @app.get("/plan/editor-data")
    def plan_editor_data(request: "fastapi.Request", user: str = "malin"):
        """周计划编辑器结构化选项源：讲次/FCE Part/书目(含必读章配置)/词汇现况。教师专属。"""
        _require_teacher(request)
        return _ok(plan.editor_data(user=user))

    @app.get("/plan/month-view")
    def plan_month_view(request: "fastapi.Request", month: str = "",
                        user: str = "malin"):
        """教师月历：某月逐日任务完成格子（周一为每周之首）。"""
        _require_teacher(request)
        from ..clock import today_cn
        if not month:
            month = today_cn().isoformat()[:7]
        try:
            return _ok(plan.month_view(month, user=user))
        except (ValueError, IndexError):
            raise HTTPException(status_code=422, detail="month 须为 YYYY-MM")

    @app.get("/plan/overview")
    def plan_overview(request: "fastapi.Request", user: str = "malin"):
        """教师全览：最早已排计划周到冲刺线的逐周汇总。"""
        _require_teacher(request)
        return _ok(plan.overview(user=user))

    # ---- 内循环（双循环教学）：学生今日任务 / 周清单 / 打卡 ----
    # 学生读自己的；教师可指定 user 查看孩子执行情况（与 recite 同模式）。

    @app.get("/plan/today")
    def plan_today(request: "fastapi.Request", user: str = "malin",
                   day: Optional[str] = None):
        """学生今日任务清单。完成只看学习信号，不看手动打卡。"""
        u = user if request.state.role == "teacher" else request.state.user
        try:
            return _ok(plan.today_tasks(user=u, day=day))
        except ValueError:
            raise HTTPException(status_code=422, detail="day 须为 YYYY-MM-DD")

    @app.get("/plan/week-todo")
    def plan_week_todo(request: "fastapi.Request", user: str = "malin"):
        """学生周清单：本周目标 + 逐日完成率（信号）+ 每条任务的 why。"""
        u = user if request.state.role == "teacher" else request.state.user
        return _ok(plan.week_todo(user=u))

    @app.post("/plan/day-check")
    def plan_day_check(request: "fastapi.Request", payload: dict):
        """写入 plan_day_checks。该表不参与 done / 完成率。

        完成只看学习信号。这里只留教师或学生的干预记录，打卡不是完成的前提。
        """
        auth_user = request.state.user
        is_teacher = request.state.role == "teacher"
        user = (payload.get("user") or "").strip() if isinstance(payload, dict) else ""
        day = str(payload.get("day") or "") if isinstance(payload, dict) else ""
        task_key = str(payload.get("task_key") or "") if isinstance(payload, dict) else ""
        on = bool(payload.get("on")) if isinstance(payload, dict) else False
        if not is_teacher and user != auth_user:
            raise HTTPException(status_code=403, detail="只能操作自己的任务")
        if not user or not day or not task_key:
            raise HTTPException(status_code=422, detail="user/day/task_key 必填")
        try:
            plan.put_day_check(user, day, task_key, on)
            return _ok({
                "user": user, "day": day, "task_key": task_key, "on": on,
                "affects_done": False,
                "note": "打卡不计入完成率；完成只看学习信号",
            })
        except ValueError:
            raise HTTPException(status_code=422, detail="day 须为 YYYY-MM-DD")

    @app.post("/plan/history-day")
    def plan_history_freeze(request: "fastapi.Request", payload: dict):
        """教师冻结某日快照。历史表是可选回填，不是每日完成流水。

        不传 tasks：按当前完成判定原样冻住，不把未完成项改成完成。
        传 tasks：只保存给出的行，服务端不补造任务或完成记录。
        """
        _require_teacher(request)
        body = payload if isinstance(payload, dict) else {}
        user = str(body.get("user") or "").strip()
        day = str(body.get("day") or "").strip()
        if not user or not day:
            raise HTTPException(status_code=422, detail="user/day 必填")
        tasks = body.get("tasks") if "tasks" in body else None
        try:
            return _ok(plan.freeze_history_day(user[:32], day, tasks))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # ---- 学情分析 · AI 周报（手动触发，分析上一自然周；教师专属） ----

    @app.get("/analytics/ai/reports")
    def analytics_ai_reports(request: "fastapi.Request", limit: int = 12):
        """已生成的 AI 周报列表（最近优先）。"""
        _require_teacher(request)
        return _ok({"reports": analytics_ai.list_reports(limit=limit)})

    @app.post("/analytics/ai/weekly")
    def analytics_ai_weekly(request: "fastapi.Request"):
        """手动触发生成上周学情分析：聚合四类作业 + 前四周基线 → GLM → 存档。

        模型与 Key 复用泛读馆设置（library_settings.ai_json）；按周覆盖存档。
        """
        from .. import glm as _glm

        _require_teacher(request)
        settings = library.get_settings()
        try:
            report = analytics_ai.generate_weekly(
                settings,
                stores={
                    "exams": exams,
                    "fce_submissions": fce_submissions,
                    "reading": reading,
                    "recite": recite,
                },
            )
        except _glm.GlmError as e:
            raise HTTPException(status_code=getattr(e, "status", 502), detail=str(e))
        return _ok(report)


    # ---- 作业成绩（可写；独立 exam.db） ----

    @app.get("/exams")
    def exams_list(request: "fastapi.Request", user: Optional[str] = None):
        # 学生只看自己的成绩；教师可用 ?user= 筛选（缺省全部）
        who = user
        if getattr(request.state, "role", "") != "teacher":
            who = _request_user(request)
        return _ok(exams.list(user=who))

    @app.post("/exams")
    def exams_add(rec: ExamRecordIn, request: "fastapi.Request"):
        payload = rec.model_dump()
        payload.pop("user", None)  # user 只认登录态，不收客户端声明
        # 归属学生：学生登录只能记自己；教师替孩子登记时可选 ?student=
        owner = _request_user(request)
        if getattr(request.state, "role", "") == "teacher" and payload.get("student"):
            owner = payload["student"]
        payload.pop("student", None)
        return _ok(exams.add(user=owner, **payload))

    @app.put("/exams/{exam_id}")
    def exams_update(exam_id: int, rec: ExamRecordIn, request: "fastapi.Request"):
        _require_teacher(request)
        payload = rec.model_dump()
        # 归属学生随编辑可改（教师修正记错名下的记录）；置空则不动
        owner = payload.pop("student", None) or None
        updated = exams.update(exam_id, **payload, user=owner)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"成绩记录 id={exam_id} 不存在")
        return _ok(updated)

    @app.delete("/exams/{exam_id}")
    def exams_delete(exam_id: int, request: "fastapi.Request"):
        _require_teacher(request)
        if not exams.delete(exam_id):
            raise HTTPException(status_code=404, detail=f"成绩记录 id={exam_id} 不存在")
        return _ok({"id": exam_id})

"""视频学习：视频清单 + 学生复述录音提交 + 教师批改与进度。

学生宇宙（任务制）：只看「被安排过」的视频——安排 = 出现在该生任意一周
plan_weeks.tasks.videos 里；本周计划里的标 week；交过复述的标 done
（未安排但做过 = 计划外，仍可回看）。详情/提交同样按此门禁。
教师：清单全量（include_disabled），进度按学生分类查看。
"""
from __future__ import annotations

from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException, Response

from ..clock import current_monday
from .http import _ok, _request_user, _require_teacher
from .schemas import VideoItemIn, VideoRetellIn, VideoGradeIn


def register_video(app, ctx):
    videos = ctx.videos
    plan = ctx.plan

    # ---- 学生视频宇宙：安排并集 + 本周集合 ----

    def _scope(user: str) -> tuple[set, set]:
        assigned: set = set()
        week: set = set()
        try:
            cur = current_monday().isoformat()
            for w in plan.list_weeks(user=user) or []:
                for v in (w.get("tasks") or {}).get("videos") or []:
                    v = str(v).strip()
                    if v:
                        assigned.add(v)
                        if w.get("week_start") == cur:
                            week.add(v)
        except Exception:  # noqa: BLE001  计划表异常时退化为「只看过做过的」
            pass
        return assigned, week

    def _student_view(user: str) -> dict:
        """该生的视频清单（含状态）+ 四类分组。"""
        assigned, week = _scope(user)
        stats = videos.retell_stats(user)
        items = []
        for v in videos.list_videos(include_disabled=True):
            if not v["enabled"]:
                continue
            st = stats.get(v["vid"])
            planned = v["vid"] in assigned
            if not planned and not st:
                continue  # 未安排且没做过：不可见
            items.append({
                "vid": v["vid"], "title": v["title"],
                "category": v["category"], "duration_sec": v["duration_sec"],
                "vocab": v["vocab"], "hints": v["hints"],
                "week": v["vid"] in week, "planned": planned,
                "done": bool(st), "retell_n": st["n"] if st else 0,
                "best_score": st["best"] if st else None,
                "pending": bool(st and st["pending"]),
                "last_at": st["last_at"] if st else None,
                "last_comment": st["last_comment"] if st else "",
            })
        return {
            "user": user,
            "videos": items,
            "categories": {
                "week": [x["vid"] for x in items if x["week"]],
                "todo": [x["vid"] for x in items if x["planned"] and not x["done"]],
                "done": [x["vid"] for x in items if x["done"]],
                "extra": [x["vid"] for x in items if not x["planned"]],
            },
        }

    def _student_can_access(user: str, vid: str) -> bool:
        assigned, _ = _scope(user)
        return vid in assigned or vid in videos.retell_stats(user)

    # ---- 视频清单 ----

    @app.get("/video-study")
    def video_study_list(request: "fastapi.Request"):
        """学生：只看被安排（或做过）的视频 + 状态分组。教师：全量清单。"""
        if getattr(request.state, "role", "") != "teacher":
            return _ok(_student_view(request.state.user))
        include = request.query_params.get("include_disabled") == "1"
        return _ok(videos.list_videos(include_disabled=include))

    @app.get("/video-study/progress")
    def video_study_progress(request: "fastapi.Request", user: str = "malin"):
        """教师看某学生的视频进度：本周安排 / 未完成 / 已完成 / 计划外。"""
        _require_teacher(request)
        return _ok(_student_view(str(user)[:32]))

    @app.get("/video-study/items/{vid}")
    def video_study_item(vid: str, request: "fastapi.Request"):
        """单个视频详情。学生仅可读自己被安排（或做过）且在架的视频。"""
        data = videos.get_video(vid)
        if data is None:
            raise HTTPException(status_code=404, detail=f"视频 {vid} 不存在")
        role = getattr(request.state, "role", "")
        if role != "teacher":
            if not data["enabled"]:
                raise HTTPException(status_code=403, detail="该视频已下架")
            if not _student_can_access(request.state.user, vid):
                raise HTTPException(status_code=403, detail="该视频未安排给你")
        return _ok(data)

    # ---- 教师管理（增改删；复述录音保留） ----

    @app.post("/video-study/items")
    def video_item_add(rec: VideoItemIn, request: "fastapi.Request"):
        """新增/覆盖一个视频条目（同 vid 覆盖）。教师。"""
        _require_teacher(request)
        try:
            return _ok(videos.upsert_video(
                rec.vid, rec.title, rec.category, rec.duration_sec,
                vocab=rec.vocab, hints=rec.hints, enabled=rec.enabled))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.put("/video-study/items/{vid}")
    def video_item_put(vid: str, rec: VideoItemIn, request: "fastapi.Request"):
        """编辑视频条目（标题/类目/生僻词/提示/上下架）。教师。"""
        _require_teacher(request)
        if rec.vid != vid:
            raise HTTPException(status_code=422, detail="body.vid 与路径不一致")
        try:
            return _ok(videos.upsert_video(
                vid, rec.title, rec.category, rec.duration_sec,
                vocab=rec.vocab, hints=rec.hints, enabled=rec.enabled))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.delete("/video-study/items/{vid}")
    def video_item_delete(vid: str, request: "fastapi.Request"):
        """删除视频条目（复述录音保留）。教师。"""
        _require_teacher(request)
        if not videos.delete_video(vid):
            raise HTTPException(status_code=404, detail=f"视频 {vid} 不存在")
        return _ok({"vid": vid})

    # ---- 复述录音（学生提交，教师批改；与精读录音同模式） ----

    @app.post("/video-study/retells")
    def video_retell_submit(rec: VideoRetellIn, request: "fastapi.Request"):
        """学生看完视频后提交复述录音（pending 待教师批改）。

        仅可提交被安排给自己（或此前做过）的视频。
        """
        user = _request_user(request)
        if getattr(request.state, "role", "") != "teacher" \
                and not _student_can_access(user, rec.vid):
            raise HTTPException(status_code=403, detail="该视频未安排给你，不能提交复述")
        try:
            return _ok(videos.submit_retell(
                user, rec.vid, rec.audio_b64, rec.mime, rec.duration_sec))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'"))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/video-study/retells")
    def video_retell_list(
        request: "fastapi.Request", user: Optional[str] = None,
        status: Optional[str] = None, limit: int = 100,
    ):
        """复述录音列表。学生只看自己的；教师看全部（?status=pending 待批改）。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(videos.list_retells(user=user, status=status, limit=limit))

    @app.get("/video-study/retells/{retell_id}/audio")
    def video_retell_audio(retell_id: int, request: "fastapi.Request"):
        """复述录音二进制流（<audio src> 直连，token 走查询参数）。
        学生只能听自己的。"""
        data = videos.get_retell(retell_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"复述录音 id={retell_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能播放自己的录音")
        import base64 as _b64
        try:
            raw = _b64.b64decode(data.get("audio_b64") or "")
        except Exception:
            raise HTTPException(status_code=500, detail="音频数据损坏")
        return Response(content=raw, media_type=data.get("mime") or "audio/mp4")

    @app.put("/video-study/retells/{retell_id}")
    def video_retell_grade(
        retell_id: int, rec: VideoGradeIn, request: "fastapi.Request"
    ):
        """教师给复述录音打分（10 分制 + 评语）。"""
        _require_teacher(request)
        try:
            data = videos.grade_retell(retell_id, rec.score, rec.comment)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if data is None:
            raise HTTPException(status_code=404, detail=f"复述录音 id={retell_id} 不存在")
        return _ok(data)

    @app.delete("/video-study/retells/{retell_id}")
    def video_retell_delete(retell_id: int, request: "fastapi.Request"):
        """教师删除一条复述录音。"""
        _require_teacher(request)
        if not videos.delete_retell(retell_id):
            raise HTTPException(status_code=404, detail=f"复述录音 id={retell_id} 不存在")
        return _ok({"id": retell_id})

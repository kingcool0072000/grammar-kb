"""FCE 真题练习与精读录音（含听力/范读音频文件）。"""
from __future__ import annotations

import os
import re as _re
from pathlib import Path as _P
from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse

from ..paths import data_path
from .http import _ok, _request_user, _require_teacher
from .schemas import FceSubmissionIn, FceGradeIn, ReadingDerivedIn, ReadingUpdateIn, ReadingRecordIn, ReadingGradeIn

def register_practice(app, ctx):
    fce_papers = ctx.fce_papers
    fce_submissions = ctx.fce_submissions
    reading = ctx.reading
    recite = ctx.recite
    audio_dir = ctx.audio_dir

    # ---- FCE 真题（只读；独立 data/fce.db，由 fce_paper 模块入库） ----

    @app.get("/fce-papers")
    def fce_papers_list():
        """FCE 青少版模拟卷概览：4 套 Test 各 paper/part 的题数。"""
        return _ok(fce_papers.list_papers())

    @app.get("/fce-papers/{test_id}")
    def fce_paper_detail(test_id: int, request: "fastapi.Request"):
        """单套 FCE Test 全部内容。学生版剥离答案/关键词（练习用），教师版完整。"""
        data = fce_papers.get_paper(test_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"FCE Test {test_id} 不存在")
        if getattr(request.state, "role", "") != "teacher":
            for sec in data["sections"]:
                for q in sec["questions"]:
                    q["answer"] = ""
        return _ok(data)

    @app.post("/fce-submissions")
    def fce_submit(rec: FceSubmissionIn, request: "fastapi.Request"):
        """提交一次大题练习：客观题自动批改；作文转待教师批改。"""
        user = _request_user(request)
        try:
            data = fce_submissions.submit(
                user, rec.test_id, rec.paper, rec.part, rec.answers,
                duration_sec=rec.duration_sec,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'"))
        return _ok(data)

    @app.get("/fce-submissions")
    def fce_submissions_list(
        request: "fastapi.Request",
        user: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ):
        """练习历史。学生只能看自己的；教师可看全部（?status=pending 拉待批改作文）。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(
            fce_submissions.list(
                user=(user or None), status=(status or None), limit=limit
            )
        )

    @app.get("/fce-submissions/{sub_id}")
    def fce_submission_detail(sub_id: int, request: "fastapi.Request"):
        """提交详情（含逐题对错明细）。学生只能看自己的，防越权读他人提交。"""
        data = fce_submissions.get(sub_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能查看自己的练习提交")
        return _ok(data)

    @app.put("/fce-submissions/{sub_id}")
    def fce_grade(sub_id: int, rec: FceGradeIn, request: "fastapi.Request"):
        """教师批改作文（打分 + 评语）。端点再拦一次，不单靠中间件前缀白名单。"""
        _require_teacher(request)
        data = fce_submissions.grade(sub_id, rec.teacher_score, rec.teacher_comment)
        if data is None:
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        return _ok(data)

    @app.delete("/fce-submissions/{sub_id}")
    def fce_delete(sub_id: int, request: "fastapi.Request"):
        """教师删除一条 FCE 练习提交。"""
        if getattr(request.state, "role", "") != "teacher":
            raise HTTPException(status_code=403, detail="该功能仅教师账号可用")
        if not fce_submissions.delete(sub_id):
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        return _ok({"id": sub_id})

    # ---- 阅读训练（base 原文段 + derived 派生文 + 录音提交/批改） ----

    @app.get("/reading/articles")
    def reading_articles(request: "fastapi.Request", kind: Optional[str] = None):
        """文章列表。默认只返回派生文（阅读练习用）；教师传 kind=base 看原文段。"""
        is_teacher = getattr(request.state, "role", "") == "teacher"
        if kind == "base":
            if not is_teacher:
                raise HTTPException(status_code=403, detail="原文段落列表仅教师可查")
            return _ok(reading.list_articles(kind="base"))
        if kind == "all" and is_teacher:
            return _ok(reading.list_articles(kind=None))
        return _ok(reading.list_articles(kind="derived"))

    @app.get("/reading/articles/{article_id}")
    def reading_article_detail(article_id: int, request: "fastapi.Request"):
        """文章正文（has_audio 标记是否备有范读 WAV）。学生访问 base 原文返回 403。"""
        data = reading.get_article(article_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"文章 id={article_id} 不存在")
        if data["kind"] == "base" and getattr(request.state, "role", "") != "teacher":
            raise HTTPException(status_code=403, detail="原文段落仅教师可读，学生请练习派生文章")
        data["has_audio"] = (audio_dir / f"{article_id}.wav").is_file()
        return _ok(data)

    @app.get("/reading/articles/{article_id}/tokens")
    def reading_article_tokens(article_id: int, request: "fastapi.Request"):
        """单篇逐词标注（学生端精读辅助：句首/连词/动词/词层级）。"""
        data = reading.get_article(article_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"文章 id={article_id} 不存在")
        if data["kind"] == "base" and getattr(request.state, "role", "") != "teacher":
            raise HTTPException(status_code=403, detail="原文段落仅教师可读")
        return _ok(reading.annotate(article_id))

    @app.get("/reading/difficulty")
    def reading_difficulty(request: "fastapi.Request", ids: Optional[str] = None):
        """精读难度评价（教师备课用）：LX 词汇分布 / 语法结构（映射哈1讲次）/ 题材。

        ids=1,2,3 限定文章；缺省全部派生文。
        """
        if getattr(request.state, "role", "") != "teacher":
            raise HTTPException(status_code=403, detail="难度评价仅教师账号可用")
        id_list: Optional[list[int]] = None
        if ids:
            try:
                id_list = [int(x) for x in ids.split(",")][:50]
            except ValueError:
                raise HTTPException(status_code=400, detail="ids 格式应为逗号分隔的文章 id")
        return _ok(reading.difficulty(id_list))

    @app.get("/reading/audio/{article_id}")
    def reading_article_audio_file(article_id: int):
        """派生文范读 WAV 文件流。"""
        path = audio_dir / f"{article_id}.wav"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="该文章暂无范读音频")
        return FileResponse(path, media_type="audio/wav")

    # ---- FCE 听力音频（文件到位即生效；占位目录 data/audio/fce/） ----
    # 命名约定：data/audio/fce/{test_id}/listening_p{n}.m4a（afconvert 48k
    # mono AAC，原 253MB 压到 65MB；旧 .mp3 仍兼容）；404=音频未就绪。
    fce_audio_dir = _P(os.environ.get("GRAMMAR_KB_FCE_AUDIO_DIR")
                       or data_path("audio", "fce"))
    _AUDIO_MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}

    @app.get("/fce-papers/{test_id}/audio/{key}")
    def fce_audio_file(test_id: int, key: str):
        """FCE 听力音频文件流（key 如 listening_p1；师生均可听）。"""
        safe = _re.sub(r"[^a-z0-9_]", "", key.lower())
        if not safe:
            raise HTTPException(status_code=422, detail="非法音频 key")
        d = fce_audio_dir / str(int(test_id))
        for ext in (".m4a", ".mp3", ".wav"):
            path = d / f"{safe}{ext}"
            if path.is_file():
                return FileResponse(path, media_type=_AUDIO_MIME[ext],
                                    headers={"Accept-Ranges": "bytes"})
        raise HTTPException(status_code=404, detail="该部分听力音频暂未就绪")

    @app.get("/fce-papers/{test_id}/listening-vocab")
    def fce_listening_vocab(test_id: int, request: "fastapi.Request"):
        """听力预习词表：按 Part 分组的「当前学生未掌握词」（做题前看）。"""
        user = _request_user(request)
        out = fce_papers.listening_vocab(
            test_id, user, lambda u: recite.mastered_words(u))
        if out is None:
            raise HTTPException(status_code=404, detail=f"Test {test_id} 不存在")
        return _ok(out)

    @app.get("/fce-papers/{test_id}/audio")
    def fce_audio_status(test_id: int):
        """某 Test 已就绪的听力音频 key 列表（前端按 key 显隐播放器）。"""
        d = fce_audio_dir / str(int(test_id))
        if not d.is_dir():
            return _ok({"available": []})
        keys = {p.stem for p in d.glob("*.m4a")}
        keys |= {p.stem for p in d.glob("*.mp3")}
        return _ok({"available": sorted(keys)})

    @app.post("/reading/articles")
    def reading_add_derived(rec: ReadingDerivedIn, request: "fastapi.Request"):
        """教师新增派生阅读文（挂到某个 base 段）。"""
        _require_teacher(request)
        try:
            return _ok(reading.add_derived(rec.base_key, rec.title, rec.text, rec.source))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.put("/reading/articles/{article_id}")
    def reading_update_derived(
        article_id: int, rec: ReadingUpdateIn, request: "fastapi.Request"
    ):
        """教师编辑派生阅读文。"""
        _require_teacher(request)
        data = reading.update_derived(
            article_id, rec.title, rec.text, rec.source, rec.base_key
        )
        if data is None:
            raise HTTPException(status_code=404, detail=f"派生文章 id={article_id} 不存在")
        return _ok(data)

    @app.delete("/reading/articles/{article_id}")
    def reading_delete_derived(article_id: int, request: "fastapi.Request"):
        """教师删除派生阅读文（base 原文段不可删）。"""
        _require_teacher(request)
        if not reading.delete_derived(article_id):
            raise HTTPException(status_code=404, detail=f"派生文章 id={article_id} 不存在")
        return _ok({"id": article_id})

    @app.post("/reading/recordings")
    def reading_submit_recording(rec: ReadingRecordIn, request: "fastapi.Request"):
        """学生提交阅读录音（一篇文章录一段）。"""
        user = _request_user(request)
        try:
            return _ok(reading.submit_recording(
                user, rec.article_id, rec.audio_b64, rec.mime, rec.duration_sec,
                selected_text=rec.selected_text,
            ))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'"))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/reading/recordings")
    def reading_recordings(
        request: "fastapi.Request", user: Optional[str] = None,
        status: Optional[str] = None, limit: int = 100,
    ):
        """录音列表。学生只看自己的；教师可看全部（?status=pending 拉待批改）。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(reading.list_recordings(user=user, status=status, limit=limit))

    @app.get("/reading/recordings/{rec_id}")
    def reading_recording_detail(rec_id: int, request: "fastapi.Request"):
        """录音详情（含 base64 音频，供播放）。学生只能听自己的。"""
        data = reading.get_recording(rec_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能查看自己的录音")
        return _ok(data)

    @app.get("/reading/recordings/{rec_id}/audio")
    def reading_recording_audio(rec_id: int, request: "fastapi.Request"):
        """录音音频二进制流（<audio src> 直连：浏览器按需流式拉取，
        替代详情 JSON 里 2MB+ 的 base64——批改首开全量预取的主瓶颈）。
        权限同详情：学生只能听自己的。token 走查询参数（audio 标签无法带
        Authorization 头），仅读且短时效场景可接受。"""
        data = reading.get_recording(rec_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能播放自己的录音")
        import base64 as _b64
        try:
            raw = _b64.b64decode(data.get("audio_b64") or "")
        except Exception:
            raise HTTPException(status_code=500, detail="音频数据损坏")
        return Response(content=raw, media_type=data.get("mime") or "audio/mp4")

    @app.put("/reading/recordings/{rec_id}")
    def reading_grade_recording(rec_id: int, rec: ReadingGradeIn, request: "fastapi.Request"):
        """教师给录音打分（10 分制 + 评语）。"""
        _require_teacher(request)
        try:
            data = reading.grade_recording(rec_id, rec.score, rec.comment)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if data is None:
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        return _ok(data)

    @app.delete("/reading/recordings/{rec_id}")
    def reading_delete_recording(rec_id: int, request: "fastapi.Request"):
        """教师删除一条录音提交。"""
        _require_teacher(request)
        if not reading.delete_recording(rec_id):
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        return _ok({"id": rec_id})

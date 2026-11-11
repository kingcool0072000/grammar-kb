"""请求体模型。

定义在模块级：server 启用延迟注解，闭包内的模型无法被 FastAPI 解析。
未安装 pydantic 时这些名字为 None（与拆分前 ``server.py`` 相同）。
"""
from __future__ import annotations

import re
from typing import Optional

from ..audioutil import MAX_AUDIO_B64

try:
    from pydantic import BaseModel, Field, field_validator, model_validator

    class ExamRecordIn(BaseModel):
        """作业成绩请求体（模块级定义：本文件启用延迟注解，闭包内模型无法被 FastAPI 解析）。"""

        lecture: int = Field(ge=1, le=99, description="讲次")
        date: str = Field(description="作答日期 YYYY-MM-DD")
        score: int = Field(default=0, ge=0, le=100)
        wrong: list[int] = Field(default_factory=list, description="错题题号")
        kind: str = Field(default="lecture", description="类型：lecture=课程测验 / hw=作业卷")
        # 成绩归属学生：教师替孩子登记时传（缺省=登记人自己）；学生
        # 自己登录提交则始终记自己，防止伪造他人记录
        student: Optional[str] = Field(default=None, max_length=32)

    class LoginIn(BaseModel):
        """登录请求体。"""

        user: str = Field(min_length=1, max_length=32)
        password: str = Field(min_length=1, max_length=64)

    class FceSubmissionIn(BaseModel):
        """FCE 大题练习提交（模块级定义：延迟注解下闭包内模型无法被解析）。"""

        test_id: int = Field(ge=1, le=4)
        paper: str
        part: int = Field(ge=0, le=7)
        answers: dict = Field(default_factory=dict)
        duration_sec: Optional[int] = Field(default=None, ge=0, le=24 * 3600)

    class FceGradeIn(BaseModel):
        """教师批改作文。"""

        teacher_score: Optional[int] = Field(default=None, ge=0, le=100)
        teacher_comment: str = ""

    class ReadingDerivedIn(BaseModel):
        """教师新增派生阅读文。"""

        base_key: str = Field(min_length=1, max_length=32)
        title: str = Field(default="", max_length=120)
        text: str = Field(min_length=20)
        source: str = Field(default="", max_length=200)

    class ReadingUpdateIn(BaseModel):
        """教师编辑派生阅读文（全部可选，仅传修改字段）。"""

        title: Optional[str] = Field(default=None, max_length=120)
        text: Optional[str] = None
        source: Optional[str] = Field(default=None, max_length=200)
        base_key: Optional[str] = Field(default=None, max_length=32)

    class ReadingRecordIn(BaseModel):
        """学生提交阅读录音（base64 webm，≤5 分钟，附选中的朗读文本）。"""

        article_id: int = Field(ge=1)
        audio_b64: str = Field(min_length=8)
        mime: str = Field(default="audio/webm", max_length=40)
        duration_sec: int = Field(default=0, ge=0, le=300)
        selected_text: str = Field(default="", max_length=4000)

    class ReadingGradeIn(BaseModel):
        """教师给录音打分（10 分制）。"""

        score: int = Field(ge=0, le=10)
        comment: str = Field(default="", max_length=2000)

    class ReciteSessionIn(BaseModel):
        """学生背单词一组练习的成绩上报。"""

        total: int = Field(ge=1, le=500)
        wrong: int = Field(default=0, ge=0, le=500)
        acc: int = Field(default=0, ge=0, le=100)
        duration_sec: int = Field(default=0, ge=0, le=3600)
        wrong_words: list = Field(default_factory=list)
        mode: str = Field(default="", max_length=20)
        scope: str = Field(default="", max_length=20)
        details: Optional[list] = None  # 逐词对错明细 [{word, correct}]，落云端逐词进度

    class FocusSessionIn(BaseModel):
        """泛读馆阅读器专注力心跳上报（累计值，按 session_id 幂等覆盖）。"""
        module: str = Field(default="library", max_length=16)
        range_start: Optional[float] = None
        range_end: Optional[float] = None
        range_label: str = Field(default="", max_length=120)
        lookup_words: Optional[list] = None
        speak_words: Optional[list] = None
        selected_words: Optional[list] = None
        activity: Optional[list] = None
        # v3 位置轨迹 [{t秒, p%}]：段级有效性（跳章剔除/短停留段剔除）
        percent_track: Optional[list] = None

        session_id: str = Field(min_length=1, max_length=64)
        book_id: Optional[int] = None
        book_title: str = Field(default="", max_length=200)
        started_at: str = Field(default="", max_length=32)
        ended_at: str = Field(default="", max_length=32)
        total_sec: int = Field(default=0, ge=0, le=14400)
        active_sec: int = Field(default=0, ge=0, le=14400)
        away_sec: int = Field(default=0, ge=0, le=14400)
        longest_away_sec: int = Field(default=0, ge=0, le=14400)
        away_count: int = Field(default=0, ge=0, le=9999)
        scroll_count: int = Field(default=0, ge=0, le=9999)
        chapter_navs: int = Field(default=0, ge=0, le=9999)
        lookups: int = Field(default=0, ge=0, le=9999)
        plays: int = Field(default=0, ge=0, le=9999)
        prep_opens: int = Field(default=0, ge=0, le=9999)
        prep_starts: int = Field(default=0, ge=0, le=9999)
        fast_scroll_flags: int = Field(default=0, ge=0, le=9999)
        percent_start: float = Field(default=0.0, ge=0, le=100)
        percent_end: float = Field(default=0.0, ge=0, le=100)
        mouse_metrics: Optional[dict] = None
        polyline: Optional[str] = Field(default=None, max_length=200000)

    class TopicProgressIn(BaseModel):
        """专题学习「我学完了」进度覆盖（done_keys＝已学完主题 key 集合）。"""

        done_keys: list[str] = Field(default_factory=list)
        assigned_user: str = Field(default="", max_length=60)

    class TopicImportIn(BaseModel):
        """结构化专题 JSON 导入：meta（标题/emoji/开放学生）+ content（nodes 树）。"""

        meta: Optional[dict] = None
        content: Optional[dict] = None

    class TopicFlagIn(BaseModel):
        on: bool = False

    class TopicAssignIn(BaseModel):
        assigned_to: str = Field(default="", max_length=240)

    class VideoItemIn(BaseModel):
        """教师新增/编辑视频学习条目。"""

        vid: str = Field(min_length=11, max_length=16)
        title: str = Field(min_length=1, max_length=120)
        category: str = Field(default="", max_length=24)
        duration_sec: int = Field(default=0, ge=0, le=4 * 3600)
        vocab: Optional[list] = None
        hints: str = Field(default="", max_length=2000)
        enabled: bool = True

    class VideoRetellIn(BaseModel):
        """学生提交复述录音（base64，≤10 分钟）。

        ref_kind=video 时 vid=YouTube 11 位 ID（默认，兼容旧前端）；
        ref_kind=pattern 时 vid=句型 pattern_item.id（数字字符串）。
        """

        vid: str = Field(min_length=1, max_length=16)
        audio_b64: str = Field(min_length=8)
        mime: str = Field(default="audio/webm", max_length=40)
        duration_sec: int = Field(default=0, ge=0, le=600)
        ref_kind: str = Field(default="video", max_length=16)

        # pattern id 在库里是自增整数，JSON 序列化成数字（patterns API
        # 原样返回 int）——前端漏 String() 时宽容强转，别让它 422
        @field_validator("vid", mode="before")
        @classmethod
        def _vid_str(cls, v):
            return str(v) if isinstance(v, int) and not isinstance(v, bool) else v

        @model_validator(mode="after")
        def _check_vid_shape(self):
            if self.ref_kind == "pattern":
                if not self.vid.isdigit():
                    raise ValueError("ref_kind=pattern 时 vid 须为句型 id（纯数字）")
            elif not re.fullmatch(r"[A-Za-z0-9_-]{11}", self.vid):
                raise ValueError("vid 须为 YouTube 视频 ID（11 位字母数字）")
            return self

    class PatternItemIn(BaseModel):
        """单条句型（提升档「句型跟读复述」素材，导入用）。"""

        lecture: int = Field(ge=1, le=99)
        sentence: str = Field(min_length=1, max_length=500)
        translation: str = Field(default="", max_length=500)
        hints: list[str] = Field(default_factory=list, max_length=8)

    class PatternsImportIn(BaseModel):
        """句型批量导入 body：{"items": [PatternItemIn, ...]}。"""

        items: list[PatternItemIn] = Field(min_length=1, max_length=200)

    class VideoGradeIn(BaseModel):
        """教师给复述录音打分（10 分制）。"""

        score: int = Field(ge=0, le=10)
        comment: str = Field(default="", max_length=2000)

    class Nce2SubmitIn(BaseModel):
        """学生提交 NCE2 听力打点/跟读录音（模块级定义，同上）。

        kind=listen：纯打点，不带音频，同日同课幂等；kind=shadow：必带
        audio_b64（base64，webm 自动转 m4a，同视频复述链路）。
        """

        lesson_id: int = Field(ge=1)
        kind: str = Field(default="listen", max_length=16)
        audio_b64: Optional[str] = Field(
            default=None, max_length=MAX_AUDIO_B64)  # 12MB，同 reading/视频复述
        mime: Optional[str] = Field(default=None, max_length=40)
        duration_sec: float = Field(default=0.0, ge=0, le=600)

        @field_validator("kind")
        @classmethod
        def _kind_ok(cls, v):
            if v not in ("listen", "shadow"):
                raise ValueError("kind 须为 listen/shadow")
            return v

        @model_validator(mode="after")
        def _shadow_needs_audio(self):
            if self.kind == "shadow" and not (self.audio_b64 or "").strip():
                raise ValueError("kind=shadow 须带 audio_b64 录音数据")
            return self

    class Nce2GradeIn(BaseModel):
        """教师批改 NCE2 跟读录音（10 分制；越界不拒、由 Store 夹取）。"""

        teacher_score: float
        teacher_comment: str = Field(default="", max_length=2000)

    class MicroAnswerIn(BaseModel):
        """微练习单题作答。"""

        id: int = Field(ge=1)
        choice: str = Field(min_length=1, max_length=8)

    class MicroSubmitIn(BaseModel):
        """学生提交当日微练习作答（1~6 题）。"""

        day: Optional[str] = Field(default=None, max_length=10)
        answers: list[MicroAnswerIn] = Field(min_length=1, max_length=6)

    class MicroQuestionIn(BaseModel):
        """教师手动添加微练习题（默认 draft，上架走 PUT status）。"""

        category: str = Field(default="grammar", max_length=16)
        stem: str = Field(min_length=1, max_length=2000)
        options: list = Field(default_factory=list)
        answer: str = Field(min_length=1, max_length=8)
        topic: str = Field(default="", max_length=120)
        explain: str = Field(default="", max_length=2000)
        source: str = Field(default="manual", max_length=16)
        ref_lecture: Optional[int] = None
        ref_qnum: Optional[int] = None
        status: str = Field(default="draft", max_length=16)

    class MicroQuestionUpdateIn(BaseModel):
        """教师编辑微练习题（仅传修改字段）。"""

        category: Optional[str] = Field(default=None, max_length=16)
        topic: Optional[str] = Field(default=None, max_length=120)
        stem: Optional[str] = Field(default=None, max_length=2000)
        options: Optional[list] = None
        answer: Optional[str] = Field(default=None, max_length=8)
        explain: Optional[str] = Field(default=None, max_length=2000)
        status: Optional[str] = Field(default=None, max_length=16)

except ImportError:  # 未装 fastapi/pydantic 时仍可 import 本模块
    ExamRecordIn = None
    FceSubmissionIn = None
    FceDerivedIn = None
    ReadingDerivedIn = None
    ReadingUpdateIn = None
    ReadingRecordIn = None
    ReadingGradeIn = None
    ReciteSessionIn = None
    FocusSessionIn = None
    TopicProgressIn = None
    VideoItemIn = None
    VideoRetellIn = None
    VideoGradeIn = None
    Nce2SubmitIn = None
    Nce2GradeIn = None
    PatternsImportIn = None
    MicroAnswerIn = None
    MicroSubmitIn = None
    MicroQuestionIn = None
    MicroQuestionUpdateIn = None


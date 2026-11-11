"""NCE2 听力跟读：课文 mp3 + 听力打点（listen）+ 跟读录音批改（shadow）。

与 :class:`VideoStore` 同库（data/fce.db，环境变量 GRAMMAR_KB_FCE_DB 优先）：
- ``nce2_submission``：96 课的提交流水。date=北京钟当日 ISO；kind ∈
  ``{"listen","shadow"}``——listen 是「听完了」的打点（无音频，同日同课
  幂等，status 恒 'done'）；shadow 是跟读录音（base64，webm 自动转 m4a，
  与视频复述同链路，'pending' → 教师批改后 'graded'，同日同课可重复提交，
  新行历史保留）。

课文清单不在库里：静态 manifest（data/nce2/book.json，逐句双语时间轴，
随部署放置）+ 每课一个 mp3（data/audio/nce2/{id}.mp3）。manifest 缺失/
坏 JSON 时接口不崩——清单退化为空表、提交被 ValueError 拦下，等部署
补文件即恢复。

学生宇宙（任务制）：与视频学习同口径——学生只看「被安排过」的课文
（安排 = 出现在该生任意一周 plan_weeks.tasks 的 NCE2 课次里）∪ 自己
提交过的课文；本周计划里的标 week。宇宙判定在路由层（routes_nce2
的 _scope），本模块只管清单与提交流水本身。
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from . import dbreg
from .audioutil import MAX_AUDIO_B64, transcode_for_safari
from .clock import today_cn
from .store_base import connect

logger = logging.getLogger(__name__)

NCE2_SCHEMA = """
CREATE TABLE IF NOT EXISTS nce2_submission (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT NOT NULL,
  lesson_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  kind TEXT NOT NULL,
  audio_b64 TEXT,
  mime TEXT,
  duration_sec REAL,
  status TEXT NOT NULL DEFAULT 'pending',
  teacher_score REAL,
  teacher_comment TEXT,
  graded_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nce2_sub_user_date ON nce2_submission(user, date);
"""

# 跟读录音时长上限（秒）：整课跟读也就几分钟，与视频复述同用 10 分钟档
_MAX_DURATION_SEC = 10 * 60

_KINDS = ("listen", "shadow")


def _now() -> str:
    return datetime.now(_tz.utc).isoformat(timespec="seconds")


def _today() -> str:
    """date 列的「今天」：北京钟当日 ISO（与计划表 today_cn 同一天）。"""
    return today_cn().isoformat()


# ---- 课本 manifest 与音频目录（静态文件，不入库） ----


def nce2_book_path() -> str:
    """book manifest：``$GRAMMAR_KB_NCE2_BOOK`` → data/nce2/book.json。"""
    env = os.environ.get("GRAMMAR_KB_NCE2_BOOK")
    if env:
        return env
    return str(dbreg.data_path("nce2", "book.json"))


def nce2_audio_dir() -> str:
    """课文 mp3 目录：``$GRAMMAR_KB_NCE2_AUDIO_DIR`` → data/audio/nce2/。"""
    env = os.environ.get("GRAMMAR_KB_NCE2_AUDIO_DIR")
    if env:
        return env
    return str(dbreg.data_path("audio", "nce2"))


def _lesson_id_of(les: dict) -> Optional[int]:
    """manifest 行的课次 id（宽容 int 化；手编 JSON 里可能是字符串）。"""
    try:
        return int(les.get("id"))
    except (TypeError, ValueError):
        return None


def load_manifest() -> dict:
    """读课本 manifest（静态文件，量小不缓存，每请求现读）。

    缺文件/坏 JSON/结构不对 → ``{"book": "NCE2", "lessons": []}``：清单
    缺失只降级不崩接口。返回 dict 原样带出 manifest 其余键（book 标题等）。
    """
    try:
        data = json.loads(Path(nce2_book_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"book": "NCE2", "lessons": []}
    if not isinstance(data, dict):
        return {"book": "NCE2", "lessons": []}
    if not isinstance(data.get("lessons"), list):
        data["lessons"] = []
    data.setdefault("book", "NCE2")
    return data


def lessons() -> list[dict]:
    """manifest 全部课文（id 升序；缺清单时为空列表）。"""
    out = [les for les in load_manifest()["lessons"] if isinstance(les, dict)]
    return sorted(
        (les for les in out if _lesson_id_of(les) is not None),
        key=_lesson_id_of,
    )


def lesson(lid) -> Optional[dict]:
    """按课次 id 取 manifest 单课原文；不在清单返回 None。"""
    try:
        lid = int(lid)
    except (TypeError, ValueError):
        return None
    for les in lessons():
        if _lesson_id_of(les) == lid:
            return les
    return None


def lesson_audio_path(lid) -> Path:
    """课文 mp3 实际路径：manifest ``filename`` 优先（导入脚本归一化的
    补零名，如 '01'），退回契约默认 ``{id}.mp3``。文件是否存在由调用方判。
    """
    les = lesson(lid) or {}
    stem = str(les.get("filename") or "").strip() or str(lid)
    return Path(nce2_audio_dir()) / f"{stem}.mp3"


def lesson_meta(les: dict) -> dict:
    """manifest 单课 → 前端行：id/标题/句数/词数/mp3 是否已放置。

    manifest 由上游脚本生成，字段名按宽容口径读：句数取 n_sentences 或
    len(sentences)；词数取 n_words 或 len(words/new_words/vocab)。
    """
    lid = _lesson_id_of(les)
    n_sentences = les.get("n_sentences")
    if not isinstance(n_sentences, int):
        sents = les.get("sentences")
        n_sentences = len(sents) if isinstance(sents, list) else 0
    n_words = les.get("n_words")
    if not isinstance(n_words, int):
        n_words = 0
        for key in ("words", "new_words", "vocab"):
            w = les.get(key)
            if isinstance(w, list):
                n_words = len(w)
                break
    return {
        "id": lid,
        "title": str(les.get("title") or ""),
        "n_sentences": n_sentences,
        "n_words": n_words,
        "has_audio": lesson_audio_path(lid).is_file(),
    }


def _sub_out(r: sqlite3.Row, with_audio: bool = False) -> dict:
    """提交行 → 前端 dict（MB 级音频默认不带，列表端点绝不能漏出去）。"""
    d = {
        "id": r["id"], "user": r["user"], "lesson_id": r["lesson_id"],
        "date": r["date"], "kind": r["kind"],
        "mime": r["mime"], "duration_sec": r["duration_sec"],
        "status": r["status"], "teacher_score": r["teacher_score"],
        "teacher_comment": r["teacher_comment"],
        "created_at": r["created_at"], "graded_at": r["graded_at"],
    }
    if with_audio:
        d["audio_b64"] = r["audio_b64"]
    return d


class Nce2Store:
    """nce2_submission 读写（fce.db 同库）。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or dbreg.fce_db_path()
        if not Path(self.db_path).exists():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # 幂等建表（无种子：课文清单在 book.json，不在库里）
        with self._connect() as conn:
            conn.executescript(NCE2_SCHEMA)

    def _connect(self):
        return connect(self.db_path)

    # ---- 提交 ----

    def submit(self, user: str, lesson_id, kind: str, audio_b64: Optional[str] = None,
               mime: str = "", duration_sec: float = 0) -> dict:
        """听力打点 / 跟读录音提交，返回落库后的行 dict（不含音频）。

        - lesson 必须在 manifest 里，否则 ValueError（路由转 422）；
        - kind=listen：无音频，同 user+lesson+date 已有行直接返回旧行
          （幂等——前端重试/重复点击不产生第二条）；
        - kind=shadow：必带 audio_b64，经 audioutil 转 m4a（同视频复述
          链路）；同日同课允许再次提交（新行，历史保留给教师回看）。
        """
        user = (user or "").strip()[:32]
        kind = (kind or "").strip()
        if kind not in _KINDS:
            raise ValueError(f"未知的提交类型 {kind}（须为 listen/shadow）")
        try:
            lid = int(lesson_id)
        except (TypeError, ValueError):
            raise ValueError(f"课次 id 无效: {lesson_id!r}") from None
        if lesson(lid) is None:
            raise ValueError(f"课文 {lid} 不在 NCE2 清单里")
        day = _today()
        if kind == "listen":
            with self._connect() as conn:
                old = conn.execute(
                    "SELECT * FROM nce2_submission"
                    " WHERE user = ? AND lesson_id = ? AND date = ? AND kind = 'listen'",
                    (user, lid, day),
                ).fetchone()
                if old is not None:
                    return _sub_out(old)
                cur = conn.execute(
                    "INSERT INTO nce2_submission"
                    " (user, lesson_id, date, kind, status, created_at)"
                    " VALUES (?,?,?,'listen','done',?)",
                    (user, lid, day, _now()),
                )
                row = conn.execute(
                    "SELECT * FROM nce2_submission WHERE id = ?",
                    (cur.lastrowid,)).fetchone()
            return _sub_out(row)

        audio_b64 = (audio_b64 or "").strip()
        if not audio_b64:
            raise ValueError("跟读录音数据为空")
        if len(audio_b64) > MAX_AUDIO_B64:
            raise ValueError("录音过大（超过 12MB），请缩短录音时长")
        try:
            base64.b64decode(audio_b64, validate=False)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"录音数据无效: {e}") from e
        try:
            dur = max(0.0, min(float(duration_sec or 0), float(_MAX_DURATION_SEC)))
        except (TypeError, ValueError):
            dur = 0.0
        audio_b64, mime = transcode_for_safari(audio_b64, mime or "audio/webm")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO nce2_submission"
                " (user, lesson_id, date, kind, audio_b64, mime, duration_sec,"
                " status, created_at)"
                " VALUES (?,?,?,'shadow',?,?,?, 'pending', ?)",
                (user, lid, day, audio_b64, mime or "audio/webm", dur, _now()),
            )
            row = conn.execute(
                "SELECT * FROM nce2_submission WHERE id = ?",
                (cur.lastrowid,)).fetchone()
        return _sub_out(row)

    # ---- 查询 ----

    def lessons_status(self, user: str) -> dict[int, dict]:
        """某生逐课状态：{lid: {listen_today, listen_done_ever, shadow, n_shadow, best_score}}。

        shadow=最近一次跟读提交摘要（无音频）或 null；best_score 只在
        已批改行上取 MAX。学生宇宙的「有提交记录」半边也以此判定。
        """
        today = _today()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM nce2_submission WHERE user = ? ORDER BY id",
                (user,),
            ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            st = out.setdefault(r["lesson_id"], {
                "listen_today": False, "listen_done_ever": False,
                "shadow": None, "n_shadow": 0, "best_score": None,
            })
            if r["kind"] == "listen":
                st["listen_done_ever"] = True
                if r["date"] == today:
                    st["listen_today"] = True
                continue
            st["n_shadow"] += 1
            # 行按 id 升序遍历：最后一次赋值即最近一次提交
            st["shadow"] = _sub_out(r)
            if (r["status"] == "graded"
                    and r["teacher_score"] is not None
                    and (st["best_score"] is None
                         or r["teacher_score"] > st["best_score"])):
                st["best_score"] = r["teacher_score"]
        return out

    def list_all(self, status: Optional[str] = None, user: Optional[str] = None,
                 limit: int = 200) -> list[dict]:
        """提交流水（新→旧，不含音频）。教师批改队列与学生个人历史共用。"""
        sql = "SELECT * FROM nce2_submission"
        conds, args = [], []
        if status:
            conds.append("status = ?")
            args.append(status)
        if user:
            conds.append("user = ?")
            args.append(user)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._connect() as conn:
            return [_sub_out(r) for r in conn.execute(sql, args).fetchall()]

    def pending_list(self, limit: int = 200) -> list[dict]:
        """待批改队列（pending 只会是 shadow 行：listen 恒 'done'）。"""
        return self.list_all(status="pending", limit=limit)

    def list_by_user(self, user: str, limit: int = 200) -> list[dict]:
        """某生的全部提交（含 listen 打点与历史跟读，新→旧）。"""
        return self.list_all(user=user, limit=limit)

    def get_audio(self, submission_id: int) -> Optional[dict]:
        """单行详情（含 audio_b64/mime），供 /audio 端点解码播放。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM nce2_submission WHERE id = ?",
                (submission_id,)).fetchone()
        return _sub_out(row, with_audio=True) if row else None

    # ---- 批改 / 删除 ----

    def grade(self, submission_id: int, score, comment: str) -> Optional[dict]:
        """教师批改跟读录音（10 分制，越界夹取）。

        只允许 shadow 行——listen 打点没有可评的音频，误传 ValueError
        （路由转 422）。行不存在返回 None（路由转 404）。
        """
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise ValueError("分数无效") from None
        if not math.isfinite(score):
            raise ValueError("分数无效")
        score = max(0.0, min(10.0, score))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT kind FROM nce2_submission WHERE id = ?",
                (submission_id,)).fetchone()
            if row is None:
                return None
            if row["kind"] != "shadow":
                raise ValueError("listen 打点无需批改，只能批改跟读录音")
            conn.execute(
                "UPDATE nce2_submission SET status='graded', teacher_score=?,"
                " teacher_comment=?, graded_at=? WHERE id = ?",
                (score, (comment or "")[:2000], _now(), submission_id),
            )
            row = conn.execute(
                "SELECT * FROM nce2_submission WHERE id = ?",
                (submission_id,)).fetchone()
        return _sub_out(row)

    def delete(self, submission_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM nce2_submission WHERE id = ?", (submission_id,))
            return cur.rowcount > 0

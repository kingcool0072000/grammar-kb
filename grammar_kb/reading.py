"""阅读训练查询层：reading_article（base 原文段 / derived 派生文）+ 录音提交。

与 :class:`FcePaperStore` 同库（data/fce.db）：
- ``ReadingStore.list_articles()``：按 base_key 聚合（教师看全部；学生只看派生文）
- ``ReadingStore.add_derived()``：教师新增派生文章（挂到某段 base）
- ``ReadingStore.delete_article()``：教师删除派生文
- ``ReadingStore.record()`` / ``grade()``：学生提交录音（base64 webm）→ 教师 10 分制打分
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path
from .reading_build import READING_SCHEMA, word_count

_MAX_AUDIO_B64 = 12 * 1024 * 1024  # 12MB base64（约 9MB 音频，5 分钟 webm 足够）


def _now() -> str:
    return datetime.now(_tz.utc).isoformat(timespec="seconds")


def _ensure_selected_text_column(conn: sqlite3.Connection) -> None:
    """老库补列（selected_text 后加的字段，ALTER 幂等）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reading_recordings)")}
    if "selected_text" not in cols:
        conn.execute("ALTER TABLE reading_recordings ADD COLUMN selected_text TEXT DEFAULT ''")
        conn.commit()


class ReadingStore:
    """reading_article / reading_recordings 读写（fce.db 同库）。"""

    def __init__(self, db_path: Optional[str] = None):
        # 与 FcePaperStore 同库同路径解析（环境变量 → iCloud → data/），
        # 录音随 iCloud 跨设备到达教师端
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(READING_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(READING_SCHEMA)
        _ensure_selected_text_column(conn)
        return conn

    # ---- 文章 ----

    def list_articles(self, kind: Optional[str] = None) -> list[dict]:
        """文章列表（不带 text 正文，列表页只展示元信息）。"""
        sql = "SELECT id, kind, base_key, title, words, source, created_at FROM reading_article"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        sql += " ORDER BY base_key, id"
        with self._connect() as conn:
            return [_art_out(r) for r in conn.execute(sql, args).fetchall()]

    def get_article(self, article_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ?", (article_id,)
            ).fetchone()
        return _art_out(row, with_text=True) if row else None

    def add_derived(
        self, base_key: str, title: str, text: str, source: str = ""
    ) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("正文不能为空")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reading_article (kind, base_key, title, text, words, source, created_at)"
                " VALUES ('derived', ?, ?, ?, ?, ?, ?)",
                (base_key, title.strip() or "未命名", text, word_count(text),
                 source.strip(), _now()),
            )
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _art_out(row, with_text=True)

    def update_derived(
        self, article_id: int, title: Optional[str], text: Optional[str],
        source: Optional[str], base_key: Optional[str],
    ) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ? AND kind = 'derived'",
                (article_id,),
            ).fetchone()
            if row is None:
                return None
            sets, args = [], []
            if title is not None:
                sets.append("title = ?")
                args.append(title.strip() or "未命名")
            if text is not None and text.strip():
                sets.append("text = ?")
                args.append(text.strip())
                sets.append("words = ?")
                args.append(word_count(text))
            if source is not None:
                sets.append("source = ?")
                args.append(source.strip())
            if base_key is not None and base_key.strip():
                sets.append("base_key = ?")
                args.append(base_key.strip())
            if sets:
                args.append(article_id)
                conn.execute(
                    f"UPDATE reading_article SET {', '.join(sets)} WHERE id = ?", args
                )
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ?", (article_id,)
            ).fetchone()
        return _art_out(row, with_text=True)

    def delete_derived(self, article_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM reading_article WHERE id = ? AND kind = 'derived'",
                (article_id,),
            )
            return cur.rowcount > 0

    # ---- 录音 ----

    def submit_recording(
        self, user: str, article_id: int, audio_b64: str, mime: str,
        duration_sec: int, selected_text: str = "",
    ) -> dict:
        audio_b64 = (audio_b64 or "").strip()
        if not audio_b64:
            raise ValueError("录音数据为空")
        if len(audio_b64) > _MAX_AUDIO_B64:
            raise ValueError("录音过大（超过 9MB），请缩短录音时长")
        # base64 有效性校验（宽松：能解出字节即可）
        try:
            base64.b64decode(audio_b64, validate=False)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"录音数据无效: {e}") from e
        dur = max(0, min(int(duration_sec or 0), 5 * 60))
        with self._connect() as conn:
            art = conn.execute(
                "SELECT id FROM reading_article WHERE id = ?", (article_id,)
            ).fetchone()
            if art is None:
                raise KeyError(f"文章 id={article_id} 不存在")
            cur = conn.execute(
                "INSERT INTO reading_recordings (user, article_id, audio_b64, mime,"
                " duration_sec, selected_text, status, created_at)"
                " VALUES (?,?,?,?,?,?, 'pending', ?)",
                (user, article_id, audio_b64, mime or "audio/webm", dur,
                 (selected_text or "")[:4000], _now()),
            )
            row = conn.execute(
                "SELECT * FROM reading_recordings WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _rec_out(row, with_audio=False)

    def list_recordings(
        self, user: Optional[str] = None, status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = (
            "SELECT r.*, a.title AS article_title, a.base_key, a.kind AS article_kind"
            " FROM reading_recordings r JOIN reading_article a ON a.id = r.article_id"
        )
        conds, args = [], []
        if user:
            conds.append("r.user = ?")
            args.append(user)
        if status:
            conds.append("r.status = ?")
            args.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            return [_rec_out(r, with_audio=False) for r in conn.execute(sql, args).fetchall()]

    def get_recording(self, rec_id: int, with_audio: bool = True) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r.*, a.title AS article_title, a.base_key, a.kind AS article_kind"
                " FROM reading_recordings r JOIN reading_article a ON a.id = r.article_id"
                " WHERE r.id = ?",
                (rec_id,),
            ).fetchone()
        return _rec_out(row, with_audio=with_audio) if row else None

    def grade_recording(
        self, rec_id: int, score: int, comment: str
    ) -> Optional[dict]:
        if not 0 <= int(score) <= 10:
            raise ValueError("分数须为 0-10")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM reading_recordings WHERE id = ?", (rec_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE reading_recordings SET status='graded', teacher_score=?,"
                " teacher_comment=?, graded_at=? WHERE id = ?",
                (int(score), comment or "", _now(), rec_id),
            )
            row = conn.execute(
                "SELECT * FROM reading_recordings WHERE id = ?", (rec_id,)
            ).fetchone()
        return _rec_out(row, with_audio=False)


def _art_out(r: sqlite3.Row, with_text: bool = False) -> dict:
    d = {
        "id": r["id"], "kind": r["kind"], "base_key": r["base_key"],
        "title": r["title"], "words": r["words"], "source": r["source"],
        "created_at": r["created_at"],
    }
    if with_text:
        d["text"] = r["text"]
    return d


def _rec_out(r: sqlite3.Row, with_audio: bool = False) -> dict:
    d = {
        "id": r["id"], "user": r["user"], "article_id": r["article_id"],
        "article_title": r["article_title"] if "article_title" in r.keys() else "",
        "base_key": r["base_key"] if "base_key" in r.keys() else "",
        "article_kind": r["article_kind"] if "article_kind" in r.keys() else "",
        "mime": r["mime"], "duration_sec": r["duration_sec"],
        "selected_text": r["selected_text"] if "selected_text" in r.keys() else "",
        "status": r["status"], "teacher_score": r["teacher_score"],
        "teacher_comment": r["teacher_comment"],
        "created_at": r["created_at"], "graded_at": r["graded_at"],
    }
    if with_audio:
        d["audio_b64"] = r["audio_b64"]
    return d

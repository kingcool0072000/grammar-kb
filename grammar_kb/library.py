"""泛读馆数据层（data/library.db）：书架 / 章节 / 进度 / 预习 / 设置 / 查词缓存。

直译自 FCEReadingLib server/src/db.ts + routes/* 的库访问逻辑，差异：
- 不建 users 表——用户列直接存用户名字符串（复用 grammar-kb 现有 auth）
- 每方法独立短连接（项目惯例，天然线程安全，供预习队列后台线程复用）
路径解析沿 fce_query 模式：GRAMMAR_KB_LIBRARY_DB → data/library.db；
文件目录 GRAMMAR_KB_LIBRARY_DIR → data/library/{books,covers}。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Optional

from . import glm
from .dict_db import DEFAULT_DB as _DEFAULT_ECDICT, DictDB
from .epub_parse import EpubParseError, parse_epub

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT DEFAULT '',
  title TEXT NOT NULL,
  author TEXT DEFAULT '',
  cover_path TEXT,
  file_path TEXT NOT NULL,
  file_size INTEGER DEFAULT 0,
  chapter_count INTEGER DEFAULT 0,
  added_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_opened_at TEXT
);
CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  title TEXT DEFAULT '',
  text_content TEXT DEFAULT '',
  word_count INTEGER DEFAULT 0,
  spine_index INTEGER DEFAULT 0,
  skip_prep INTEGER DEFAULT 0,
  UNIQUE(book_id, idx)
);
CREATE TABLE IF NOT EXISTS reading_progress (
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  user TEXT NOT NULL,
  cfi TEXT,
  chapter_index INTEGER DEFAULT 0,
  percent REAL DEFAULT 0,
  reading_seconds INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (book_id, user)
);
CREATE TABLE IF NOT EXISTS chapter_preps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_index INTEGER NOT NULL,
  payload TEXT NOT NULL,
  model TEXT DEFAULT '',
  difficulty TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(book_id, chapter_index)
);
CREATE TABLE IF NOT EXISTS prep_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  user TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'running',
  total INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0,
  pending TEXT DEFAULT '[]',
  failed_indexes TEXT DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS library_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  ai_json TEXT NOT NULL DEFAULT '{}',
  dict_json TEXT NOT NULL DEFAULT '{}',
  student_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS dict_cache (
  word TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class LibraryError(Exception):
    """带 HTTP 状态码的业务错误（上传校验等）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _default_db_path() -> str:
    env = os.environ.get("GRAMMAR_KB_LIBRARY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parent.parent / "data" / "library.db")


def _default_dir() -> Path:
    env = os.environ.get("GRAMMAR_KB_LIBRARY_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "library"


# ---- 查词归一化（直译 dict.ts normalizeWord） ----
_POSSESSIVE_RE = re.compile(r"(?:'|\u2019)s$", re.I)


def normalize_word(raw: str) -> str:
    """所有格与基本归一：children's → children、大写 → 小写。"""
    w = re.sub(r"[^A-Za-z'’-]", "", (raw or "").strip())
    w = w.replace("’", "'")
    if _POSSESSIVE_RE.search(w):
        w = _POSSESSIVE_RE.sub("", w)
    return w.lower()


def _variants(w: str) -> list[str]:
    """简单词形归一（-s/-es/-ed/-ing/-ly）二次尝试。"""
    out: list[str] = []
    if w.endswith("ies") and len(w) > 4:
        out.append(w[:-3] + "y")
    if w.endswith("es") and len(w) > 3:
        out.append(w[:-2])
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        out.append(w[:-1])
    if w.endswith("ied") and len(w) > 4:
        out.append(w[:-3] + "y")
    if w.endswith("ed") and len(w) > 4:
        out.append(w[:-2])
        out.append(w[:-1])
    if w.endswith("ing") and len(w) > 5:
        out.append(w[:-3])
        out.append(w[:-3] + "e")
    if w.endswith("ly") and len(w) > 4:
        out.append(w[:-2])
    return out


class LibraryStore:
    """泛读馆 SQLite 读写层（epub/封面文件同时落盘 data/library/）。"""

    def __init__(self, db_path: Optional[str] = None, data_dir: Optional[str] = None,
                 ecdict_path=None):
        self.db_path = db_path or _default_db_path()
        self.dir = Path(data_dir) if data_dir else _default_dir()
        self.books_dir = self.dir / "books"
        self.covers_dir = self.dir / "covers"
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.dict_db = DictDB(ecdict_path) if ecdict_path else DictDB(_DEFAULT_ECDICT)

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            # 全局设置默认行
            conn.execute(
                "INSERT OR IGNORE INTO library_settings (id, ai_json, dict_json, student_json)"
                " VALUES (1, ?, ?, '{}')",
                (
                    json.dumps({"model": "glm-4-flash", "difficulty": "L2", "maxWords": 12}),
                    json.dumps({"aiFallback": True}),
                ),
            )
            # 服务重启时，清掉旧策略留下的 not_found 缓存（词典策略升级后避免误报"未收录"）
            conn.execute("DELETE FROM dict_cache WHERE source = 'not_found'")
            # 服务重启时，将遗留 running/queued 任务标记为 interrupted
            conn.execute(
                "UPDATE prep_jobs SET status = 'interrupted',"
                " updated_at = datetime('now') WHERE status IN ('running', 'queued')"
            )
            conn.commit()

    # ---- 连接（每调用独立连接 + 自动提交/回滚/关闭） ----

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextlib.contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- 书架 ----

    def _book_view(self, row: sqlite3.Row, user: str, conn: sqlite3.Connection) -> dict:
        prog = conn.execute(
            "SELECT percent, reading_seconds, updated_at FROM reading_progress"
            " WHERE book_id = ? AND user = ?",
            (row["id"], user),
        ).fetchone()
        prepped = conn.execute(
            "SELECT COUNT(*) AS c FROM chapter_preps WHERE book_id = ?", (row["id"],)
        ).fetchone()["c"]
        return {
            "id": row["id"],
            "title": row["title"],
            "author": row["author"] or "",
            "hasCover": bool(row["cover_path"]),
            "chapterCount": row["chapter_count"] or 0,
            "preppedCount": prepped,
            "fileSize": row["file_size"] or 0,
            "addedAt": row["added_at"],
            "lastOpenedAt": row["last_opened_at"],
            "percent": prog["percent"] if prog else None,
            "readingSeconds": prog["reading_seconds"] if prog else None,
            "updatedAt": prog["updated_at"] if prog else None,
        }

    def list_books(self, user: str) -> dict:
        """书架 + 当前用户阅读统计。books 按 id DESC；stats 基于当前用户进度。"""
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
            books = [self._book_view(r, user, conn) for r in rows]
        reading = sum(1 for b in books if b["percent"] is not None and 0 < b["percent"] < 100)
        finished = sum(1 for b in books if b["percent"] is not None and b["percent"] >= 100)
        total_seconds = sum(b["readingSeconds"] or 0 for b in books)
        return {"books": books, "stats": {"reading": reading, "finished": finished,
                                          "totalSeconds": total_seconds}}

    def add_book(self, user: str, filename: str, content_type: str, data: bytes) -> dict:
        """上传入库：内存解析 → 事务落库 → 文件落盘。校验失败抛 LibraryError。"""
        name = filename or ""
        if not (name.lower().endswith(".epub") or content_type == "application/epub+zip"):
            raise LibraryError("只支持 .epub 文件", 400)
        if len(data) > MAX_UPLOAD_BYTES:
            raise LibraryError("文件超过 100MB 上限", 400)
        try:
            parsed = parse_epub(data)
        except EpubParseError as e:
            raise LibraryError(str(e), 422) from e

        file_id = secrets.token_hex(8)
        epub_rel = f"books/{file_id}.epub"
        (self.books_dir / f"{file_id}.epub").write_bytes(data)

        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO books (user, title, author, cover_path, file_path, file_size,"
                " chapter_count) VALUES (?, ?, ?, NULL, ?, ?, ?)",
                (user, parsed.title, parsed.author, epub_rel, len(data), len(parsed.chapters)),
            )
            book_id = cur.lastrowid
            cover_rel = None
            if parsed.cover is not None:
                cover_rel = f"covers/{book_id}.{parsed.cover.ext}"
                conn.execute("UPDATE books SET cover_path = ? WHERE id = ?", (cover_rel, book_id))
            conn.executemany(
                "INSERT INTO chapters (book_id, idx, title, text_content, word_count,"
                " spine_index, skip_prep) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (book_id, ch.idx, ch.title, ch.text, ch.word_count,
                     ch.spine_index or 0, 1 if ch.auxiliary else 0)
                    for ch in parsed.chapters
                ],
            )
        if parsed.cover is not None and cover_rel is not None:
            (self.dir / cover_rel).write_bytes(parsed.cover.buffer)

        with contextlib.closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
            return self._book_view(row, user, conn)

    def get_file(self, book_id: int) -> Optional[tuple[Path, str]]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT file_path FROM books WHERE id = ?", (book_id,)
            ).fetchone()
        if not row:
            return None
        path = self.dir / row["file_path"]
        if not path.is_file():
            return None
        return path, "application/epub+zip"

    _COVER_MIME = {"png": "image/png", "svg": "image/svg+xml", "webp": "image/webp"}

    def get_cover(self, book_id: int) -> Optional[tuple[Path, str]]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT cover_path FROM books WHERE id = ?", (book_id,)
            ).fetchone()
        if not row or not row["cover_path"]:
            return None
        path = self.dir / row["cover_path"]
        if not path.is_file():
            return None
        ext = path.suffix.lower().lstrip(".")
        return path, self._COVER_MIME.get(ext, "image/jpeg")

    def list_chapters(self, book_id: int) -> Optional[list[dict]]:
        with contextlib.closing(self._connect()) as conn:
            if not conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone():
                return None
            rows = conn.execute(
                "SELECT c.idx, c.title, c.word_count, c.spine_index, c.skip_prep,"
                " CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END AS prepped"
                " FROM chapters c"
                " LEFT JOIN chapter_preps p ON p.book_id = c.book_id AND p.chapter_index = c.idx"
                " WHERE c.book_id = ? ORDER BY c.idx",
                (book_id,),
            ).fetchall()
        return [
            {
                "idx": r["idx"],
                "title": r["title"],
                "wordCount": r["word_count"],
                "spineIndex": r["spine_index"],
                "skipPrep": bool(r["skip_prep"]),
                "prepped": bool(r["prepped"]),
            }
            for r in rows
        ]

    def delete_book(self, book_id: int) -> Optional[int]:
        """事务删 5 表关联 + unlink epub/封面；书不存在返回 None。"""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, file_path, cover_path FROM books WHERE id = ?", (book_id,)
            ).fetchone()
        if not row:
            return None
        with self._tx() as conn:
            conn.execute("DELETE FROM chapter_preps WHERE book_id = ?", (book_id,))
            conn.execute("DELETE FROM prep_jobs WHERE book_id = ?", (book_id,))
            conn.execute("DELETE FROM reading_progress WHERE book_id = ?", (book_id,))
            conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
            conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        for rel in (row["file_path"], row["cover_path"]):
            if rel:
                with contextlib.suppress(OSError):
                    (self.dir / rel).unlink(missing_ok=True)
        return book_id

    def set_skip(self, book_id: int, idx: int, skip: bool) -> Optional[dict]:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE chapters SET skip_prep = ? WHERE book_id = ? AND idx = ?",
                (1 if skip else 0, book_id, idx),
            )
            if cur.rowcount == 0:
                return None
        return {"idx": idx, "skipPrep": bool(skip)}

    def touch_open(self, book_id: int) -> None:
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                "UPDATE books SET last_opened_at = datetime('now') WHERE id = ?", (book_id,)
            )
            conn.commit()

    # ---- 阅读进度 ----

    def get_progress(self, book_id: int, user: str) -> Optional[dict]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT cfi, chapter_index, percent, reading_seconds, updated_at"
                " FROM reading_progress WHERE book_id = ? AND user = ?",
                (book_id, user),
            ).fetchone()
        if not row:
            return None
        return {
            "cfi": row["cfi"],
            "chapterIndex": row["chapter_index"],
            "percent": row["percent"],
            "readingSeconds": row["reading_seconds"],
            "updatedAt": row["updated_at"],
        }

    def put_progress(self, book_id: int, user: str, cfi: str, chapter_index: int,
                     percent: float, delta: int) -> Optional[dict]:
        """UPSERT 进度；percent 夹 0-100、delta 夹 0-3600。书不存在返回 None。"""
        with contextlib.closing(self._connect()) as conn:
            if not conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone():
                return None
            cur_seconds = conn.execute(
                "SELECT reading_seconds FROM reading_progress WHERE book_id = ? AND user = ?",
                (book_id, user),
            ).fetchone()
            p = max(0.0, min(100.0, float(percent or 0)))
            d = max(0, min(3600, int(delta or 0)))
            conn.execute(
                "INSERT INTO reading_progress (book_id, user, cfi, chapter_index, percent,"
                " reading_seconds, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(book_id, user) DO UPDATE SET"
                " cfi = excluded.cfi, chapter_index = excluded.chapter_index,"
                " percent = excluded.percent, reading_seconds = reading_seconds + excluded.reading_seconds,"
                " updated_at = datetime('now')",
                (book_id, user, str(cfi) if cfi else None, int(chapter_index or 0), p, d),
            )
            conn.commit()
        return {"percent": p, "readingSeconds": (cur_seconds["reading_seconds"] if cur_seconds else 0) + d}

    # ---- 章节预习 ----

    def get_chapter(self, book_id: int, chapter_index: int) -> Optional[dict]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT title, text_content FROM chapters WHERE book_id = ? AND idx = ?",
                (book_id, chapter_index),
            ).fetchone()
        return dict(row) if row else None

    def get_prep(self, book_id: int, chapter_index: int) -> Optional[dict]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM chapter_preps WHERE book_id = ? AND chapter_index = ?",
                (book_id, chapter_index),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except ValueError:
            return None

    def put_prep(self, book_id: int, chapter_index: int, payload: dict,
                 model: str, difficulty: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chapter_preps"
                " (book_id, chapter_index, payload, model, difficulty, created_at)"
                " VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (book_id, chapter_index, json.dumps(payload, ensure_ascii=False),
                 model, difficulty),
            )

    # ---- 批量预习任务（行驱动，由 prep_queue 消费） ----

    def filter_batch(self, book_id: int, chapter_indexes: list[int]) -> Optional[list[int]]:
        """过滤出待生成章节（去掉已生成与 skip_prep）；书不存在返回 None。"""
        with contextlib.closing(self._connect()) as conn:
            if not conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone():
                return None
            existing = {
                r["chapter_index"]
                for r in conn.execute(
                    "SELECT chapter_index FROM chapter_preps WHERE book_id = ?", (book_id,)
                )
            }
            skipped = {
                r["idx"]
                for r in conn.execute(
                    "SELECT idx FROM chapters WHERE book_id = ? AND skip_prep = 1", (book_id,)
                )
            }
        return [int(i) for i in chapter_indexes if int(i) not in existing and int(i) not in skipped]

    def insert_job(self, book_id: int, user: str, chapter_indexes: list[int]) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO prep_jobs (book_id, user, status, total, pending, failed_indexes)"
                " VALUES (?, ?, 'queued', ?, ?, '[]')",
                (book_id, user, len(chapter_indexes), json.dumps(chapter_indexes)),
            )
            return cur.lastrowid

    def get_job_row(self, job_id: int) -> Optional[dict]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, book_id, status, pending, failed_indexes FROM prep_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_job_running(self, job_id: int) -> None:
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                "UPDATE prep_jobs SET status = 'running', updated_at = datetime('now')"
                " WHERE id = ? AND status = 'queued'",
                (job_id,),
            )
            conn.commit()

    def advance_job(self, job_id: int, pending: list[int], failed: Optional[list[int]] = None) -> None:
        """出一章队：done+1、写回 pending（失败章同时写回 failed_indexes）。"""
        with self._tx() as conn:
            if failed is None:
                conn.execute(
                    "UPDATE prep_jobs SET done = done + 1, pending = ?,"
                    " updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(pending), job_id),
                )
            else:
                conn.execute(
                    "UPDATE prep_jobs SET done = done + 1, pending = ?, failed_indexes = ?,"
                    " updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(pending), json.dumps(failed), job_id),
                )

    def finish_job(self, job_id: int, failed: list[int]) -> None:
        status = "done" if not failed else "partial"
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                "UPDATE prep_jobs SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status, job_id),
            )
            conn.commit()

    def get_job_view(self, job_id: int) -> Optional[dict]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT j.status, j.total, j.done, j.failed_indexes,"
                " (SELECT COUNT(*) FROM chapter_preps p WHERE p.book_id = j.book_id) AS prepped_total,"
                " (SELECT chapter_count FROM books b WHERE b.id = j.book_id) AS book_chapters"
                " FROM prep_jobs j WHERE j.id = ?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        try:
            failed = json.loads(row["failed_indexes"])
        except ValueError:
            failed = []
        return {
            "status": row["status"],
            "total": row["total"],
            "done": row["done"],
            "failedIndexes": failed,
            "preppedTotal": row["prepped_total"],
            "bookChapters": row["book_chapters"],
        }

    # ---- 设置 ----

    def get_settings(self) -> dict:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT ai_json, dict_json, student_json FROM library_settings WHERE id = 1"
            ).fetchone()
        return {
            "ai": json.loads(row["ai_json"] or "{}"),
            "dict": json.loads(row["dict_json"] or "{}"),
            "student": json.loads(row["student_json"] or "{}"),
        }

    def settings_view(self, teacher: bool) -> dict:
        """设置视图。学生视图绝不能泄漏 apiKey/model/prompt（只给 student + hasKey）。"""
        s = self.get_settings()
        student = {
            "preset": s["student"].get("preset") or "paper",
            "fontFamily": s["student"].get("fontFamily") or "serif",
            # 专注力采集开关（泛读馆阅读器）：默认开，教师可在设置里关
            "focus": bool(s["student"].get("focus", True)),
            # 分层词库解锁（背单词）：0=仅 L0；N=已解锁到 L N（0-5）
            "vocabUnlock": max(0, min(5, int(s["student"].get("vocabUnlock") or 0))),
        }
        has_key = bool(s["ai"].get("apiKey"))
        if not teacher:
            return {"student": student, "hasKey": has_key}
        return {
            "ai": {
                "apiKey": s["ai"].get("apiKey") or "",
                "model": s["ai"].get("model") or "glm-4-flash",
                "difficulty": s["ai"].get("difficulty") or "L2",
                "maxWords": s["ai"].get("maxWords") or 12,
                "prompt": s["ai"].get("prompt") or "",
            },
            "dict": {"aiFallback": bool(s["dict"].get("aiFallback"))},
            "student": student,
            "hasKey": has_key,
            "defaultPrompt": glm.DEFAULT_PREP_PROMPT,
        }

    def update_settings(self, ai_patch: Optional[dict], dict_patch: Optional[dict],
                        student_patch: Optional[dict]) -> dict:
        """浅合并保存（仅接受各段内合法字段），返回教师视图。"""
        cur = self.get_settings()
        ai = dict(cur["ai"])
        dct = dict(cur["dict"])
        student = dict(cur["student"])

        if isinstance(ai_patch, dict):
            if isinstance(ai_patch.get("apiKey"), str):
                ai["apiKey"] = ai_patch["apiKey"].strip()
            if isinstance(ai_patch.get("model"), str):
                ai["model"] = ai_patch["model"]
            if ai_patch.get("difficulty") in ("L1", "L2", "L3"):
                ai["difficulty"] = ai_patch["difficulty"]
            mw = ai_patch.get("maxWords")
            if isinstance(mw, (int, float)) and not isinstance(mw, bool):
                ai["maxWords"] = min(25, max(3, round(mw)))
            if isinstance(ai_patch.get("prompt"), str):
                # 空串 = 恢复默认；非空最多 8000 字
                p = ai_patch["prompt"].strip()
                ai["prompt"] = p[:8000]
        if isinstance(dict_patch, dict) and isinstance(dict_patch.get("aiFallback"), bool):
            dct["aiFallback"] = dict_patch["aiFallback"]
        if isinstance(student_patch, dict):
            if student_patch.get("preset") in ("paper", "sepia", "night", "contrast"):
                student["preset"] = student_patch["preset"]
            if student_patch.get("fontFamily") in ("serif", "sans", "kai"):
                student["fontFamily"] = student_patch["fontFamily"]
            if "focus" in student_patch:
                if not isinstance(student_patch["focus"], bool):
                    raise LibraryError("student.focus 须为布尔值（true/false）", status=422)
                student["focus"] = student_patch["focus"]
            if "vocabUnlock" in student_patch:
                vu = student_patch["vocabUnlock"]
                if not isinstance(vu, int) or isinstance(vu, bool) or not 0 <= vu <= 5:
                    raise LibraryError("student.vocabUnlock 须为 0-5 整数", status=422)
                student["vocabUnlock"] = vu

        with self._tx() as conn:
            conn.execute(
                "UPDATE library_settings SET ai_json = ?, dict_json = ?, student_json = ? WHERE id = 1",
                (json.dumps(ai, ensure_ascii=False), json.dumps(dct),
                 json.dumps(student, ensure_ascii=False)),
            )
        return self.settings_view(teacher=True)

    # ---- 查词（缓存 → ECDICT → AI 兜底 → not_found） ----

    def cache_get(self, word: str) -> Optional[dict]:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM dict_cache WHERE word = ?", (word,)
            ).fetchone()
        if not row:
            return None
        try:
            parsed = json.loads(row["payload"])
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def cache_set(self, word: str, entry: dict) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dict_cache (word, source, payload, fetched_at)"
                " VALUES (?, ?, ?, datetime('now'))",
                (word, entry.get("source", ""), json.dumps(entry, ensure_ascii=False)),
            )

    def lookup_ecdict(self, raw_word: str) -> Optional[dict]:
        """内置词典（主词典，离线即时）。未命中返回 None。"""
        if not self.dict_db.available:
            return None
        word = normalize_word(raw_word)
        if not word:
            return None
        hit = self.dict_db.lookup(word)
        if hit is None:
            for v in _variants(word):
                hit = self.dict_db.lookup(v)
                if hit is not None:
                    break
        if hit is None or not hit.get("gloss_lines"):
            return None
        senses = [
            {"pos": ln.get("pos", ""), "gloss": ln.get("text", ""), "examples": []}
            for ln in hit["gloss_lines"][:6]
        ]
        phonetic = hit.get("phonetic") or ""
        entry: dict = {
            "word": hit["word"],
            "senses": senses,
            "meaning": senses[0]["gloss"] if senses else "",
            "source": "ecdict",
        }
        if phonetic:
            entry["phonetic"] = f"/{phonetic}/"
        pos_list = hit.get("pos") or []
        if pos_list:
            entry["pos"] = "/".join(pos_list)
        return entry

    def dict_lookup(self, raw_word: str, context: Optional[str] = None) -> dict:
        """三级查词策略。ECDICT 命中不入缓存（本地查询零成本）。"""
        word = normalize_word(raw_word)
        # 1. 缓存（只缓存 AI 结果与未收录标记）
        cached = self.cache_get(word) if word else None
        if cached:
            if cached.get("source") != "not_found" or not self.get_settings()["dict"].get("aiFallback"):
                return cached

        # 2. 内置 ECDICT（优先于缓存中的 not_found 标记——词典可能后来升级）
        local = self.lookup_ecdict(raw_word)
        if local:
            return local

        # 3. AI 兜底（可全局关闭）
        s = self.get_settings()
        api_key = s["ai"].get("apiKey")
        if s["dict"].get("aiFallback") and api_key:
            try:
                ai = glm.explain_word(
                    api_key, s["ai"].get("model") or "glm-4-flash", word,
                    context=(context or "")[:300] or None,
                )
                entry = {
                    "word": ai["word"],
                    "phonetic": ai["phonetic"] or None,
                    "pos": ai["pos"] or None,
                    "meaning": ai["meaning"] or None,
                    "example": ai["example"] or None,
                    "source": "ai",
                }
                entry = {k: v for k, v in entry.items() if v is not None}
                self.cache_set(word, entry)
                return entry
            except Exception:  # noqa: BLE001 - AI 失败则落到 not_found
                pass

        if not cached:
            cached = {"word": word, "source": "not_found"}
            self.cache_set(word, cached)
        return cached

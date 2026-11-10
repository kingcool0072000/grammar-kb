"""泛读馆数据层（data/library.db）：书架 / 章节 / 进度 / 预习 / 设置 / 查词。

直译自 FCEReadingLib server/src/db.ts + routes/* 的库访问逻辑，差异：
- 不建 users 表——用户列直接存用户名字符串（复用 grammar-kb 现有 auth）
- 每方法独立短连接（项目惯例，天然线程安全）
路径解析经 dbreg 统一口径：GRAMMAR_KB_LIBRARY_DB → 仓库根 data/library.db；
文件目录 GRAMMAR_KB_LIBRARY_DIR → data/library/{books,covers}。
"""
from __future__ import annotations

import contextlib
import json
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Optional

from . import dbreg
from .dict_db import DictDB
from .epub_parse import EpubParseError, parse_epub
from .vocab_exam import MAX_UNLOCK_LEVEL

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB

# 词卡三档：释义（词光 + ECDICT）/ 插图 / 例句。按功能分别开关。
# 泛读馆、阅读练习默认只留释义；背单词等词卡默认保持完整。
WORD_CARD_FEATURES = ("library", "reading", "recite", "conquered", "vocab")
WORD_CARD_KEYS = ("def", "image", "example")
WORD_CARD_DEFAULTS = {
    "library": {"def": True, "image": False, "example": False},
    "reading": {"def": True, "image": False, "example": False},
    "recite": {"def": True, "image": True, "example": True},
    "conquered": {"def": True, "image": True, "example": True},
    "vocab": {"def": True, "image": True, "example": True},
}


def word_cards_view(raw: object) -> dict:
    """补齐各功能的三档开关。缺字段用默认，非法值忽略。"""
    src = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for feat, dft in WORD_CARD_DEFAULTS.items():
        row = src.get(feat) if isinstance(src.get(feat), dict) else {}
        out[feat] = {
            key: row[key] if isinstance(row.get(key), bool) else dft[key]
            for key in WORD_CARD_KEYS
        }
    return out

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
CREATE TABLE IF NOT EXISTS library_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  ai_json TEXT NOT NULL DEFAULT '{}',
  dict_json TEXT NOT NULL DEFAULT '{}',
  student_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS book_reading_config (
  book_id    INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
  chapters   TEXT NOT NULL DEFAULT '[]',  -- JSON: 必读章 idx 列表；空数组=未配置(全书口径)
  updated_at TEXT
);
"""


class LibraryError(Exception):
    """带 HTTP 状态码的业务错误（上传校验等）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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
        self.db_path = db_path or dbreg.library_db_path()
        self.dir = Path(data_dir) if data_dir else Path(dbreg.library_dir())
        self.books_dir = self.dir / "books"
        self.covers_dir = self.dir / "covers"
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.dict_db = DictDB(ecdict_path) if ecdict_path else DictDB()

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            # 全局设置默认行。ai_json/dict_json 列保留（历史存量 Key 供
            # scripts/glm_client.py 等离线脚本读取），线上不再写入 AI 配置
            conn.execute(
                "INSERT OR IGNORE INTO library_settings (id, ai_json, dict_json, student_json)"
                " VALUES (1, '{}', '{}', '{}')"
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
        # 章维度信息（书架章节进度展示）：
        # valid_chapters=有效章数（必读配置，未配置=词数≥200 内容章）、
        # total_words=有效章词数和（全书词数口径=有效章之和）、
        # cur_no/cur_in=当前读到的章号（标题字面数字）与章内%。
        chs = conn.execute(
            "SELECT idx, title, word_count FROM chapters"
            " WHERE book_id = ? ORDER BY idx", (row["id"],),
        ).fetchall()
        cfg_row = conn.execute(
            "SELECT chapters FROM book_reading_config WHERE book_id = ?",
            (row["id"],)).fetchone()
        try:
            cfg = sorted({int(x) for x in json.loads(cfg_row["chapters"] or "[]")}) \
                if cfg_row and cfg_row["chapters"] else None
        except (ValueError, TypeError):
            cfg = None
        valid = set(cfg) if cfg else {c["idx"] for c in chs
                                      if (c["word_count"] or 0) >= 200}
        valid_words = sum(c["word_count"] or 0 for c in chs if c["idx"] in valid)
        cur_no = cur_in = None
        pct = (prog["percent"] if prog else 0.0) or 0.0
        if chs and valid_words > 0:
            pos = pct / 100 * sum(c["word_count"] or 0 for c in chs)
            cum = 0
            first_content = 0
            for c in chs:
                if (c["word_count"] or 0) >= 200:
                    first_content = c["idx"]
                    break
            for i, c in enumerate(chs):
                c1 = cum + (c["word_count"] or 0)
                if pos < c1 or i == len(chs) - 1:
                    m = re.match(r"\s*(\d+)", str(c["title"] or ""))
                    cur_no = (int(m.group(1)) if m
                              else max(1, c["idx"] - first_content + 1))
                    wc = c["word_count"] or 1
                    cur_in = round(max(0.0, min(100.0, (pos - cum) / wc * 100)))
                    break
                cum = c1
        return {
            "id": row["id"],
            "title": row["title"],
            "author": row["author"] or "",
            "hasCover": bool(row["cover_path"]),
            "chapterCount": row["chapter_count"] or 0,
            "validChapters": len(valid),
            "validWords": valid_words,
            "curChapterNo": cur_no,
            "curChapterIn": cur_in,
            "preppedCount": prepped,
            "fileSize": row["file_size"] or 0,
            "addedAt": row["added_at"],
            "lastOpenedAt": row["last_opened_at"],
            "percent": prog["percent"] if prog else None,
            "readingSeconds": prog["reading_seconds"] if prog else None,
            "updatedAt": prog["updated_at"] if prog else None,
        }

    def list_books(self, user: str, role: str = "student") -> dict:
        """书架 + 当前用户阅读统计。books 按 id DESC；stats 基于当前用户进度。

        书籍解锁：student_json.bookUnlock = {学生: [book_id...]}（教师配置）。
        学生配置了列表 → 仅可见列表内书目；未配置/空 → 全部可见（向后兼容）。
        正在读/读完的书始终可见（不因解锁配置收回进度）。
        """
        gate = None
        if role != "teacher":
            st = self.get_settings()["student"]
            gate = (st.get("bookUnlock") or {}).get(user)
            if gate is not None:
                gate = {int(x) for x in gate if str(x).isdigit()}
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
            books = []
            for r in rows:
                b = self._book_view(r, user, conn)
                in_progress = b["percent"] is not None and b["percent"] > 0
                if gate is not None and r["id"] not in gate and not in_progress:
                    continue
                books.append(b)
        if role == "teacher":
            # 教师视角=书籍管理台：不做教师个人的阅读统计，返回书目资产概览
            total_ch = sum(b["validChapters"] or b["chapterCount"] or 0 for b in books)
            total_w = sum(b["validWords"] or 0 for b in books)
            prepped = sum(min(b["preppedCount"] or 0,
                              b["validChapters"] or b["chapterCount"] or 0)
                          for b in books)
            return {"books": books, "stats": {
                "manage": True, "books": len(books), "chapters": total_ch,
                "words": total_w, "prepped": prepped,
            }}
        reading = sum(1 for b in books if b["percent"] is not None and 0 < b["percent"] < 100)
        finished = sum(1 for b in books if b["percent"] is not None and b["percent"] >= 100)
        total_seconds = sum(b["readingSeconds"] or 0 for b in books)
        return {"books": books, "stats": {"reading": reading, "finished": finished,
                                          "totalSeconds": total_seconds}}

    def student_can_access_book(self, user: str, role: str, book_id: int) -> bool:
        """学生能否打开这本书。与 list_books 同一道门。

        教师恒可。未给该生配置 bookUnlock（键不存在）=全部可见。
        配置了 id 列表则只放行列表内的书，以及该生自己 percent>0 的书
        （教师后来收紧列表时，不把正在读的书藏掉）。没有进度的锁定书
        不能靠先上报进度把自己放出来——调用方须在写进度前先问这里。
        """
        if role == "teacher":
            return True
        gate = (self.get_settings()["student"].get("bookUnlock") or {}).get(user)
        if gate is None:
            return True
        allowed = {int(x) for x in gate if str(x).isdigit()}
        if int(book_id) in allowed:
            return True
        prog = self.get_progress(int(book_id), user)
        return bool(prog and (prog.get("percent") or 0) > 0)

    def get_book_unlock(self) -> dict:
        """书籍解锁配置全量（教师视图）：{学生: [book_id...]}。"""
        return dict(self.get_settings()["student"].get("bookUnlock") or {})

    def put_book_unlock(self, mapping: dict) -> dict:
        """保存书籍解锁配置（整体覆盖；空对象=回到全部可见）。"""
        clean = {}
        for k, v in (mapping or {}).items():
            if isinstance(v, list):
                ids = sorted({int(x) for x in v if str(x).isdigit()})
                if ids:
                    clean[str(k)[:32]] = ids
        with self._tx() as conn:
            row = conn.execute(
                "SELECT student_json FROM library_settings WHERE id = 1"
            ).fetchone()
            st = json.loads(row["student_json"] or "{}")
            st["bookUnlock"] = clean
            conn.execute(
                "UPDATE library_settings SET student_json = ? WHERE id = 1",
                (json.dumps(st, ensure_ascii=False),),
            )
        return clean

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

    def get_title(self, book_id: int) -> Optional[str]:
        """按 id 取书名（书不存在返回 None）。"""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT title FROM books WHERE id = ?", (int(book_id),)
            ).fetchone()
        return row["title"] if row else None

    def get_reading_config(self, book_id: int) -> Optional[list[int]]:
        """必读章 idx 列表；未配置返回 None（全书口径）。"""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                (int(book_id),),
            ).fetchone()
        if not row:
            return None
        try:
            v = json.loads(row["chapters"] or "[]")
            return sorted({int(x) for x in v}) if v else None
        except (ValueError, TypeError):
            return None

    def put_reading_config(self, book_id: int, chapters: list) -> None:
        """保存必读章配置（chapters 传空列表/None = 清除配置回到全书口径）。"""
        from datetime import datetime, timezone as _tz
        idxs = sorted({int(x) for x in (chapters or [])})
        with self._tx() as conn:
            if idxs:
                conn.execute(
                    "INSERT OR REPLACE INTO book_reading_config"
                    " (book_id, chapters, updated_at) VALUES (?,?,?)",
                    (int(book_id), json.dumps(idxs),
                     datetime.now(_tz.utc).isoformat(timespec="seconds")),
                )
            else:
                conn.execute(
                    "DELETE FROM book_reading_config WHERE book_id = ?",
                    (int(book_id),),
                )

    def titles_map(self, book_ids) -> dict[int, str]:
        """批量 id → 书名（专注力会话补书名用；空集返回空 dict）。"""
        ids = sorted({int(i) for i in book_ids if i is not None})
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT id, title FROM books WHERE id IN ({ph})", ids
            ).fetchall()
        return {r["id"]: r["title"] for r in rows}

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
            conn.execute("DELETE FROM reading_progress WHERE book_id = ?", (book_id,))
            conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
            conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        for rel in (row["file_path"], row["cover_path"]):
            if rel:
                with contextlib.suppress(OSError):
                    (self.dir / rel).unlink(missing_ok=True)
        return book_id

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
        """设置视图：师生同构，只有 student 段。

        LLM 退役后不再有教师专属的 ai/dict 段，hasKey 也一并取消（前端
        没有 AI 设置页可 gate）。保留 teacher 形参以兼容既有调用与未来
        可能重新出现的教师专属段。
        """
        s = self.get_settings()
        student = {
            "preset": s["student"].get("preset") or "paper",
            "fontFamily": s["student"].get("fontFamily") or "serif",
            # 专注力采集开关（泛读馆阅读器）：默认开，教师可在设置里关
            "focus": bool(s["student"].get("focus", True)),
            # 分层词库解锁（背单词）：0=仅 L0；N=已解锁到 L N
            "vocabUnlock": max(0, min(MAX_UNLOCK_LEVEL, int(s["student"].get("vocabUnlock") or 0))),
            "wordCards": word_cards_view(s["student"].get("wordCards")),
        }
        return {"student": student}

    def update_settings(self, student_patch: Optional[dict]) -> dict:
        """浅合并保存（仅接受 student 段内合法字段），返回设置视图。

        ai/dict 段随 LLM 退役移除：旧 payload 里带了也静默忽略（路由侧
        不解析），存量 ai_json 原样保留（离线脚本仍要读 Key）。
        """
        student = dict(self.get_settings()["student"])
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
                if not isinstance(vu, int) or isinstance(vu, bool) \
                        or not 0 <= vu <= MAX_UNLOCK_LEVEL:
                    raise LibraryError(
                        f"student.vocabUnlock 须为 0-{MAX_UNLOCK_LEVEL} 整数",
                        status=422)
                student["vocabUnlock"] = vu
            if "wordCards" in student_patch:
                incoming = student_patch["wordCards"]
                if not isinstance(incoming, dict):
                    raise LibraryError("student.wordCards 须为对象", status=422)
                merged = word_cards_view(student.get("wordCards"))
                for feat, row in incoming.items():
                    if feat not in WORD_CARD_DEFAULTS or not isinstance(row, dict):
                        continue
                    for key in WORD_CARD_KEYS:
                        if isinstance(row.get(key), bool):
                            merged[feat][key] = row[key]
                student["wordCards"] = merged

        with self._tx() as conn:
            conn.execute(
                "UPDATE library_settings SET student_json = ? WHERE id = 1",
                (json.dumps(student, ensure_ascii=False),),
            )
        return self.settings_view(teacher=True)

    # ---- 查词（ECDICT → not_found；AI 兜底与 dict_cache 随 LLM 退役移除） ----

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

    def dict_lookup(self, raw_word: str) -> dict:
        """查词：ECDICT 命中 → {source:'ecdict',...}；未命中 → {source:'not_found'}。

        结果不再落 dict_cache：ECDICT 本地查询零成本，AI 兜底退役后也没
        有需要缓存的内容了。
        """
        local = self.lookup_ecdict(raw_word)
        if local:
            return local
        return {"word": normalize_word(raw_word), "source": "not_found"}

"""词光 Word Atlas 词条（插画 / 录音 / 义项）叠加层。

数据来自 https://github.com/designfuchen/word-atlas （CC BY 4.0，
署名 Word Atlas contributors（词光））。只往 grammar.db 增加
``vocab_atlas``，不改 ``vocab_word`` 的级别归属，也不碰 fce.db 里的
背词进度。媒体文件不入库，由 ``scripts/import_word_atlas.py`` 拉到
可配置目录，缺文件时接口仍返回词条、前端退回浏览器 TTS。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .paths import data_path

SOURCE_REPO = "https://github.com/designfuchen/word-atlas"
SOURCE_BRANCH = "main"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Word Atlas contributors（词光）"

# 与词光 index.json 的 media 位标记一致。
MEDIA_IMG = 1
MEDIA_UK = 2
MEDIA_US = 4
MEDIA_EX = 8
MEDIA_EX2 = 16
# 公开数据集里每个词都有插画、英音、美音和例句 1 的录音；ex2 只有一部分。
MEDIA_DEFAULT = MEDIA_IMG | MEDIA_UK | MEDIA_US | MEDIA_EX

TOPIC_ZH = {
    "education": "教育",
    "work": "职场",
    "technology": "科技",
    "science": "科学",
    "environment": "环境",
    "health": "健康",
    "society": "社会",
    "government": "政法",
    "economy": "经济",
    "media": "媒体",
    "arts": "艺术",
    "travel": "旅行",
    "city": "城市",
    "nature": "自然",
    "daily": "生活",
    "emotion": "情感",
    "thinking": "思维",
    "sport": "运动",
    "history": "历史",
}

# 媒体文件名：词光 id 是小写字母、数字和连字符。拒绝路径片段。
_SAFE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PART = re.compile(r"^[a-z0-9][a-z0-9.-]*$")

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("atlas_id", "TEXT NOT NULL DEFAULT ''"),
    ("uk", "TEXT NOT NULL DEFAULT ''"),
    ("us", "TEXT NOT NULL DEFAULT ''"),
    ("cefr", "TEXT NOT NULL DEFAULT ''"),
    ("topic", "TEXT NOT NULL DEFAULT ''"),
    ("senses", "TEXT NOT NULL DEFAULT '[]'"),
    ("examples", "TEXT NOT NULL DEFAULT '[]'"),
    ("collocations", "TEXT NOT NULL DEFAULT '[]'"),
    ("family", "TEXT NOT NULL DEFAULT '[]'"),
    ("synonyms", "TEXT NOT NULL DEFAULT '[]'"),
    ("antonyms", "TEXT NOT NULL DEFAULT '[]'"),
    ("tip", "TEXT NOT NULL DEFAULT ''"),
    ("note", "TEXT NOT NULL DEFAULT ''"),
    ("media", "INTEGER NOT NULL DEFAULT 0"),
    ("imported_at", "TEXT NOT NULL DEFAULT ''"),
)

_CREATE = """
CREATE TABLE IF NOT EXISTS vocab_atlas (
    word         TEXT PRIMARY KEY,
    atlas_id     TEXT NOT NULL DEFAULT '',
    uk           TEXT NOT NULL DEFAULT '',
    us           TEXT NOT NULL DEFAULT '',
    cefr         TEXT NOT NULL DEFAULT '',
    topic        TEXT NOT NULL DEFAULT '',
    senses       TEXT NOT NULL DEFAULT '[]',
    examples     TEXT NOT NULL DEFAULT '[]',
    collocations TEXT NOT NULL DEFAULT '[]',
    family       TEXT NOT NULL DEFAULT '[]',
    synonyms     TEXT NOT NULL DEFAULT '[]',
    antonyms     TEXT NOT NULL DEFAULT '[]',
    tip          TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    media        INTEGER NOT NULL DEFAULT 0,
    imported_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_vocab_atlas_id ON vocab_atlas(atlas_id);
"""

_UPSERT = """
INSERT INTO vocab_atlas (
    word, atlas_id, uk, us, cefr, topic, senses, examples, collocations,
    family, synonyms, antonyms, tip, note, media, imported_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(word) DO UPDATE SET
    atlas_id=excluded.atlas_id,
    uk=excluded.uk,
    us=excluded.us,
    cefr=excluded.cefr,
    topic=excluded.topic,
    senses=excluded.senses,
    examples=excluded.examples,
    collocations=excluded.collocations,
    family=excluded.family,
    synonyms=excluded.synonyms,
    antonyms=excluded.antonyms,
    tip=excluded.tip,
    note=excluded.note,
    media=excluded.media,
    imported_at=excluded.imported_at
"""


def cache_repo_dir() -> Path:
    """git 稀疏克隆落点（不进 git，见 .gitignore）。"""
    return data_path("atlas-cache", "word-atlas")


def resolve_atlas_dir() -> Path:
    """媒体根目录：``img/`` ``thumb/`` ``audio/{uk,us,ex,ex2}/``。

    ``GRAMMAR_KB_ATLAS_DIR`` 优先。否则若默认克隆目录里已经有插画就用它，
    再看 ``data/atlas``。都没有时返回克隆目录——导入脚本会把文件放进去，
    服务进程不用重启也能在下一次请求读到。
    """
    env = os.environ.get("GRAMMAR_KB_ATLAS_DIR", "").strip()
    if env:
        return Path(env)
    cache = cache_repo_dir() / "public" / "atlas"
    plain = data_path("atlas")
    if (cache / "img").is_dir():
        return cache
    if (plain / "img").is_dir():
        return plain
    return cache


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表。已有旧表时只补列，不删行。"""
    conn.executescript(_CREATE)
    have = {row[1] for row in conn.execute("PRAGMA table_info(vocab_atlas)")}
    for name, decl in _COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE vocab_atlas ADD COLUMN {name} {decl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vocab_atlas_id ON vocab_atlas(atlas_id)"
    )


def normalize_word(raw: str) -> str:
    word = (raw or "").strip().lower().replace("’", "'").replace("‘", "'")
    return " ".join(word.split())


def lookup_keys(raw: str) -> list[str]:
    """词头的若干写法：空格/连字符、撇号、``zip code`` → ``zipcode``。"""
    word = normalize_word(raw)
    if not word:
        return []
    keys: list[str] = []

    def add(value: str) -> None:
        value = " ".join(value.split())
        if value and value not in keys:
            keys.append(value)

    add(word)
    add(word.replace("'", ""))
    add(word.replace(" ", "-"))
    add(word.replace("-", " "))
    add(word.replace("'", "").replace(" ", "-"))
    squashed = re.sub(r"[^a-z0-9]+", "", word)
    add(squashed)
    hyphenated = re.sub(r"[^a-z0-9]+", "-", word).strip("-")
    add(hyphenated)
    return keys


def media_map_from_index(index: Optional[dict]) -> dict[str, int]:
    """``index.json`` 的 words 行：``[id, word, zh, ipa, cefr, topic, media]``。"""
    out: dict[str, int] = {}
    if not isinstance(index, dict):
        return out
    for row in index.get("words") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 7:
            atlas_id = str(row[0] or "").strip().lower()
            if _SAFE_ID.fullmatch(atlas_id):
                try:
                    out[atlas_id] = int(row[6])
                except (TypeError, ValueError):
                    continue
    return out


def _dumps(value) -> str:
    if value is None:
        value = []
    return json.dumps(value, ensure_ascii=False)


def import_entries(
    conn: sqlite3.Connection,
    entries: Iterable[dict],
    media_by_id: Optional[dict[str, int]] = None,
    *,
    have_index: bool = False,
) -> dict:
    """幂等写入。返回 ``{"upserted", "skipped"}``。

    给了 index 却没有该 id 时 media=0（不要假装文件存在）。
    没给 index 时用数据集文档里的默认覆盖：插画 + 英音 + 美音 + 例句 1。
    """
    ensure_schema(conn)
    flags = media_by_id or {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        word = normalize_word(str(entry.get("w") or ""))
        atlas_id = str(entry.get("id") or "").strip().lower()
        if not word or not _SAFE_ID.fullmatch(atlas_id):
            skipped += 1
            continue
        if atlas_id in flags:
            media = int(flags[atlas_id])
        elif have_index:
            media = 0
        else:
            media = MEDIA_DEFAULT
        rows.append((
            word,
            atlas_id,
            str(entry.get("uk") or ""),
            str(entry.get("us") or ""),
            str(entry.get("cefr") or ""),
            str(entry.get("topic") or ""),
            _dumps(entry.get("senses") or []),
            _dumps(entry.get("ex") or []),
            _dumps(entry.get("col") or []),
            _dumps(entry.get("fam") or []),
            _dumps(entry.get("syn") or []),
            _dumps(entry.get("ant") or []),
            str(entry.get("tip") or ""),
            str(entry.get("note") or ""),
            media,
            now,
        ))
    with conn:
        conn.executemany(_UPSERT, rows)
    return {"upserted": len(rows), "skipped": skipped}


def _loads(raw: str, default: str):
    try:
        return json.loads(raw if raw else default)
    except (json.JSONDecodeError, TypeError):
        return json.loads(default)


def to_public(row: sqlite3.Row, *, full: bool = True) -> dict:
    topic = row["topic"] or ""
    out = {
        "word": row["word"],
        "id": row["atlas_id"],
        "uk": row["uk"] or "",
        "us": row["us"] or "",
        "cefr": row["cefr"] or "",
        "topic": topic,
        "topic_zh": TOPIC_ZH.get(topic, ""),
        "media": int(row["media"] or 0),
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "attribution": ATTRIBUTION,
        "source": SOURCE_REPO,
    }
    if not full:
        return out
    out.update({
        "senses": _loads(row["senses"], "[]"),
        "examples": _loads(row["examples"], "[]"),
        "collocations": _loads(row["collocations"], "[]"),
        "family": _loads(row["family"], "[]"),
        "synonyms": _loads(row["synonyms"], "[]"),
        "antonyms": _loads(row["antonyms"], "[]"),
        "tip": row["tip"] or "",
        "note": row["note"] or "",
    })
    return out


def _as_row_conn(conn: sqlite3.Connection) -> sqlite3.Connection:
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    return conn


def lookup_row(conn: sqlite3.Connection, raw: str) -> Optional[sqlite3.Row]:
    keys = lookup_keys(raw)
    if not keys:
        return None
    conn = _as_row_conn(conn)
    for key in keys:
        row = conn.execute(
            "SELECT * FROM vocab_atlas WHERE word = ?", (key,)
        ).fetchone()
        if row:
            return row
    for key in keys:
        row = conn.execute(
            "SELECT * FROM vocab_atlas WHERE atlas_id = ?", (key,)
        ).fetchone()
        if row:
            return row
    return None


def lookup_public(conn: sqlite3.Connection, raw: str, *, full: bool = True) -> Optional[dict]:
    try:
        row = lookup_row(conn, raw)
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return to_public(row, full=full)


def compact_for_words(conn: sqlite3.Connection, words: Iterable[str]) -> dict[str, dict]:
    """按 vocab_word 的词头取精简词光字段。表不存在时返回空。"""
    wanted = []
    seen = set()
    for word in words:
        key = normalize_word(str(word or ""))
        if key and key not in seen:
            seen.add(key)
            wanted.append(key)
    if not wanted:
        return {}
    conn = _as_row_conn(conn)
    out: dict[str, dict] = {}
    try:
        for offset in range(0, len(wanted), 400):
            chunk = wanted[offset:offset + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT word, atlas_id, uk, us, cefr, topic, media, senses, examples "
                f"FROM vocab_atlas WHERE word IN ({marks})",
                chunk,
            ).fetchall()
            for row in rows:
                item = to_public(row, full=False)
                senses = _loads(row["senses"], "[]")
                examples = _loads(row["examples"], "[]")
                gloss = "；".join(
                    str(sense.get("zh") or "").strip()
                    for sense in senses
                    if isinstance(sense, dict) and sense.get("zh")
                )[:120]
                if gloss:
                    item["gloss"] = gloss
                if examples and isinstance(examples[0], dict) and examples[0].get("en"):
                    item["example"] = {
                        "en": str(examples[0].get("en") or "")[:200],
                        "zh": str(examples[0].get("zh") or "")[:160],
                    }
                out[row["word"]] = item
    except sqlite3.OperationalError:
        return {}
    return out


def count_entries(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) FROM vocab_atlas").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] if row else 0)


def media_ready(directory: Optional[Path] = None) -> bool:
    root = directory or resolve_atlas_dir()
    img = root / "img"
    if not img.is_dir():
        return False
    try:
        next(img.iterdir())
    except StopIteration:
        return False
    except OSError:
        return False
    return True


def resolve_media_file(root: Path, tail: str) -> Optional[Path]:
    """把 ``/atlas-media/`` 后面的路径解析成文件。非法或越界返回 None。"""
    if not tail or "\\" in tail or "\x00" in tail or tail.startswith("/"):
        return None
    parts = tail.split("/")
    if any(part in {"", ".", ".."} or not _PART.fullmatch(part) for part in parts):
        return None
    if len(parts) == 2 and parts[0] in {"img", "thumb"} and parts[1].endswith(".webp"):
        pass
    elif (
        len(parts) == 3
        and parts[0] == "audio"
        and parts[1] in {"uk", "us", "ex", "ex2"}
        and parts[2].endswith(".mp3")
    ):
        pass
    else:
        return None
    try:
        root_real = root.resolve()
        candidate = root.joinpath(*parts)
        if not candidate.is_file():
            return None
        real = candidate.resolve()
    except OSError:
        return None
    if not real.is_relative_to(root_real):
        return None
    return real


def mirror_tree(
    src: Path,
    dest: Path,
    subdirs: tuple[str, ...] = ("img", "thumb", "audio"),
) -> dict:
    """把媒体子目录同步到服务目录。同设备优先硬链接，失败再复制。

    已是同一 inode，或大小与 mtime 都一致的文件会跳过。git 用新 inode
    替换源文件后，mtime 对不上，下次运行会换上新文件。
    """
    linked = copied = skipped = 0
    for name in subdirs:
        source = src / name
        if not source.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(source):
            rel = Path(dirpath).relative_to(src)
            target_dir = dest / rel
            target_dir.mkdir(parents=True, exist_ok=True)
            for filename in filenames:
                origin = Path(dirpath) / filename
                if not origin.is_file():
                    continue
                target = target_dir / filename
                if target.exists():
                    try:
                        src_stat = origin.stat()
                        dst_stat = target.stat()
                    except OSError:
                        skipped += 1
                        continue
                    same_inode = (
                        src_stat.st_ino == dst_stat.st_ino
                        and src_stat.st_dev == dst_stat.st_dev
                    )
                    same_bytes = (
                        src_stat.st_size == dst_stat.st_size
                        and src_stat.st_mtime_ns == dst_stat.st_mtime_ns
                    )
                    if same_inode or same_bytes:
                        skipped += 1
                        continue
                    target.unlink()
                try:
                    os.link(origin, target)
                    linked += 1
                except OSError:
                    shutil.copy2(origin, target)
                    copied += 1
    return {"linked": linked, "copied": copied, "skipped": skipped}

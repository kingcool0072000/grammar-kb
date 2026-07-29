"""SQLite 存储层。

设计：
- 所有正文/表格/长文本字段为 ``TEXT``（SQLite 无长度上限 → 不截断）。
- 全文检索用 FTS5 ``trigram`` 分词器（中文子串检索友好），external-content
  模式指向 knowledge_point 表，FTS 仅用于命中 rowid，正文从主表读取（完整）。
- ``ON DELETE CASCADE`` 让清空一讲时连带清理其知识点/标志词/块。
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from .models import Block, KnowledgePoint, Lecture, Marker, Relation, TableData

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 数据集元信息（版本、生成时间、来源等，用于溯源/复现）
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS lecture (
    id            INTEGER PRIMARY KEY,
    number        INTEGER UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    full_title    TEXT,
    category      TEXT,
    subcategory   TEXT,
    source_file   TEXT,
    page_count    INTEGER DEFAULT 0,
    ingested_at   TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_point (
    id            INTEGER PRIMARY KEY,
    lecture_id    INTEGER NOT NULL REFERENCES lecture(id) ON DELETE CASCADE,
    lecture_number INTEGER NOT NULL,
    title         TEXT NOT NULL,
    category      TEXT,
    section_path  TEXT,
    body_md       TEXT DEFAULT '',
    examples_md   TEXT DEFAULT '',
    table_md      TEXT DEFAULT '',
    is_table      INTEGER DEFAULT 0,
    source_page   INTEGER DEFAULT 1,
    source_bbox   TEXT,
    tags_json     TEXT DEFAULT '[]',
    ord           INTEGER DEFAULT 0,
    ingested_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_kp_lecture ON knowledge_point(lecture_number);
CREATE INDEX IF NOT EXISTS idx_kp_category ON knowledge_point(category);

CREATE TABLE IF NOT EXISTS marker (
    id            INTEGER PRIMARY KEY,
    kp_id         INTEGER NOT NULL REFERENCES knowledge_point(id) ON DELETE CASCADE,
    lecture_number INTEGER,
    marker        TEXT NOT NULL,
    marker_type   TEXT,
    tense         TEXT,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_marker_tense ON marker(tense);
CREATE INDEX IF NOT EXISTS idx_marker_marker ON marker(marker);

CREATE TABLE IF NOT EXISTS relation (
    id            INTEGER PRIMARY KEY,
    kp_id         INTEGER NOT NULL REFERENCES knowledge_point(id) ON DELETE CASCADE,
    type          TEXT NOT NULL,
    to_kp_id      INTEGER,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_relation_kp ON relation(kp_id);

CREATE TABLE IF NOT EXISTS lecture_block (
    id            INTEGER PRIMARY KEY,
    lecture_id    INTEGER NOT NULL REFERENCES lecture(id) ON DELETE CASCADE,
    page          INTEGER,
    seq           INTEGER,
    kind          TEXT,
    text_md       TEXT
);
CREATE INDEX IF NOT EXISTS idx_block_lecture ON lecture_block(lecture_id, seq);

-- 全文检索（external-content + trigram，中文友好、子串命中）
CREATE VIRTUAL TABLE IF NOT EXISTS kp_fts USING fts5(
    title, body_md, examples_md, table_md,
    content='knowledge_point', content_rowid='id',
    tokenize='trigram'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GrammarDB:
    """封装 sqlite3 连接，提供讲义/知识点/标志词的读写。"""

    def __init__(self, path: str):
        self.path = path
        # check_same_thread=False：HTTP 服务在工作线程中只读访问同一连接；
        # 写入（ingest）在 CLI 离线单线程进行，不会并发写。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.init_schema()

    # ---- 生命周期 -------------------------------------------------------- #

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def reset_all(self) -> None:
        """清空整个库并重建 schema（全量重建用，让 id 从 1 开始、可复现）。

        解决反复 ``clear + 重新插入`` 导致自增 id 单调漂移、旧 id 失效的问题。
        """
        with self.transaction() as c:
            # 先删 FTS 与子表，再删主表（注意外键顺序）
            for t in ("kp_fts", "lecture_block", "relation", "marker",
                      "knowledge_point", "lecture", "meta"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
        self.init_schema()
        # 物理回收空间，清除旧数据碎片（避免本地路径等旧值残留在 db 文件里）
        try:
            self.conn.commit()
            self.conn.execute("VACUUM")
        except Exception:
            pass

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta_all(self) -> dict:
        return {
            r["key"]: r["value"]
            for r in self.conn.execute("SELECT key, value FROM meta")
        }

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "GrammarDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- 讲次 ------------------------------------------------------------ #

    def clear_lecture(self, number: int) -> None:
        """删除某讲及其全部知识点/标志词/关系/块（级联）。"""
        with self.transaction() as c:
            row = c.execute("SELECT id FROM lecture WHERE number=?", (number,)).fetchone()
            if row:
                c.execute("DELETE FROM lecture WHERE id=?", (row["id"],))
                # 级联清理 kp/marker/relation/block；FTS 需手动清
                c.execute(
                    "DELETE FROM kp_fts WHERE rowid IN "
                    "(SELECT id FROM knowledge_point WHERE lecture_id=?)",
                    (row["id"],),
                )

    def upsert_lecture(self, lec: Lecture) -> int:
        with self.transaction() as c:
            c.execute(
                """INSERT INTO lecture(number,title,full_title,category,subcategory,
                       source_file,page_count,ingested_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(number) DO UPDATE SET
                       title=excluded.title, full_title=excluded.full_title,
                       category=excluded.category, subcategory=excluded.subcategory,
                       source_file=excluded.source_file, page_count=excluded.page_count,
                       ingested_at=excluded.ingested_at""",
                (
                    lec.number,
                    lec.title,
                    lec.full_title,
                    lec.category,
                    lec.subcategory,
                    lec.source_file,
                    lec.page_count,
                    lec.ingested_at or _now(),
                ),
            )
            row = c.execute(
                "SELECT id FROM lecture WHERE number=?", (lec.number,)
            ).fetchone()
            return row["id"]

    def get_lecture(self, number: int) -> Optional[Lecture]:
        r = self.conn.execute(
            "SELECT * FROM lecture WHERE number=?", (number,)
        ).fetchone()
        if not r:
            return None
        return _row_to_lecture(r)

    def list_lectures(self) -> list[Lecture]:
        rows = self.conn.execute(
            "SELECT * FROM lecture ORDER BY number"
        ).fetchall()
        return [_row_to_lecture(r) for r in rows]

    # ---- 知识点 ---------------------------------------------------------- #

    def insert_kp(self, kp: KnowledgePoint, lecture_id: int) -> int:
        """写入知识点及其标志词/关系，并同步 FTS。返回 kp id。"""
        with self.transaction() as c:
            cur = c.execute(
                """INSERT INTO knowledge_point(
                       lecture_id, lecture_number, title, category, section_path,
                       body_md, examples_md, table_md, is_table, source_page,
                       source_bbox, tags_json, ord, ingested_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    lecture_id,
                    kp.lecture_number,
                    kp.title,
                    kp.category,
                    kp.section_path,
                    kp.body_md,
                    kp.examples_md,
                    kp.table_md,
                    1 if kp.is_table else 0,
                    kp.source_page,
                    kp.source_bbox,
                    json.dumps(kp.tags, ensure_ascii=False),
                    kp.ord,
                    _now(),
                ),
            )
            kp_id = cur.lastrowid
            kp.id = kp_id
            for m in kp.markers:
                c.execute(
                    """INSERT INTO marker(kp_id, lecture_number, marker,
                       marker_type, tense, note) VALUES(?,?,?,?,?,?)""",
                    (
                        kp_id,
                        kp.lecture_number,
                        m.marker,
                        m.marker_type,
                        m.tense,
                        m.note,
                    ),
                )
            for r in kp.relations:
                c.execute(
                    """INSERT INTO relation(kp_id, type, to_kp_id, note)
                       VALUES(?,?,?,?)""",
                    (kp_id, r.type, r.to_kp_id, r.note),
                )
            # 同步 FTS（external-content 需手动）
            c.execute(
                """INSERT INTO kp_fts(rowid, title, body_md, examples_md, table_md)
                   VALUES(?,?,?,?,?)""",
                (kp_id, kp.title, kp.body_md, kp.examples_md, kp.table_md),
            )
            return kp_id

    def get_kp(self, kp_id: int) -> Optional[KnowledgePoint]:
        r = self.conn.execute(
            "SELECT * FROM knowledge_point WHERE id=?", (kp_id,)
        ).fetchone()
        if not r:
            return None
        return _row_to_kp(r, self.conn)

    def kps_of_lecture(self, number: int) -> list[KnowledgePoint]:
        rows = self.conn.execute(
            "SELECT * FROM knowledge_point WHERE lecture_number=? ORDER BY ord, id",
            (number,),
        ).fetchall()
        return [_row_to_kp(r, self.conn) for r in rows]

    def kps_of_category(self, category: str) -> list[KnowledgePoint]:
        rows = self.conn.execute(
            "SELECT * FROM knowledge_point WHERE category=? ORDER BY lecture_number, ord",
            (category,),
        ).fetchall()
        return [_row_to_kp(r, self.conn) for r in rows]

    # ---- 块（整讲还原）--------------------------------------------------- #

    def insert_blocks(self, lecture_id: int, blocks: list[Block]) -> None:
        from .markdown import table_to_markdown

        with self.transaction() as c:
            for b in blocks:
                if b.kind == "table" and b.table_data:
                    text = table_to_markdown(b.table_data)
                else:
                    text = b.text_md
                c.execute(
                    """INSERT INTO lecture_block(lecture_id, page, seq, kind, text_md)
                       VALUES(?,?,?,?,?)""",
                    (lecture_id, b.page, b.seq, b.kind, text),
                )

    def blocks_of_lecture(self, lecture_id: int) -> list[Block]:
        rows = self.conn.execute(
            "SELECT * FROM lecture_block WHERE lecture_id=? ORDER BY seq",
            (lecture_id,),
        ).fetchall()
        out: list[Block] = []
        for r in rows:
            text = r["text_md"] or ""
            td = None
            if r["kind"] == "table":
                td = _markdown_to_tabledata(text)
            out.append(
                Block(
                    kind=r["kind"],
                    text_md=text,
                    table_data=td,
                    page=r["page"],
                    seq=r["seq"],
                )
            )
        return out

    # ---- 统计 ------------------------------------------------------------ #

    def stats(self) -> dict:
        n_lec = self.conn.execute("SELECT COUNT(*) c FROM lecture").fetchone()["c"]
        n_kp = self.conn.execute("SELECT COUNT(*) c FROM knowledge_point").fetchone()["c"]
        n_mk = self.conn.execute("SELECT COUNT(*) c FROM marker").fetchone()["c"]
        cats = self.conn.execute(
            "SELECT category, COUNT(*) c FROM knowledge_point GROUP BY category"
        ).fetchall()
        return {
            "lectures": n_lec,
            "knowledge_points": n_kp,
            "markers": n_mk,
            "by_category": {r["category"]: r["c"] for r in cats},
            "dataset": self.get_meta_all(),
        }


# --------------------------------------------------------------------------- #
# 行 → 模型
# --------------------------------------------------------------------------- #


def _row_to_lecture(r: sqlite3.Row) -> Lecture:
    return Lecture(
        id=r["id"],
        number=r["number"],
        title=r["title"],
        full_title=r["full_title"],
        category=r["category"],
        subcategory=r["subcategory"],
        source_file=r["source_file"],
        page_count=r["page_count"],
        ingested_at=r["ingested_at"],
    )


def _row_to_kp(r: sqlite3.Row, conn: sqlite3.Connection) -> KnowledgePoint:
    markers = [
        Marker(
            marker=mr["marker"],
            marker_type=mr["marker_type"],
            tense=mr["tense"],
            note=mr["note"],
        )
        for mr in conn.execute(
            "SELECT * FROM marker WHERE kp_id=?", (r["id"],)
        ).fetchall()
    ]
    relations = [
        Relation(type=rr["type"], to_kp_id=rr["to_kp_id"], note=rr["note"])
        for rr in conn.execute(
            "SELECT * FROM relation WHERE kp_id=?", (r["id"],)
        ).fetchall()
    ]
    try:
        tags = json.loads(r["tags_json"] or "[]")
    except Exception:
        tags = []
    return KnowledgePoint(
        id=r["id"],
        title=r["title"],
        lecture_number=r["lecture_number"],
        category=r["category"],
        section_path=r["section_path"],
        body_md=r["body_md"] or "",
        examples_md=r["examples_md"] or "",
        table_md=r["table_md"] or "",
        is_table=bool(r["is_table"]),
        source_page=r["source_page"],
        source_bbox=r["source_bbox"],
        tags=tags,
        ord=r["ord"],
        markers=markers,
        relations=relations,
    )


def _markdown_to_tabledata(md: str) -> Optional[TableData]:
    """从 GFM markdown 反解析回 TableData（块表用，整讲还原时尽量保留结构）。

    仅支持标准管道表格；解析失败返回 None。
    """
    lines = [ln.strip() for ln in md.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    if not all("|" in ln for ln in lines[:2]):
        return None
    # 跳过分隔行 | --- | --- |
    def split_row(ln: str) -> list[str]:
        ln = ln.strip()
        if ln.startswith("|"):
            ln = ln[1:]
        if ln.endswith("|"):
            ln = ln[:-1]
        return [c.strip() for c in ln.split("|")]

    rows = [split_row(ln) for ln in lines]
    # 去掉分隔行（全 --- 或 :---）
    body = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c != "")]
    if not body:
        return None
    return TableData.from_rows(body)

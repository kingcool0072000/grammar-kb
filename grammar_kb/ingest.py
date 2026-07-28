"""导入：PDF → 解析 → 结构化 → 落库（讲次/知识点/标志词/块）。"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from .db import GrammarDB, _now
from .models import Lecture
from .structure import structure_from_file


@dataclass
class IngestResult:
    lecture_number: int
    title: str
    knowledge_points: int
    markers: int
    ok: bool
    error: str = ""


def ingest_pdf(db: GrammarDB, pdf_path: str) -> IngestResult:
    """解析并导入单个 PDF。若该讲已存在则先清空再导入（幂等）。"""
    try:
        sl = structure_from_file(pdf_path)
    except Exception as e:  # noqa: BLE001
        # 至少能从文件名拿到讲号用于报错
        from .classify import parse_filename

        num, title, _ = parse_filename(pdf_path)
        return IngestResult(num or 0, title, 0, 0, False, f"{type(e).__name__}: {e}")

    lec: Lecture = sl.lecture
    db.clear_lecture(lec.number)  # 幂等：先清后写
    lec.ingested_at = _now()
    lecture_id = db.upsert_lecture(lec)

    n_kp = 0
    n_mk = 0
    for kp in sl.knowledge_points:
        db.insert_kp(kp, lecture_id)
        n_kp += 1
        n_mk += len(kp.markers)
    db.insert_blocks(lecture_id, sl.blocks)

    return IngestResult(lec.number, lec.title, n_kp, n_mk, True)


def ingest_dir(
    db: GrammarDB,
    directory: str,
    pattern: str = "*.pdf",
    rebuild: bool = True,
) -> list[IngestResult]:
    """导入目录下所有 PDF（按文件名排序，保证讲号顺序）。

    ``rebuild=True`` 时先 :meth:`reset_all` 清空整个库再导入，使 id 从 1
    开始、可复现（避免反复导入造成 id 漂移）。单文件 :func:`ingest_pdf`
    只清对应讲，不影响其它讲 id。
    """
    if rebuild:
        db.reset_all()
    files = sorted(glob.glob(os.path.join(directory, pattern)))
    results: list[IngestResult] = []
    for f in files:
        if os.path.basename(f).startswith("."):
            continue
        r = ingest_pdf(db, f)
        results.append(r)
    return results


def default_db_path() -> str:
    """默认 DB 路径：环境变量优先，否则项目内 data/grammar.db。"""
    env = os.environ.get("GRAMMAR_KB_DB")
    if env:
        return env
    # 放在 cwd 下的 data 目录，便于用户定位
    return os.path.join(os.getcwd(), "data", "grammar.db")


def open_db(path: str | None = None) -> GrammarDB:
    p = path or default_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    return GrammarDB(p)

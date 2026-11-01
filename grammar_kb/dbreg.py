"""SQLite 库路径与连接的唯一注册处（Phase 1 数据访问统一）。

历史上 8 个模块各写一套默认路径解析（cwd/data、仓库根 data、双目录探测
并存），同一张库在不同入口可能解析到不同文件。现在全部收敛到这里：

- **路径**：环境变量优先（历史变量名一个不变）；默认值一律仓库根
  ``data/``（经 :func:`grammar_kb.paths.data_path`，与进程 cwd 无关）。
  唯一例外 ``readingmore``：历史上跟随 ``GRAMMAR_KB_DATA_DIR``，保持不变。
- **连接**：:func:`connect` 即用即关——成功提交、异常回滚、退出必关。
  sqlite3 连接自身的 ``with conn`` 只管事务、不关连接，本仓 9 个 Store
  曾因此句柄全靠 GC 回收；新代码禁止再裸开 ``sqlite3.connect``。

Store 的 SCHEMA/迁移在各自 ``__init__`` 里跑一次（走 :func:`connect`），
``_connect`` 只负责取连接，不再每次重跑 DDL。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .paths import data_path

__all__ = [
    "connect",
    "data_dir",
    "grammar_db_path",
    "fce_db_path",
    "exam_db_path",
    "library_db_path",
    "library_dir",
    "readingmore_db_path",
    "ecdict_db_path",
    "audio_reading_dir",
]


def data_dir() -> str:
    """数据目录：``$GRAMMAR_KB_DATA_DIR`` → 仓库根 ``data/``。"""
    env = os.environ.get("GRAMMAR_KB_DATA_DIR")
    if env:
        return env
    return str(data_path())


def _db(env_name: str, filename: str) -> str:
    """库路径：环境变量 → 仓库根 data/<filename>（与 cwd 无关）。"""
    env = os.environ.get(env_name)
    if env:
        return env
    return str(data_path(filename))


def grammar_db_path() -> str:
    """grammar.db：``$GRAMMAR_KB_DB`` → data/grammar.db。"""
    return _db("GRAMMAR_KB_DB", "grammar.db")


def fce_db_path() -> str:
    """fce.db：``$GRAMMAR_KB_FCE_DB`` → data/fce.db。"""
    return _db("GRAMMAR_KB_FCE_DB", "fce.db")


def exam_db_path() -> str:
    """exam.db：``$GRAMMAR_KB_EXAM_DB`` → data/exam.db。"""
    return _db("GRAMMAR_KB_EXAM_DB", "exam.db")


def library_db_path() -> str:
    """library.db：``$GRAMMAR_KB_LIBRARY_DB`` → data/library.db。"""
    return _db("GRAMMAR_KB_LIBRARY_DB", "library.db")


def library_dir() -> str:
    """泛读馆文件目录（epub/封面）：``$GRAMMAR_KB_LIBRARY_DIR`` → data/library/。"""
    env = os.environ.get("GRAMMAR_KB_LIBRARY_DIR")
    if env:
        return env
    return str(data_path("library"))


def readingmore_db_path() -> str:
    """readingmore.db：跟随数据目录（``$GRAMMAR_KB_DATA_DIR``，历史口径）。"""
    return os.path.join(data_dir(), "readingmore.db")


def ecdict_db_path() -> str:
    """ecdict.db：``$GRAMMAR_KB_ECDICT_DB`` → data/ecdict.db。"""
    return _db("GRAMMAR_KB_ECDICT_DB", "ecdict.db")


def audio_reading_dir() -> str:
    """精读范读 WAV 目录：``$GRAMMAR_KB_AUDIO_DIR`` → data/audio/reading/。"""
    env = os.environ.get("GRAMMAR_KB_AUDIO_DIR")
    if env:
        return env
    return str(data_path("audio", "reading"))


@contextmanager
def connect(
    path: str,
    *,
    readonly: bool = False,
    timeout: float = 5.0,
    foreign_keys: bool = False,
) -> Iterator[sqlite3.Connection]:
    """短连接：进入建连，成功提交、异常回滚、退出必关。

    - ``readonly``：URI 只读模式打开（库文件不存在时 connect 即报错，
      调用方按既有惯例先判存在）。
    - ``timeout``：sqlite 忙等待秒数（readingmore 用 10）。
    - ``foreign_keys``：library.db 需要外键约束，按需打开。
    """
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()

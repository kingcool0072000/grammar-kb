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


# WAL 白名单（按文件名匹配：测试会把库指到 tmp 下的同名文件）：
# - fce.db：recite/reading/topics/plan/video 等多 Store 在同一请求里高频
#   短连接交错读写，delete 模式写锁互斥易 SQLITE_BUSY，WAL 读写不互斥。
# - library.db：epub 上传事务大且 AI 预习后台任务并发写，同理。
# grammar.db 进程级单连接、exam.db 极小、ecdict.db 纯只读——不启用。
_WAL_DB_NAMES = frozenset({"fce.db", "library.db"})


def enable_wal(path: str) -> str:
    """对白名单库幂等启用 ``journal_mode=WAL``，返回实际生效的模式。

    WAL 是持久属性（写入库文件头），设一次此后任何连接（含 library.py
    这类不经 :func:`connect` 的裸连接）都运行在 WAL 下。非白名单库原样
    返回 ``delete``；库忙/打不开时不抛错返回当前模式（调用点多为启动期，
    后续任一可写连接会再次尝试）。
    """
    if os.path.basename(path) not in _WAL_DB_NAMES:
        return "delete"
    try:
        conn = sqlite3.connect(path, timeout=5.0)
    except sqlite3.Error:
        return "delete"
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        return str(row[0]) if row else "delete"
    except sqlite3.Error:
        return "delete"
    finally:
        conn.close()


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
    - fce.db / library.db 可写连接自动处于 WAL（见 :func:`enable_wal`；
      只读连接跳过——只读连接改不了 journal_mode，且 WAL 是持久属性，
      可写连接早已设好）。正常 close 时 SQLite 自动 checkpoint 并清理
      -wal/-shm，即用即关模式不会留下游离 -wal 残留。
    """
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    if not readonly and os.path.basename(path) in _WAL_DB_NAMES:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass  # 忙时跳过：WAL 持久化，任一可写连接设成即生效
    try:
        with conn:
            yield conn
    finally:
        conn.close()

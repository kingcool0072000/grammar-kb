"""Store 公共基座：连接入口、建表迁移、JSON 容错、分页 limit 钳制。

历史上各 Store 复制了同一批样板：``_connect`` 一律
``dbreg.connect(self.db_path)``；``__init__`` 一律 executescript(SCHEMA)
加「PRAGMA 探列 + ALTER」老库补列；读行时一律
``json.loads(x or "[]")`` 配 ``except (ValueError, TypeError)``。
口径一致但文本各异，收敛到本模块后新 Store 只组合这里的工具。

刻意保持与历史样板**逐字等价**的口径（纯结构搬移，不改行为）：
- :func:`loads_json` / :func:`loads_dict` 不做 isinstance 校验——合法 JSON
  解析出的 None/标量原样返回，类型错误留给调用方（与旧 try/except 同界）；
- :func:`clamp_limit` 对非法类型照旧抛 ValueError，不静默兜底；
- :func:`add_column` 沿用 homework_admin 惯例：表不存在时跳过（空库新
  部署不能崩），表存在才补列。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable, Optional

from . import dbreg

__all__ = [
    "connect",
    "ensure_schema",
    "add_column",
    "loads_json",
    "loads_dict",
    "clamp_limit",
]


def connect(path: str, **kw):
    """Store 取连接统一入口（dbreg.connect 薄封装：短连接，进连退关）。"""
    return dbreg.connect(path, **kw)


def ensure_schema(db_path: str, schema: str, *migrations: Callable) -> None:
    """建表 + 幂等迁移（Store ``__init__`` 跑一次，正常路径不再重跑 DDL）。

    migrations 为接收 conn 的可调用对象（模块函数 / staticmethod / bound
    method 均可），按序执行；任一抛异常随短连接整体回滚。
    """
    with connect(db_path) as conn:
        conn.executescript(schema)
        for mig in migrations:
            mig(conn)


def add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """表存在且缺列时执行 ddl 补列（幂等），返回是否补了。

    表不存在时跳过：homework_question 这类由 ingest 管线建的表，运行时
    不要求已存在。PRAGMA 探列 + ALTER 的老库补列样板统一走这里。
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if cols and column not in cols:
        conn.execute(ddl)
        return True
    return False


def loads_json(s: Optional[str], default):
    """JSON 容错解析：空/坏串回 default，不抛。

    历史 ``json.loads(x or "[]")`` + ``except (ValueError, TypeError)``
    的收口。与旧口径同界：合法 JSON 解析出的 None/标量原样返回
    （"null" → None），TypeError 之类留给调用方，不擅自加类型校验。
    """
    try:
        return json.loads(s) if s else default
    except (ValueError, TypeError):
        return default


def loads_dict(s: Optional[str]) -> dict:
    """:func:`loads_json` 的 ``"{}"`` 缺省版（同样不校验类型，坏 dict 照旧由调用方炸）。"""
    return loads_json(s, {})


def clamp_limit(v, default: int, hi: int) -> int:
    """分页 limit 钳到 [1, hi]；falsy（0/None/''）用 default。

    与历史口径逐字等价：``max(1, min(int(v or default), hi))``——非法
    类型照旧抛 ValueError，不做静默兜底（路由层参数口径不变）。
    """
    return max(1, min(int(v or default), hi))

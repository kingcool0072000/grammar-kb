"""仓库根与 data/ 路径。

拆成包之后 ``Path(__file__)`` 的层级变了，默认库一律从这里取，
避免指到 ``grammar_kb/data`` 或 ``grammar_kb/server/data``。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_path(*parts: str) -> Path:
    return ROOT.joinpath("data", *parts)

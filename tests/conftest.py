"""pytest 公共夹具与真实讲义路径探测。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 让 `import grammar_kb` 可用（pyproject 已配 pythonpath，这里兜底）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 真实讲义目录（集成测试用）；仅从环境变量读取，不内置任何默认路径
HANDBOOK_DIR = os.environ.get("GRAMMAR_TEST_PDF_DIR")


def handbook_path(name: str) -> str:
    return os.path.join(HANDBOOK_DIR, name) if HANDBOOK_DIR else ""


@pytest.fixture(scope="session")
def handbook_dir():
    if not HANDBOOK_DIR or not os.path.isdir(HANDBOOK_DIR):
        pytest.skip("未设置 GRAMMAR_TEST_PDF_DIR 或目录不存在，跳过真实 PDF 集成测试")
    return HANDBOOK_DIR


def maybe_skip_if_no_pdf(path: str):
    if not os.path.isfile(path):
        pytest.skip(f"PDF 不存在：{path}")

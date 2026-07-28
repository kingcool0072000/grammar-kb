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

# 真实讲义目录（集成测试用）；可用环境变量覆盖
HANDBOOK_DIR = os.environ.get(
    "GRAMMAR_TEST_PDF_DIR",
    "/Users/maxiangyu/Desktop/哈1语法课/讲义",
)


def handbook_path(name: str) -> str:
    return os.path.join(HANDBOOK_DIR, name)


@pytest.fixture(scope="session")
def handbook_dir():
    if not os.path.isdir(HANDBOOK_DIR):
        pytest.skip(f"讲义目录不存在：{HANDBOOK_DIR}")
    return HANDBOOK_DIR


def maybe_skip_if_no_pdf(path: str):
    if not os.path.isfile(path):
        pytest.skip(f"PDF 不存在：{path}")

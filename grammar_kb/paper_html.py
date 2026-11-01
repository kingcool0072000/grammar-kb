"""可打印考卷模板加载（grammar_kb/templates/*.html）。

两套考卷版式（vocab_exam 四题型 L 级卷 / vocab_paper 三题型哈一卷）
此前以大段 f-string 内嵌在 Python 里；HTML/CSS 属于「版式数据」，
出代码后落到 templates/，本模块只负责读取与占位替换
（string.Template，$ 定界，CSS 花括号无需转义）。所有占位符由调用方
先渲染成最终字符串，模板内不含逻辑。
"""
from __future__ import annotations

from pathlib import Path
from string import Template

_CACHE: dict[str, Template] = {}


def render_template(name: str, **kw: str) -> str:
    """读 templates/<name> 并替换 $占位符（模板原文进程内缓存）。"""
    t = _CACHE.get(name)
    if t is None:
        path = Path(__file__).parent / "templates" / name
        t = _CACHE[name] = Template(path.read_text(encoding="utf-8"))
    return t.substitute(**kw)

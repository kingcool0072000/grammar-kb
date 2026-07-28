"""数据模型（dataclass）。

所有模型都是纯数据容器，不依赖 PDF / 数据库，方便单测构造。
注意：正文相关字段统一用 ``*_md``（markdown 源串），由 markdown 模块渲染，
长度无上限，落库为 SQLite TEXT（不截断）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class Category(str, Enum):
    """知识点大类。str 子类便于直接序列化/落库。"""

    LEXICAL = "词法"       # 名词/代词/冠词/数词/介词/连词/形容词/副词/动词词形
    TENSE = "时态"         # 动词时态 1-5
    VOICE = "语态"         # 被动语态
    NON_FINITE = "非谓语"  # 不定式 / 动名词
    SYNTAX = "句法"        # 叹句/反义疑问/特殊疑问/倒装/主谓一致/各类从句
    REVIEW = "综合复习"    # 综合复习卷
    OTHER = "其他"


@dataclass
class TableData:
    """结构化表格。rows[0] 为表头，其余为数据行。

    由 pdfplumber 在过滤水印后的字符上检测得到；渲染时还原为 GFM markdown。
    """

    headers: list[str]
    rows: list[list[str]]
    caption: Optional[str] = None  # 表格上方的标题/说明（如"对比一般将来时&过去将来时"）

    @property
    def n_cols(self) -> int:
        return len(self.headers)

    def to_list(self) -> list[list[str]]:
        return [list(self.headers), *[list(r) for r in self.rows]]

    @classmethod
    def from_rows(cls, rows: list[list[str]], caption: Optional[str] = None) -> "TableData":
        """从 pdfplumber 的 extract_tables() 结果构造。

        约定第一行为表头。空表头会被补成 ``列N``。
        """
        if not rows:
            return cls(headers=[], rows=[], caption=caption)
        rows = [[(c or "").strip() for c in row] for row in rows]
        headers = rows[0]
        body = rows[1:] if len(rows) > 1 else []
        # 补齐每行列数，避免 markdown 渲染错位
        n = max(len(headers), *(len(r) for r in body)) if body else len(headers)
        if n == 0:
            n = 1
        headers = headers + [f"列{i+1}" for i in range(len(headers), n)]
        # 空表头补名（避免渲染出空列）
        headers = [h if h.strip() else f"列{i+1}" for i, h in enumerate(headers)]
        body = [r + [""] * (n - len(r)) for r in body]
        return cls(headers=headers, rows=body, caption=caption)


@dataclass
class Block:
    """讲义里一个内容块（渲染整讲时用）。

    kind 取值：title / heading / para / table / exercise / example。
    table_data 仅在 kind == "table" 时有值；其余用 text_md。
    """

    kind: str
    text_md: str = ""
    table_data: Optional[TableData] = None
    page: int = 1
    seq: int = 0  # 在讲内的顺序


@dataclass
class Marker:
    """标志词 / 关键词。

    例如时态时间状语标志词：always/usually/now/since/already/by tomorrow。
    marker_type 例如 "时间状语"、"标志词"。
    """

    marker: str
    marker_type: str = "标志词"
    tense: Optional[str] = None  # 关联的时态（如"现在完成时"）
    note: Optional[str] = None


@dataclass
class Relation:
    """知识点之间的关系：主将从现 / 时态呼应 / 对比 / 同义 等。"""

    type: str
    to_title: Optional[str] = None  # 指向另一个知识点标题（落库时再解析为 id）
    to_kp_id: Optional[int] = None
    note: Optional[str] = None


@dataclass
class Lecture:
    """一讲。number 来自文件名前缀，full_title 来自首页大标题。"""

    number: int
    title: str                 # 短标题，如"动词时态1"
    full_title: str            # 完整标题，如"第二十二讲 动词时态1"
    category: str              # Category 值
    subcategory: Optional[str] = None  # 细分，如"一般现在时"
    source_file: str = ""      # PDF 绝对/相对路径
    page_count: int = 0
    ingested_at: str = ""
    id: Optional[int] = None


@dataclass
class KnowledgePoint:
    """一个知识点：讲义里可独立检索、可溯源的最小单元。

    溯源信息：lecture_number + section_path + source_page，足以定位到"某一讲"。
    body_md / examples_md / table_md 长度均无上限。
    """

    title: str
    lecture_number: int        # 冗余字段，便于按讲次查询时回填
    category: str
    section_path: str = ""     # 如 "I.过去将来时 > 1.定义"
    body_md: str = ""
    examples_md: str = ""
    table_md: str = ""
    table_data: Optional[TableData] = None
    is_table: bool = False
    markers: list[Marker] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_page: int = 1
    source_bbox: Optional[str] = None  # "[x0,y0,x1,y1]"
    ord: int = 0
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

"""EPUB 解析：元数据、封面、按 spine 拆章（标准库 zipfile + ElementTree 直译）。

直译自 FCEReadingLib server/src/services/epub.ts（jszip + fast-xml-parser）。
- container.xml → OPF → metadata(title、多 creator 逗号连)/manifest/spine
- 封面：EPUB3 properties=cover-image 优先，否则 EPUB2 meta[name=cover]
- TOC：EPUB3 nav 优先，空则 NCX 递归 navPoint
- htmlToText 清洗顺序与辅助页（封面/版权/试读等）归类、近空页合并照搬原文
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import unquote


class EpubParseError(ValueError):
    """EPUB 解析失败（对应 TS 抛出的 Error，端点转 422）。"""


@dataclass
class ParsedCover:
    buffer: bytes
    ext: str


@dataclass
class ParsedChapter:
    idx: int
    title: str
    text: str
    word_count: int
    # 该章首个 spine 项下标（合并前），用于前端 epub.js 章级定位
    spine_index: int
    # 非正文辅助章（封面/目录/版权/宣传页等）：预习默认跳过
    auxiliary: bool


@dataclass
class ParsedEpub:
    title: str
    author: str
    cover: ParsedCover | None = None
    chapters: list[ParsedChapter] = field(default_factory=list)


# ---- 词数统计（注意弯撇号 ’） ----
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


# ---- HTML → 纯文本（保留段落换行），清洗顺序照 epub.ts ----
_HEAD_RE = re.compile(r"<head[\s\S]*?</head>", re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_BLOCK_CLOSE_RE = re.compile(r"</(?:p|div|h[1-6]|li|blockquote|tr|section|article)>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_APOS_ENTITY_RE = re.compile(r"&#0?39;|&apos;|&#x27;", re.I)
_SLASH_ENTITY_RE = re.compile(r"&#x2F;", re.I)
_DEC_ENTITY_RE = re.compile(r"&#(\d+);")
_HEX_ENTITY_RE = re.compile(r"&#x([0-9a-f]+);", re.I)
_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _safe_chr_dec(m: re.Match) -> str:
    try:
        return chr(int(m.group(1)))
    except (ValueError, OverflowError):
        return m.group(0)


def _safe_chr_hex(m: re.Match) -> str:
    """十六进制实体解码；非法码位保留原文。"""
    try:
        return chr(int(m.group(1), 16))
    except (ValueError, OverflowError):
        return m.group(0)


def html_to_text(html: str) -> str:
    s = html or ""
    s = _HEAD_RE.sub(" ", s)
    s = _SCRIPT_STYLE_RE.sub(" ", s)
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_CLOSE_RE.sub("\n\n", s)
    s = _TAG_RE.sub(" ", s)
    s = (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    s = _APOS_ENTITY_RE.sub("'", s)
    s = _SLASH_ENTITY_RE.sub("/", s)
    s = _DEC_ENTITY_RE.sub(_safe_chr_dec, s)
    s = _HEX_ENTITY_RE.sub(_safe_chr_hex, s)
    s = "\n".join(_SPACE_RE.sub(" ", ln).strip() for ln in s.split("\n"))
    s = _BLANK_LINES_RE.sub("\n\n", s).strip()
    return s


_HEADING_RE = re.compile(r"<h[1-6][^>]*>([\s\S]*?)</h[1-6]>", re.I)


def _extract_heading(html: str) -> str:
    m = _HEADING_RE.search(html or "")
    if not m:
        return ""
    return html_to_text(m.group(1))[:140]


# ---- zip 内路径归一化：解码 URI、去开头 ./、合并 ../ ----
def normalize_zip_path(base_dir: str, href: str) -> str:
    p = href.split("#")[0].split("?")[0]
    try:
        p = unquote(p)
    except Exception:  # noqa: BLE001 - 保留原样
        pass
    full = f"{base_dir}/{p}" if base_dir else p
    parts: list[str] = []
    for seg in full.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


# ---- XML（ElementTree + 去命名空间前缀，对应 fast-xml-parser removeNSPrefix） ----
def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _parse_xml(content: str) -> ET.Element:
    return _strip_ns(ET.fromstring(content))


def _el_text(el: ET.Element | None) -> str:
    return (el.text or "") if el is not None else ""


# ---- 封面 media-type → 扩展名 ----
_COVER_EXT_BY_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}


# ---- 目录 ----
def _parse_ncx_toc(ncx_content: str, opf_dir: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(pt: ET.Element) -> None:
        label = ""
        nav_label = pt.find("navLabel")
        if nav_label is not None:
            label = _el_text(nav_label.find("text"))
        src = ""
        content = pt.find("content")
        if content is not None:
            src = content.attrib.get("src", "")
        if src:
            out.append((normalize_zip_path(opf_dir, src), label.strip()))
        for kid in pt.findall("navPoint"):
            walk(kid)

    try:
        doc = _parse_xml(ncx_content)
    except ET.ParseError:
        return out
    nav_map = doc.find("navMap")
    if nav_map is None:
        return out
    for pt in nav_map.findall("navPoint"):
        walk(pt)
    return out


_NAV_LINK_RE = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', re.I)


def _parse_nav_toc(nav_html: str, opf_dir: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _NAV_LINK_RE.finditer(nav_html or ""):
        label = html_to_text(m.group(2)).strip()
        href = m.group(1)
        if href and not href.startswith("http"):
            out.append((normalize_zip_path(opf_dir, href), label))
    return out


# ---- 辅助页归类正则（原文照抄） ----
_AUX_TITLE_RE = re.compile(
    r"^(cover|title page|contents|table of contents|copyright|about the author|also by|books by|"
    r"praise|reviews|dedication|acknowledg(e)?ments?|about the (author|illustrator)|the story of|"
    r"visit|website|press|puffin|read more|the adventure never stops|excerpt|preview|sample chapter|"
    r"前言|目录|版权|作者简介|致谢|封面)",
    re.I,
)
_AUX_URL_RE = re.compile(r"\.co\.uk$|\.com$|www\.", re.I)
_NUMBERED_RE = re.compile(r"^\s*(\d+|chapter\s+\d+)\s+\S", re.I)
_DEFAULT_TITLE_RE = re.compile(r"^第 \d+ 节$")

MIN_WORDS = 30


def parse_epub(data: bytes) -> ParsedEpub:
    """解析 EPUB：元数据、封面、按 spine 顺序拆章。

    章节 idx 与 spine 顺序一致（与前端 epub.js 的 spine 索引对齐，供预习映射）。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise EpubParseError("无法解压 EPUB 文件（不是有效的 zip 格式）") from e
    names = set(zf.namelist())

    container_name = "META-INF/container.xml"
    if container_name not in names:
        raise EpubParseError("EPUB 缺少 META-INF/container.xml，文件可能已损坏")
    try:
        container = _parse_xml(zf.read(container_name).decode("utf-8", "replace"))
    except ET.ParseError as e:
        raise EpubParseError("META-INF/container.xml 格式不正确") from e
    rootfiles = container.find("rootfiles")
    rootfile_entry = (
        rootfiles.findall("rootfile")[0] if rootfiles is not None and rootfiles.findall("rootfile") else None
    )
    opf_path = rootfile_entry.attrib.get("full-path", "") if rootfile_entry is not None else ""
    if not opf_path:
        raise EpubParseError("EPUB 缺少 OPF 描述文件")
    opf_dir = opf_path[: opf_path.rfind("/")] if "/" in opf_path else ""

    if opf_path not in names:
        raise EpubParseError(f"EPUB 内找不到 OPF 文件: {opf_path}")
    try:
        opf = _parse_xml(zf.read(opf_path).decode("utf-8", "replace"))
    except ET.ParseError as e:
        raise EpubParseError("OPF 文件格式不正确（无法解析 XML）") from e
    if opf.tag != "package":
        raise EpubParseError("OPF 文件格式不正确（缺少 package 节点）")

    metadata = opf.find("metadata")
    title = _el_text(metadata.find("title") if metadata is not None else None).strip()
    creators = (
        [_el_text(c) for c in metadata.findall("creator")] if metadata is not None else []
    )
    author = ", ".join(c for c in creators if c).strip()

    # manifest
    manifest = opf.find("manifest")
    manifest_items = manifest.findall("item") if manifest is not None else []
    by_id = {it.attrib.get("id", ""): it for it in manifest_items}

    # spine
    spine = opf.find("spine")
    itemrefs = spine.findall("itemref") if spine is not None else []
    spine_ids = [r.attrib.get("idref", "") for r in itemrefs if r.attrib.get("idref", "")]
    if not spine_ids:
        raise EpubParseError("EPUB spine 为空，无法拆分章节")

    # 封面：EPUB3 properties=cover-image，或 EPUB2 meta[name=cover]
    cover_id = ""
    for it in manifest_items:
        props = it.attrib.get("properties", "")
        if "cover-image" in props.split():
            cover_id = it.attrib.get("id", "")
            break
    if not cover_id and metadata is not None:
        for m in metadata.findall("meta"):
            if m.attrib.get("name", "") == "cover":
                cover_id = m.attrib.get("content", "")
                break
    cover: ParsedCover | None = None
    if cover_id and cover_id in by_id:
        item = by_id[cover_id]
        media_type = item.attrib.get("media-type", "")
        ext = _COVER_EXT_BY_TYPE.get(media_type, "")
        href = item.attrib.get("href", "")
        if ext and href:
            path = normalize_zip_path(opf_dir, href)
            if path in names:
                buf = zf.read(path)
                if len(buf) > 0:
                    cover = ParsedCover(buffer=buf, ext=ext)

    # 目录标题映射（EPUB3 nav 优先，其次 NCX）
    toc_map: dict[str, str] = {}
    nav_path = ""
    ncx_id = (spine.attrib.get("toc", "") if spine is not None else "") or ""
    for it in manifest_items:
        props = it.attrib.get("properties", "")
        if "nav" in props.split():
            nav_path = normalize_zip_path(opf_dir, it.attrib.get("href", ""))
    if not ncx_id:
        for it in manifest_items:
            if it.attrib.get("media-type", "") == "application/x-dtbncx+xml":
                ncx_id = it.attrib.get("id", "")
                break
    toc_entries: list[tuple[str, str]] = []
    if nav_path and nav_path in names:
        toc_entries.extend(_parse_nav_toc(zf.read(nav_path).decode("utf-8", "replace"), opf_dir))
    if not toc_entries and ncx_id and ncx_id in by_id:
        href = by_id[ncx_id].attrib.get("href", "")
        path = normalize_zip_path(opf_dir, href) if href else ""
        if path and path in names:
            toc_entries.extend(_parse_ncx_toc(zf.read(path).decode("utf-8", "replace"), opf_dir))
    for path, label in toc_entries:
        if label and path not in toc_map:
            toc_map[path] = label

    # 按 spine 拆章
    raw_chapters: list[ParsedChapter] = []
    for i, sid in enumerate(spine_ids):
        item = by_id.get(sid)
        href = item.attrib.get("href", "") if item is not None else ""
        media_type = item.attrib.get("media-type", "") if item is not None else ""
        html = ""
        path = normalize_zip_path(opf_dir, href) if href else ""
        if href and re.search(r"x?html", media_type) and path in names:
            html = zf.read(path).decode("utf-8", "replace")
        fallback_title = "封面" if not raw_chapters else f"第 {len(raw_chapters) + 1} 节"
        text = html_to_text(html)
        heading_title = _extract_heading(html)
        text_first_line = text[:120].split("\n")[0].strip()
        # 注意：Python 无块级作用域，这里必须用独立变量名，
        # 否则循环赋值会遮蔽上面的书籍元数据 title
        ch_title = toc_map.get(path) or heading_title or text_first_line or fallback_title
        raw_chapters.append(
            ParsedChapter(
                idx=len(raw_chapters),
                title=ch_title[:160],
                text=text,
                word_count=count_words(text),
                spine_index=i,
                auxiliary=False,
            )
        )

    # ---- 判定辅助章（预习不应关注的非正文页） ----
    body_word_counts = sorted(c.word_count for c in raw_chapters if c.word_count >= 200)
    if body_word_counts:
        median_words = body_word_counts[len(body_word_counts) // 2]
    else:
        all_counts = sorted(c.word_count for c in raw_chapters)
        median_words = all_counts[len(all_counts) // 2] if all_counts else 0
    # 正文核心 = 编号章（"1 xxx"、"chapter 1"）里词数最高那章的位置，作为前后分界参考
    numbered = [
        c.idx
        for c in raw_chapters
        if _NUMBERED_RE.search(c.title) and c.word_count > median_words * 0.5
    ]
    first_numbered = numbered[0] if numbered else -1
    last_numbered = numbered[-1] if numbered else first_numbered

    for ch in raw_chapters:
        if ch.word_count < MIN_WORDS:
            ch.auxiliary = True  # 近空页（并入正文的除外——合并时被消费的不会走到这）
            continue
        if _AUX_TITLE_RE.match(ch.title) or _AUX_URL_RE.search(ch.title.strip()):
            ch.auxiliary = True
            continue
        # 主编号区结束之后又出现的编号章 = 系列下一本的试读章节
        if (
            first_numbered >= 0
            and last_numbered >= 0
            and ch.idx > last_numbered
            and _NUMBERED_RE.search(ch.title)
        ):
            ch.auxiliary = True
            continue
        # 编号正文区之外、且体量明显小于正文章的页 → 辅助
        if (
            first_numbered >= 0
            and (ch.idx < first_numbered or ch.idx > last_numbered)
            and ch.word_count < median_words * 1.5
        ):
            ch.auxiliary = True
    # 正文核心章永不标记
    for i in range(first_numbered, last_numbered + 1):
        if 0 <= i < len(raw_chapters) and (
            _NUMBERED_RE.search(raw_chapters[i].title)
            or raw_chapters[i].word_count >= median_words
        ):
            raw_chapters[i].auxiliary = False

    # ---- 合并近空辅助页（扉页/版权/目录等，<30 词）到后一个正文章 ----
    chapters: list[ParsedChapter] = []
    pending_empty: list[ParsedChapter] = []
    for ch in raw_chapters:
        is_empty = ch.word_count < MIN_WORDS
        if is_empty and not chapters:
            # 开头的空页保留为第一章（封面页），避免全书第一章错位
            chapters.append(ch)
            continue
        if is_empty:
            # 挂到 pending，等遇到正文章时合并（若全书都是空页则最后兜底追加）
            pending_empty.append(ch)
            continue
        if pending_empty:
            # 空页文本并入本章开头，标题优先用空页里最有信息量的（章扉页标题往往更有意义）
            merged_text = "\n\n".join(p.text for p in pending_empty if p.text)
            better_title = next(
                (
                    p.title
                    for p in pending_empty
                    if p.title and not _DEFAULT_TITLE_RE.search(p.title) and len(p.title) >= 3
                ),
                None,
            )
            if merged_text:
                ch.text = merged_text + "\n\n" + ch.text
            if better_title and _DEFAULT_TITLE_RE.search(ch.title):
                ch.title = better_title
            ch.word_count = count_words(ch.text)
            pending_empty = []
            # 吸收空页的编号章：仅当位于正文编号区内才视为正文入口（尾部编号章 = 系列试读，保持辅助标记）
            if _NUMBERED_RE.search(ch.title) and first_numbered <= ch.idx <= last_numbered:
                ch.auxiliary = False
        chapters.append(ch)
    if pending_empty:
        # 书尾的空页合并到最后一章
        last = chapters[-1] if chapters else None
        merged_text = "\n\n".join(p.text for p in pending_empty if p.text)
        if last and merged_text:
            last.text += "\n\n" + merged_text
            last.word_count = count_words(last.text)
        elif not last:
            chapters.extend(pending_empty)
    # 重排 idx
    for i, c in enumerate(chapters):
        c.idx = i

    return ParsedEpub(
        title=title or "未命名书籍",
        author=author,
        cover=cover,
        chapters=chapters,
    )

"""FCE 听力原文（transcript）入库：OCR 缓存 → fce_section.transcript 列。

用法：python -m grammar_kb.fce_transcript [--fce-db path] [--dry]
词表端点（listening-vocab）与前端预习词表依赖本列；重复执行幂等覆盖。
脚本提取规则见 _extract()：坐标行 → 按 y 分行 → 去噪音 → 按 Part 切分。
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "fce.db"
OCR_DIR = ROOT / "data" / "ocr_cache"

# 各 Test transcript 区间（OCR 缓存页号，含端点；区间右界=下个 Test 起点）
TEST_PAGES = {1: (121, 132), 2: (133, 144), 3: (145, 155), 4: (156, 169)}
PART_NUM = {"One": 1, "Two": 2, "Three": 3, "Four": 4}

_NOISE = re.compile(r"jq-kpf|KET|PET|FCE|Test \d Key")
_SILENT = {"tone", "[pause]", "ipause]", "lpause]", "[the recording is repeated.]"}


def _ocr_lines(path: Path) -> list[str]:
    """OCR 坐标行（y x text）→ 按 y 分行（容差 25px），行内按 x 排序。"""
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^(\d+)\s+(\d+)\s+(.*)$", ln)
        if m:
            rows.append((int(m.group(1)), int(m.group(2)), m.group(3)))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines, cur_y, buf = [], None, []
    for y, x, t in rows:
        if cur_y is None or abs(y - cur_y) <= 25:
            buf.append(t)
            cur_y = y if cur_y is None else cur_y
        else:
            lines.append(" ".join(buf))
            buf, cur_y = [t], y
    if buf:
        lines.append(" ".join(buf))
    return lines


def _clean(s: str) -> str | None:
    s = s.strip()
    if not s or s.lower() in _SILENT:
        return None
    if re.fullmatch(r"Question \d+", s) or re.fullmatch(r"\d+", s):
        return None
    if _NOISE.search(s) and not re.search(r"[A-Za-z]{4,}\s+[A-Za-z]{4,}", s):
        return None
    if re.search(r"[А-Яа-я]", s) and len(s) < 40:  # OCR 把页脚广告混成西里尔
        return None
    return s


def _extract() -> dict[int, dict[int, str]]:
    """{test_id: {part: transcript}}——指令/停顿/页脚全部剥离后的纯对白。"""
    out: dict[int, dict[int, str]] = {}
    for tn, (a, b) in TEST_PAGES.items():
        lines: list[str] = []
        for p in range(a, b + 1):
            f = OCR_DIR / f"p{p:03d}.txt"
            if f.is_file():
                lines.extend(x for x in (_clean(l) for l in _ocr_lines(f)) if x)
        text = "\n".join(lines)
        i = text.find("This is the Cambridge")
        j = text.rfind("end of the test")
        body = text[i:j] if (i >= 0 and j > i) else text
        parts: dict[int, str] = {}
        for pname, pno in PART_NUM.items():
            m = re.search(rf"(?:look at Part {pname}|Now turn to Part {pname})\b", body)
            if not m:
                continue
            ends = [m.end() + q.start()
                    for q in (re.search(rf"Now turn to Part {n}\b", body[m.end():])
                              for n in PART_NUM if n != pname) if q]
            raw = body[m.end():(min(ends) if ends else len(body))]
            # 再剥一层：段首可能残留 "PART 1 You'll hear…" 指令行
            raw = re.sub(r"^\.?\s*PART \d+\s+[^\n]*\n", "", raw.strip())
            parts[pno] = raw.strip()
        out[tn] = parts
    return out


def apply(db_path: Path, dry: bool = False) -> dict:
    data = _extract()
    if dry:
        return {t: {p: len(v) for p, v in ps.items()} for t, ps in data.items()}
    with sqlite3.connect(db_path) as conn:
        # 老库补列（幂等）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fce_section)")}
        if "transcript" not in cols:
            conn.execute("ALTER TABLE fce_section ADD COLUMN transcript TEXT DEFAULT ''")
        n = 0
        for tn, parts in data.items():
            for pno, script in parts.items():
                if not script:
                    continue
                cur = conn.execute(
                    "UPDATE fce_section SET transcript = ?"
                    " WHERE test_id = ? AND paper = 'Listening' AND part = ?",
                    (script, tn, pno),
                )
                n += cur.rowcount
        conn.commit()
    return {"updated": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fce-db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    print(json.dumps(apply(args.fce_db, args.dry), ensure_ascii=False))


if __name__ == "__main__":
    main()

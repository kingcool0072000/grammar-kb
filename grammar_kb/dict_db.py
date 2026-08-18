"""ECDICT 词典库：全量导入 SQLite 与词条查询。

数据来自 https://github.com/skywind3000/ECDICT 的 ecdict.csv（约 77 万词条），
导入为 data/ecdict.db（独立于 grammar.db，词典与语料解耦，可单独重建）。

设计：
- 全量导入只保留本库需要的列（word/phonetic/translation/exchange/pos/tag/frq），
  并做与 ecdict-slim 相同的预处理：translation 字面 ``\\n`` 转真换行、
  去领域标签行、序号行；释义行数组存为 JSON
- 查询 lookup(word)：返回 {word, phonetic, gloss_lines, forms, pos, frq}
  forms 由 exchange(p/d/i/3/s/r/t) 译成可读键；词性由释义行前缀解析
- 导入幂等：DROP+CREATE，可重复执行
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "ecdict.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dict (
    word        TEXT PRIMARY KEY,
    phonetic    TEXT,
    gloss       TEXT,   -- JSON: ["n. 音乐, 乐曲", ...] 清理后的释义行
    exchange    TEXT,   -- 原始 exchange 编码 "p:went/d:gone/i:going/3:goes"
    pos         TEXT,   -- JSON: ["n","v"] 由释义行前缀解析
    tag         TEXT,   -- 词典标签（zk gk 中考高考等）
    frq         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dict_word ON dict(word);
"""

# 释义行词性前缀 → 本库词性缩写
_TAG_RE = re.compile(r"^(?:(vi|vt|aux|int|n|a|ad|adv|v|p|prep|conj|pron|num|m|u|c))\.")
_TAG_POS = {
    "n": "n", "v": "v", "vi": "v", "vt": "v", "aux": "v",
    "a": "adj", "ad": "adv", "adv": "adv", "p": "prep", "prep": "prep",
    "conj": "conj", "pron": "pron", "u": "pron", "num": "num", "m": "num",
    "c": "conj",
}

# ECDICT exchange 编码 → 可读键（动词：过去式/过去分词/现在分词/三单；
# 形容词副词：比较级/最高级；名词：复数）
EX_KEY = {
    "p": "past", "d": "past_participle", "i": "present_participle",
    "3": "third_singular", "s": "plural", "r": "comparative", "t": "superlative",
}


def _clean_gloss(translation: str) -> list[str]:
    """translation → 清理后的释义行数组。"""
    t = (translation or "").strip().replace("\\n", "\n")
    lines = []
    for ln in t.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("[") or re.match(r"^\d", ln):
            continue  # 空行 / [领域] 标签行 / 序号行
        lines.append(ln)
    return lines[:5]


def _parse_pos(lines: list[str]) -> list[str]:
    pos = []
    for ln in lines:
        m = _TAG_RE.match(ln)
        if m:
            p = _TAG_POS.get(m.group(1))
            if p and p not in pos:
                pos.append(p)
    return pos


def import_ecdict(csv_path: str | Path, db_path: str | Path = DEFAULT_DB) -> int:
    """全量导入 ecdict.csv，返回导入词条数（幂等：重建表）。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("DROP TABLE IF EXISTS dict")
        conn.executescript(SCHEMA)

        n = 0
        batch = []
        with open(csv_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                word = (row.get("word") or "").strip().lower()
                if not word or not word[0].isascii() or not word[0].isalpha():
                    continue
                lines = _clean_gloss(row.get("translation") or "")
                exchange = (row.get("exchange") or "").strip()
                tag = (row.get("tag") or "").strip()
                frq_raw = row.get("frq") or "0"
                if not lines and not exchange:
                    continue  # 无释义无词形（纯符号/数字词条）不入库
                batch.append((
                    word,
                    (row.get("phonetic") or "").strip(),
                    json.dumps(lines, ensure_ascii=False),
                    exchange,
                    json.dumps(_parse_pos(lines), ensure_ascii=False),
                    tag,
                    int(frq_raw) if frq_raw.isdigit() else 0,
                ))
                n += 1
                if len(batch) >= 5000:
                    conn.executemany(
                        "INSERT OR REPLACE INTO dict VALUES (?,?,?,?,?,?,?)", batch
                    )
                    batch = []
        if batch:
            conn.executemany("INSERT OR REPLACE INTO dict VALUES (?,?,?,?,?,?,?)", batch)
        conn.commit()
        return n
    finally:
        conn.close()


class DictDB:
    """词典查询层。线程安全性依赖 SQLite 连接的 check_same_thread=False（由 server 传入）。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def available(self) -> bool:
        return self.path.exists()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self.available:
                raise FileNotFoundError(
                    f"词典库不存在：{self.path}（先运行 grammar-kb import-ecdict <ecdict.csv>）"
                )
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def lookup(self, word: str) -> Optional[dict]:
        """查词条；未命中返回 None。"""
        w = word.strip().lower()
        if not w:
            return None
        row = self._get_conn().execute(
            "SELECT * FROM dict WHERE word = ?", (w,)
        ).fetchone()
        if not row:
            return None
        lines = json.loads(row["gloss"] or "[]")
        exchange = row["exchange"] or ""
        forms = {}
        for part in exchange.split("/"):
            code, _, val = part.partition(":")
            key = EX_KEY.get(code)
            if key and val and val != w and key not in forms:
                forms[key] = val
        return {
            "word": row["word"],
            "phonetic": row["phonetic"] or "",
            "gloss_lines": [
                {"pos": _GLOSS_POS_CN.get(_TAG_RE.match(ln).group(1), "") if _TAG_RE.match(ln) else "",
                 "text": _TAG_RE.sub("", ln).strip()}
                for ln in lines
            ],
            "gloss": " / ".join(_TAG_RE.sub("", ln).strip() for ln in lines),
            "pos": json.loads(row["pos"] or "[]"),
            "forms": forms,
            "tag": row["tag"] or "",
            "frq": row["frq"] or 0,
        }


# 释义词性缩写 → 中文（词典展示）
_GLOSS_POS_CN = {
    "n": "名词", "v": "动词", "vi": "不及物动词", "vt": "及物动词", "aux": "助动词",
    "a": "形容词", "ad": "副词", "adv": "副词", "p": "介词", "prep": "介词",
    "conj": "连词", "pron": "代词", "num": "数词", "int": "感叹词",
    "m": "数词", "u": "代词", "c": "连词",
}

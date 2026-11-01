"""把词光词头收成和爸学词表的优先来源。

词表成员关系写在 ``data/wordlists/*.jsonl`` 和 ``data/vocab_alias.json``。
新补进词表、原来没有的词光词 ``exam: false``，不进入各级试卷抽词池。
派生词、屈折和拼写变体收成别名，进度合并到词光词头，不删计数。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from .atlas import normalize_word
from .paths import data_path


def _load_meta() -> dict:
    """语言学元数据（data/atlas_vocab_meta.json）：级别顺序、英美拼写对、
    词缀变换规则、错词更正。此前内嵌本模块，数据出代码后迁到 data/。"""
    return json.loads(data_path("atlas_vocab_meta.json").read_text(encoding="utf-8"))


_META = _load_meta()
# 词表级别顺序（初中 → 专四）
ORDER: list[str] = _META["order"]
CEFR_LEVEL: dict[str, str] = _META["cefr_level"]
# 明确的错词。值是进度要并过去的正确词；空字符串表示只删除。
DROP_TYPOS: dict[str, str] = _META["drop_typos"]
# 正确拼写还不在词表里：改词头，不丢掉这条释义。
RENAME_TYPOS: dict[str, str] = _META["rename_typos"]
# 英美拼写。只在「一边只在和爸学、另一边只在词光」时收成一对，避免误伤真词。
_EXPL: dict[str, str] = _META["spelling_pairs"]
_EXPL_R = {v: k for k, v in _EXPL.items()}
_SUBS: list[tuple[str, str]] = [tuple(x) for x in _META["suffix_subs"]]


def canon_key(word: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_word(word))


def headword_allowed(word: str, origin: str = "") -> bool:
    """词表词能不能进 vocab_word。词光补进来的短语允许空格和斜杠。"""
    if not word or not word.isascii() or len(word) < 2:
        return False
    if origin == "word-atlas":
        return True
    return " " not in word and "/" not in word


def _variants(word: str) -> set[str]:
    out: set[str] = set()
    if word in _EXPL:
        out.add(_EXPL[word])
    if word in _EXPL_R:
        out.add(_EXPL_R[word])
    for src, dst in _SUBS:
        for match in re.finditer(src, word):
            out.add(word[:match.start()] + dst + word[match.end():])
    out.discard(word)
    return out


def _fam_base(text: str) -> str:
    return normalize_word(re.sub(r"\s*\(.*?\)\s*$", "", text or ""))


def index_atlas(entries: Iterable[dict]) -> dict:
    """canon → 词光词条。斜杠词头（cheque/check）两边都能对上。"""
    out: dict[str, dict] = {}
    order: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        word = normalize_word(str(entry.get("w") or ""))
        if not word:
            continue
        keys = {canon_key(word), canon_key(str(entry.get("id") or ""))}
        if "/" in word:
            keys |= {canon_key(part) for part in word.split("/") if part.strip()}
        for key in keys:
            if key and key not in out:
                out[key] = entry
                order[key] = index
        order.setdefault(canon_key(word), index)
    return {"by_key": out, "order": order}


def _entry_order(index: dict, entry: dict) -> int:
    return index["order"].get(canon_key(str(entry.get("w") or "")), 10**9)


def _pick_parent(parents: list[dict], in_lists: set[str], index: dict) -> dict:
    """多个词光词头都把这个词写在词族里时：先选词表里已经有的，再选数据集里更靠前的。"""
    def sort_key(entry: dict) -> tuple:
        word = normalize_word(str(entry.get("w") or ""))
        present = 0 if canon_key(word) in in_lists else 1
        return (present, _entry_order(index, entry), len(word), word)

    return sorted(parents, key=sort_key)[0]


def load_alias_file(path: Optional[Path] = None) -> dict[str, dict]:
    path = path or data_path("vocab_alias.json")
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        alias = normalize_word(str(key))
        if isinstance(value, str):
            target, kind = normalize_word(value), "alias"
        elif isinstance(value, dict):
            target = normalize_word(str(value.get("to") or ""))
            kind = str(value.get("kind") or "alias")
        else:
            continue
        if alias and target and alias != target:
            out[alias] = {"to": target, "kind": kind}
    return out


def canonical_word(raw: str, aliases: Optional[dict[str, dict]] = None) -> str:
    """别名链折成最终词头。没有别名时返回归一后的原词。"""
    word = normalize_word(raw)
    table = load_alias_file() if aliases is None else aliases
    seen = set()
    while word in table and word not in seen:
        seen.add(word)
        word = table[word]["to"]
    return word


def alias_kind(raw: str, aliases: Optional[dict[str, dict]] = None) -> str:
    table = load_alias_file() if aliases is None else aliases
    word = normalize_word(raw)
    return (table.get(word) or {}).get("kind") or ""


def plan_vocab(
    records: dict[str, dict],
    entries: list[dict],
    *,
    existing_aliases: Optional[dict[str, dict]] = None,
) -> dict:
    """根据当前词表记录和词光词条，算出删除、别名和要追加的词。

    ``records``：canon → {word, first, lists}。调用前先改好错词。
    """
    index = index_atlas(entries)
    by_key = index["by_key"]
    in_lists = set(records)
    only_g = [key for key in records if key not in by_key]
    only_a = []
    for entry in entries:
        keys = {canon_key(str(entry.get("w") or "")), canon_key(str(entry.get("id") or ""))}
        word = str(entry.get("w") or "")
        if "/" in word:
            keys |= {canon_key(part) for part in word.split("/") if part.strip()}
        if not any(key in records for key in keys if key):
            only_a.append(entry)
    only_a_by = {}
    for entry in only_a:
        only_a_by.setdefault(canon_key(str(entry.get("w") or "")), entry)

    fam: dict[str, list[dict]] = {}
    for entry in entries:
        for item in entry.get("fam") or []:
            base = canon_key(_fam_base(item))
            if base:
                fam.setdefault(base, []).append(entry)

    aliases: dict[str, dict] = {}
    remove: set[str] = set()
    additions: dict[str, dict] = {}

    def add_alias(source: str, target: str, kind: str) -> None:
        source_n = normalize_word(source)
        target_n = normalize_word(target)
        if not source_n or not target_n or source_n == target_n:
            return
        aliases[source_n] = {"to": target_n, "kind": kind}
        remove.add(canon_key(source_n))

    def surface_word(entry: dict) -> str:
        raw = str(entry.get("w") or "")
        if "/" in raw:
            return normalize_word(str(entry.get("id") or ""))
        return normalize_word(raw)

    def ensure_head(entry: dict, level: str, *, exam: bool, kind: str) -> None:
        word = surface_word(entry)
        key = canon_key(word)
        if not headword_allowed(word, "word-atlas"):
            return
        if key in in_lists or key in additions or key in remove:
            return
        additions[key] = {
            "word": word,
            "level": level,
            "exam": exam,
            "kind": kind,
            "entry": entry,
        }

    # 拼写：和爸学写法让位给词光写法，级别留在原来首次出现的那一层，仍可入考卷。
    claimed_targets = set()
    for key in list(only_g):
        for variant in _variants(records[key]["word"]):
            target = only_a_by.get(variant)
            if not target:
                continue
            target_word = surface_word(target)
            add_alias(records[key]["word"], target_word, "spelling")
            claimed_targets.add(canon_key(target_word))
            ensure_head(
                target, records[key]["first"], exam=True, kind="spelling",
            )
            break

    # 词族：和爸学独有、又写在某条词光词族里的，收到那个词光词头下面。
    for key in list(only_g):
        if key in remove or key not in fam:
            continue
        parent = _pick_parent(fam[key], in_lists - remove, index)
        parent_word = surface_word(parent)
        add_alias(records[key]["word"], parent_word, "family")
        if canon_key(parent_word) not in in_lists:
            ensure_head(
                parent, CEFR_LEVEL.get(parent.get("cefr") or "", "雅思"),
                exam=False, kind="family",
            )

    # 屈折不做自动并词。词尾还原会把 herring 收成 her、rugged 收成 rug。
    # 这 50 来个词仍各自做词头；见报告里的未决项。

    # 其余只在词光里的词：A1/A2 进初中，B1 进高中，B2 及以上进雅思。不进考卷。
    for entry in only_a:
        word = surface_word(entry)
        key = canon_key(word)
        if key in claimed_targets or key in in_lists or key in additions:
            continue
        level = CEFR_LEVEL.get(entry.get("cefr") or "", "雅思")
        ensure_head(entry, level, exam=False, kind="atlas")

    # 以前写过的别名还在：目标词头仍在（或能再折过去）就保留。
    for alias, info in (existing_aliases or {}).items():
        if alias in aliases:
            continue
        target = canonical_word(info["to"], {**aliases, alias: info})
        if target and target != alias:
            aliases[alias] = {"to": target, "kind": info.get("kind") or "alias"}

    return {
        "aliases": aliases,
        "remove": remove,
        "additions": additions,
        "only_g": len(only_g),
        "only_a": len(only_a),
    }


def atlas_entry_to_jsonl(entry: dict, *, exam: bool) -> dict:
    senses = entry.get("senses") or []
    translations = []
    for sense in senses:
        if not isinstance(sense, dict):
            continue
        zh = (sense.get("zh") or "").strip()
        if not zh:
            continue
        pos = (sense.get("pos") or "").strip().rstrip(".")
        translations.append({"translation": zh, "type": pos.lower()})
    sentences = []
    for example in entry.get("ex") or []:
        if not isinstance(example, dict):
            continue
        en = (example.get("en") or "").strip()
        if en:
            sentences.append({
                "sentence": en,
                "translation": (example.get("zh") or "").strip(),
            })
    word = normalize_word(str(entry.get("w") or ""))
    if "/" in str(entry.get("w") or ""):
        word = normalize_word(str(entry.get("id") or ""))
    return {
        "word": word,
        "us": entry.get("us") or "",
        "uk": entry.get("uk") or "",
        "translations": translations,
        "phrases": [],
        "sentences": sentences,
        "origin": "word-atlas",
        "exam": bool(exam),
        "cefr": entry.get("cefr") or "",
        "topic": entry.get("topic") or "",
        "atlas_id": entry.get("id") or "",
    }


def read_wordlist_dir(directory: Path) -> dict[str, list[dict]]:
    files = {}
    for name in ORDER:
        path = directory / f"{name}.jsonl"
        rows = []
        if not path.is_file():
            files[name] = rows
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"raw": line, "broken": True, "word": "", "d": None})
                continue
            rows.append({
                "raw": line,
                "broken": False,
                "word": normalize_word(str(payload.get("word") or "")),
                "d": payload,
            })
        files[name] = rows
    return files


def records_from_files(files: dict[str, list[dict]]) -> dict[str, dict]:
    records = {}
    for name in ORDER:
        for row in files.get(name) or []:
            word = row.get("word") or ""
            if not word or row.get("broken"):
                continue
            key = canon_key(word)
            if key not in records:
                records[key] = {"word": word, "first": name, "lists": [name]}
            elif name not in records[key]["lists"]:
                records[key]["lists"].append(name)
    return records


def apply_typos(files: dict[str, list[dict]]) -> tuple[list[dict], dict[str, dict]]:
    """删掉明确错词，改正还没有正确词头的那条。返回删除说明。"""
    deleted = []
    present = {
        row["word"]
        for rows in files.values()
        for row in rows
        if row.get("word")
    }
    for name in ORDER:
        kept = []
        for row in files[name]:
            if row.get("broken"):
                deleted.append({
                    "word": "", "list": name, "reason": "损坏的 JSON 行已丢弃",
                    "raw": (row.get("raw") or "")[:80],
                })
                continue
            word = row["word"]
            if word in RENAME_TYPOS:
                fixed = RENAME_TYPOS[word]
                payload = dict(row["d"])
                payload["word"] = fixed
                row = {"raw": None, "broken": False, "word": fixed, "d": payload}
                deleted.append({
                    "word": word, "list": name,
                    "reason": f"错拼已改为 {fixed}",
                })
                kept.append(row)
                continue
            if word in DROP_TYPOS:
                deleted.append({
                    "word": word, "list": name,
                    "reason": f"错拼，正确词是 {DROP_TYPOS[word]}",
                })
                continue
            kept.append(row)
        files[name] = kept
    # 错词别名只在正确词还在（或改名后会在）时保留。
    typo_aliases = {}
    for wrong, right in {**DROP_TYPOS, **RENAME_TYPOS}.items():
        if right and (right in present or right in RENAME_TYPOS.values()):
            typo_aliases[wrong] = {"to": right, "kind": "typo"}
    return deleted, typo_aliases


def render_files(files: dict[str, list[dict]], additions: dict[str, dict]) -> dict[str, list[str]]:
    lines = {name: [] for name in ORDER}
    seen = set()
    for name in ORDER:
        for row in files[name]:
            word = row.get("word") or ""
            key = canon_key(word)
            if not word:
                continue
            seen.add(key)
            if row.get("raw") and row["d"] is not None and normalize_word(
                str(row["d"].get("word") or "")
            ) == word:
                lines[name].append(row["raw"])
            else:
                lines[name].append(json.dumps(row["d"], ensure_ascii=False, separators=(",", ":")))
    for key, item in sorted(additions.items(), key=lambda kv: (ORDER.index(kv[1]["level"]), kv[1]["word"])):
        if key in seen:
            continue
        payload = atlas_entry_to_jsonl(item["entry"], exam=item["exam"])
        if not payload["word"]:
            continue
        lines[item["level"]].append(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        seen.add(key)
    return lines


def write_alias_file(path: Path, aliases: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "note": "别名折到词光词头。kind=family|inflection|spelling|typo。进度合并用，不要手改后不重跑脚本。",
        "aliases": {
            key: aliases[key]
            for key in sorted(aliases)
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def exam_allowed(extra: Optional[str]) -> bool:
    """试卷抽词：没标 exam 的旧词照旧；词光新补的 exam=false 排除。"""
    if not extra:
        return True
    try:
        payload = json.loads(extra)
    except (ValueError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    return payload.get("exam") is not False

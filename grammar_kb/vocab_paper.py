"""新版单词试卷生成（考试资产：与具体学生无关）。

需求口径：
- 随机选词且多样：不同首字母、不同词性（贪心轮转）
- 三题型：中译英 20 / 英译中 20 / 拼写挖空 10（给中文+字母挖空，考察特殊拼写）
- 与历史试卷重复 ≤10 词（vocab_paper_history 表去重）
- 产出可打印 HTML（试题页 + 答案页独立成页）；后续可绑定学生计划目标
"""
from __future__ import annotations

import html
import json
import random
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .atlas_vocab import exam_allowed
from .paper_html import render_template
from .vocab_exam import _grammar_db_path, VocabExamStore


def _pos_list(pos: str) -> list[str]:
    try:
        v = json.loads(pos or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# 词性中文（试卷展示用）：给考生「词性提示」，也用于选词多样性
POS_CN = {"n": "n. 名词", "v": "v. 动词", "adj": "adj. 形容词", "adv": "adv. 副词",
          "prep": "prep. 介词", "conj": "conj. 连词", "pron": "pron. 代词",
          "num": "num. 数词", "interj": "interj. 感叹词", "aux": "aux. 助动词"}


def _pos_cn(pos: str) -> str:
    """词性串 → 中文提示（如 '["v","n"]' → 'v. 动词 / n. 名词'）。"""
    tags = [t for t in _pos_list(pos) if t in POS_CN]
    return " / ".join(POS_CN[t] for t in tags)


_GLOSS_POS_PREFIX = None


def _strip_gloss_pos(gloss: str) -> str:
    """词库部分 gloss 自带词性前缀（如 'prep. 为，为了…'）——词性已有
    专门列展示，题目里剥掉开头前缀避免重复。"""
    global _GLOSS_POS_PREFIX
    if _GLOSS_POS_PREFIX is None:
        import re as _re
        _GLOSS_POS_PREFIX = _re.compile(
            r"^(?:n|v|adj|adv|prep|conj|pron|num|interj|aux)\.\s*/?\s*"
            r"(?:(?:n|v|adj|adv|prep|conj|pron|num|interj)\.\s*/?\s*)*")
    return _GLOSS_POS_PREFIX.sub("", gloss or "", count=1)


_NAME_GLOSS_RE = None  # 延迟编译


def _is_name_word(pos: str, gloss: str) -> bool:
    """人名/地名/专名判定：pos=proper，或 gloss 明确标注（男子名/女子名/
    人名/…名）。这类词不适合做考题（答案发散、考察意义弱），全局规避。"""
    tags = set(_pos_list(pos))
    if "proper" in tags:
        return True
    gl = gloss or ""
    if "男子名" in gl or "女子名" in gl or "人名" in gl:
        return True
    global _NAME_GLOSS_RE
    if _NAME_GLOSS_RE is None:
        import re as _re
        _NAME_GLOSS_RE = _re.compile(r"[（(][^)）]*名[)）]")
    return bool(_NAME_GLOSS_RE.search(gl))


def _skip_for_en2zh(pos: str) -> bool:
    """英译中规避：凡 pos 含 prep（介词）或 proper（人名/地名）即跳过。

    介词义主导的词（含 like/after/per/beneath 这类多性词）单独给英文
    让考生写中文，答案发散、考察意义弱——从严全滤，宁缺毋滥。
    """
    tags = set(_pos_list(pos))
    return bool(tags & {"prep", "proper"})


def _rich_rows(rows) -> list[dict]:
    """vocab_word 原始行 → 富词条（pos 解析、人名/地名规避）。"""
    out = []
    for r in rows:
        pos = (r["pos"] or "").strip()
        gloss = (r["gloss"] or "").strip()
        if _is_name_word(pos, gloss):
            continue
        out.append({"word": r["word"], "pos": pos,
                    "pos_list": _pos_list(pos),
                    "pos_cn": _pos_cn(pos),
                    "gloss": gloss, "gloss_clean": _strip_gloss_pos(gloss)})
    return out


def _load_words_rich(level: int) -> list[dict]:
    """本级词条：word/pos/gloss（pos 解析为列表）。

    人名/地名/专名全局规避（pos=proper 或 gloss 标注 男子名/女子名/
    人名）：这类词进考题没有考察意义（tom→汤姆、beijing→北京），
    且中译英答案发散。各级合计约 161 词被滤除。
    """
    path = _grammar_db_path()
    if not path or not Path(path).exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cols = {row[1] for row in conn.execute("PRAGMA table_info(vocab_word)")}
        sql = "SELECT word, pos, gloss"
        if "extra" in cols:
            sql += ", extra"
        sql += " FROM vocab_word WHERE level = ?"
        rows = [
            row for row in conn.execute(sql, (int(level),)).fetchall()
            if "extra" not in cols or exam_allowed(row["extra"])
        ]
    return _rich_rows(rows)


def _load_words_by_names(names: list[str]) -> dict[str, dict]:
    """按词面批量取富词条（跨级别；MIX 卷重建用），返回 {word: rich}。"""
    path = _grammar_db_path()
    if not path or not Path(path).exists() or not names:
        return {}
    out: dict[str, dict] = {}
    chunk = 400
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for i in range(0, len(names), chunk):
            part = names[i:i + chunk]
            rows = conn.execute(
                "SELECT word, pos, gloss FROM vocab_word"
                f" WHERE word IN ({','.join('?' * len(part))})",
                part).fetchall()
            for w in _rich_rows(rows):
                out[w["word"]] = w
    return out


def _load_learned_words(user: str, fce_db_path: Optional[str] = None) -> list[dict]:
    """某学生「已学」词（跨级别混合）。

    「已学」= recite_word_progress 里出现过的词，含尚未掌握
    （mastered_at 为空、只错过）的词。不是「已掌握」子集。回
    vocab_word 取释义/词性；词库里没有的词面跳过。

    fce_db_path 由调用方透传（与 VocabExamStore 同库）。None 时
    走 VocabExamStore 的默认路径。
    """
    user = (user or "").strip()[:60]
    if not user:
        return []
    store = VocabExamStore(fce_db_path)
    with store._connect() as conn:
        try:
            rows = conn.execute(
                "SELECT DISTINCT word FROM recite_word_progress"
                " WHERE user = ?", (user,)).fetchall()
        except sqlite3.OperationalError:
            return []
    learned = [r["word"] for r in rows if r["word"]]
    if not learned:
        return []
    rich = _load_words_by_names(learned)
    return [rich[w] for w in learned if w in rich]


def _pick_diverse(words: list[dict], n: int, rng: random.Random) -> list[dict]:
    """多样选词：尽量覆盖不同首字母与不同词性（贪心轮转 + 兜底补齐）。"""
    by_initial: dict[str, list] = defaultdict(list)
    for w in words:
        by_initial[(w["word"] or "?")[0].lower()].append(w)
    initials = list(by_initial.keys())
    rng.shuffle(initials)
    for k in initials:
        rng.shuffle(by_initial[k])
    pos_seen: set[str] = set()
    picked: list[dict] = []
    used: set[str] = set()
    for round_no in range(1, 40):
        if len(picked) >= n:
            break
        progressed = False
        for k in initials:
            if len(picked) >= n:
                break
            for w in by_initial[k]:
                if w["word"] in used:
                    continue
                # 前 8 轮跳过已见词性，强制词性多样性；之后放开
                if round_no <= 8 and w["pos"] in pos_seen:
                    continue
                used.add(w["word"])
                pos_seen.add(w["pos"])
                picked.append(w)
                progressed = True
                break
        if not progressed:
            break
    for w in words:  # 兜底：忽略多样性补齐
        if len(picked) >= n:
            break
        if w["word"] not in used:
            used.add(w["word"])
            picked.append(w)
    rng.shuffle(picked)
    return picked[:n]


def _spell_blank(word: str, rng: random.Random) -> tuple[str, str]:
    """拼写挖空：保留首字母，挖 2-3 个非首字母。返回 (展示串, 答案字母)。"""
    letters = list(word)
    if len(letters) < 4:
        blanks = list(range(1, len(letters)))
    else:
        n_blank = min(3, len(letters) - 1)
        blanks = sorted(rng.sample(range(1, len(letters)), n_blank))
    answer = "".join(letters[i] for i in blanks)
    display = "".join("_" if i in blanks else ch for i, ch in enumerate(letters))
    return display, answer


_HISTORY_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS vocab_paper_history ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " level INTEGER NOT NULL,"
    " paper_id TEXT NOT NULL UNIQUE,"
    " words TEXT NOT NULL,"
    " created_at TEXT)")


def list_paper_history(level: Optional[int] = None, limit: int = 50,
                       fce_db_path: Optional[str] = None,
                       include_mix: bool = False) -> list[dict]:
    """已生成试卷。level=None 为全部（含混合卷）。

    include_mix：指定级别时再带上混合卷（level<0）。两桶各自取
    limit 条再按 id 合并，避免「先取全局最近 N 条再在客户端筛本级」
    时本级卷被别的级别挤出窗口。
    """
    store = VocabExamStore(fce_db_path)
    limit = max(1, min(int(limit), 200))
    cols = "id, paper_id, level, words, created_at"
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        if level is None:
            rows = conn.execute(
                f"SELECT {cols} FROM vocab_paper_history"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        elif include_mix:
            rows_lv = conn.execute(
                f"SELECT {cols} FROM vocab_paper_history"
                " WHERE level = ? ORDER BY id DESC LIMIT ?",
                (int(level), limit)).fetchall()
            rows_mix = conn.execute(
                f"SELECT {cols} FROM vocab_paper_history"
                " WHERE level < 0 ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
            rows = sorted([*rows_lv, *rows_mix],
                          key=lambda r: r["id"], reverse=True)
        else:
            rows = conn.execute(
                f"SELECT {cols} FROM vocab_paper_history"
                " WHERE level = ? ORDER BY id DESC LIMIT ?",
                (int(level), limit)).fetchall()
    out = []
    for r in rows:
        try:
            ws = json.loads(r["words"] or "[]")
        except (ValueError, TypeError):
            ws = []
        out.append({"paper_id": r["paper_id"], "level": r["level"],
                    "words": ws, "count": len(ws),
                    "created_at": r["created_at"]})
    return out


def generate_paper(level: int, save: bool = True, scope: str = "level",
                   user: Optional[str] = None,
                   fce_db_path: Optional[str] = None) -> dict:
    """生成一份考试卷。save=False 仅预览不落历史。

    scope="level"：本级词池（原口径）。
    scope="learned"：user 的全部已学词（进度里出现过即可，含未掌握），
    跨级别混合。卷号 MIX- 前缀，level 存 -1。去重只对先前的混合卷
    （level<0），不用各级试卷把混合卷词池抽干。
    """
    rng = random.Random()
    scope = scope or "level"
    if scope not in ("level", "learned"):
        raise ValueError("scope 须为 level 或 learned")
    if scope == "learned":
        lvl_out, prefix = -1, "MIX"
        words = _load_learned_words(user or "", fce_db_path)
        if len(words) < 50:
            who = (user or "").strip() or "该学生"
            raise ValueError(f"{who} 已学词不足 50（现有 {len(words)}）")
    else:
        lvl_out, prefix = int(level), f"L{int(level)}"
        words = _load_words_rich(level)
        if len(words) < 50:
            raise ValueError(f"L{level} 词量不足 50（现有 {len(words)}）")

    store = VocabExamStore(fce_db_path)
    hist_words: set[str] = set()
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        if scope == "learned":
            rows = conn.execute(
                "SELECT words FROM vocab_paper_history WHERE level < 0"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT words FROM vocab_paper_history WHERE level = ?",
                (int(level),)).fetchall()
    for r in rows:
        try:
            hist_words.update(json.loads(r["words"] or "[]"))
        except (ValueError, TypeError):
            pass

    # 去重约束：优先历史未用词；不够时允许重叠（兜底阶段重叠 ≤10 由校验把关）
    fresh = [w for w in words if w["word"] not in hist_words]
    pool = fresh if len(fresh) >= 50 else fresh + [
        w for w in words if w["word"] in hist_words]
    picked = _pick_diverse(pool, 50, rng)
    if len(picked) < 50:
        raise ValueError(f"可选词不足 50（现有 {len(picked)}）")

    # 拼写段优先拿长词（≥4 字母才好挖空）：从全部选中词里挑最长的 10 个
    long_sorted = sorted(picked, key=lambda w: -len(w["word"]))
    spell_pool = [w for w in long_sorted if len(w["word"]) >= 4][:10]
    spell_words = {w["word"] for w in spell_pool}
    rest = [w for w in picked if w["word"] not in spell_words]
    # 英译中规避介词/人名：可译词优先；不够时用剩余词补（保证 20 题）
    translatable = [w for w in rest if not _skip_for_en2zh(w["pos"])]
    others = [w for w in rest if _skip_for_en2zh(w["pos"])]
    en2zh = (translatable + others)[:20]
    en2zh_ids = {w["word"] for w in en2zh}
    zh2en = [w for w in rest if w["word"] not in en2zh_ids][:20]
    spell = []
    for w in spell_pool:
        blanked, answer = _spell_blank(w["word"], rng)
        spell.append({**w, "gloss": w["gloss_clean"], "pos_cn": w["pos_cn"],
                      "blanked": blanked, "spell_answer": answer})

    overlap = sum(1 for w in picked if w["word"] in hist_words)
    if overlap > 10:
        raise ValueError(f"与历史重复 {overlap} 词超限（≤10）")
    all_words = [w["word"] for w in picked]

    paper_id = (f"{prefix}-"
                + datetime.now(_tz.utc).strftime("%Y%m%d%H%M%S")
                + f"-{rng.randrange(1000, 9999)}")
    paper = {
        "paper_id": paper_id,
        "level": lvl_out,
        "scope": scope,
        "user": (user or "").strip()[:60] if scope == "learned" else "",
        "created_at": datetime.now(_tz.utc).isoformat(timespec="seconds"),
        "overlap_with_history": overlap,
        "zh2en": [{"word": w["word"], "gloss": w["gloss_clean"],
                   "pos_cn": w["pos_cn"]}
                  for w in zh2en],
        "en2zh": [{"word": w["word"], "gloss": w["gloss"],
                   "pos_cn": w["pos_cn"]}
                  for w in en2zh],
        "spell": [{**s} for s in spell],
        "words": all_words,
    }
    if save:
        with store._connect() as conn:
            conn.execute(_HISTORY_TABLE_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO vocab_paper_history"
                " (level, paper_id, words, created_at) VALUES (?,?,?,?)",
                (lvl_out, paper_id,
                 json.dumps(all_words, ensure_ascii=False),
                 paper["created_at"]),
            )
    return paper


def save_paper(paper: dict, fce_db_path: Optional[str] = None) -> dict:
    """存档一份「预览态」生成的卷（生成→预览→保存 流程的第三步）。

    paper_id 形如 L{n}-... 或 MIX-...（混合卷 level 记 -1）。
    词表取 paper['words']，须为 50 词。幂等（INSERT OR REPLACE）。
    """
    pid = str((paper or {}).get("paper_id") or "")[:64]
    ws = (paper or {}).get("words") or []
    m = re.match(r"^(?:L(\d)|MIX)-", pid)
    if not m or not ws:
        raise ValueError("试卷数据不完整（paper_id/level/words）")
    if len(ws) != 50:
        raise ValueError("试卷须为 50 词")
    level = int(m.group(1)) if m.group(1) else -1
    created = str(paper.get("created_at") or "")[:40] or datetime.now(
        _tz.utc).isoformat(timespec="seconds")
    store = VocabExamStore(fce_db_path)
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO vocab_paper_history"
            " (level, paper_id, words, created_at) VALUES (?,?,?,?)",
            (level, pid, json.dumps(ws, ensure_ascii=False), created))
    return {"paper_id": pid, "level": level, "count": len(ws)}


def delete_paper(paper_id: str, fce_db_path: Optional[str] = None) -> bool:
    """删除一份已生成的试卷资产。"""
    store = VocabExamStore(fce_db_path)
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        cur = conn.execute(
            "DELETE FROM vocab_paper_history WHERE paper_id = ?",
            (str(paper_id)[:64],))
        return cur.rowcount > 0


def get_paper(paper_id: str, fce_db_path: Optional[str] = None) -> Optional[dict]:
    """取一份历史卷（含渲染 HTML，供预览）。"""
    store = VocabExamStore(fce_db_path)
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        row = conn.execute(
            "SELECT paper_id, level, words, created_at FROM vocab_paper_history"
            " WHERE paper_id = ?", (str(paper_id)[:64],),
        ).fetchone()
    if not row:
        return None
    try:
        ws = json.loads(row["words"] or "[]")
    except (ValueError, TypeError):
        ws = []
    # 从词库回填词条渲染试卷（历史卷可完整重建；词性一并带上）
    if row["level"] is not None and int(row["level"]) >= 0:
        words_rich = {w["word"]: w for w in _load_words_rich(row["level"])}
    else:
        # 混合卷（MIX- / level<0）：词跨级别，按词面回填
        words_rich = _load_words_by_names(list(ws))
    spell = []

    def _entry(w):
        rich = words_rich.get(w) or {}
        return {"word": w, "gloss": rich.get("gloss_clean") or rich.get("gloss", ""),
                "pos_cn": rich.get("pos_cn", "")}
    # 历史卷只存词序：前 20 中译英，次 20 英译中，末 10 拼写（生成时的段序）
    zh2en = [_entry(w) for w in ws[:20]]
    en2zh = [_entry(w) for w in ws[20:40]]
    spell_ws = ws[40:50]
    rng = random.Random(paper_id)
    for w in spell_ws:
        blanked, answer = _spell_blank(w, rng)
        spell.append({**_entry(w), "blanked": blanked, "spell_answer": answer})
    paper = {
        "paper_id": row["paper_id"], "level": row["level"],
        "created_at": row["created_at"], "overlap_with_history": 0,
        "zh2en": zh2en, "en2zh": en2zh, "spell": spell, "words": ws,
    }
    return paper


def render_new_paper_html(paper: dict, title: str = "") -> str:
    """可打印考卷：三题型分区 + 答案页（独立成页可撕）。"""
    e = html.escape
    lvl = paper["level"]
    pid = paper["paper_id"]
    if title:
        t = title
    elif lvl is not None and int(lvl) < 0:
        who = (paper.get("user") or "").strip()
        t = f"哈一词汇考试 · 已学全部混合卷（{who}）" if who else "哈一词汇考试 · 已学全部混合卷"
    else:
        t = f"哈一词汇考试 · L{lvl} 级"

    zh2en = "".join(
        f'<div class="q"><span class="n">{i}</span>'
        f'<span class="pos">{e(w.get("pos_cn") or "")}</span>'
        f'<span class="zh">{e(w["gloss"])}</span><span class="line"></span></div>'
        for i, w in enumerate(paper["zh2en"], 1))
    en2zh = "".join(
        f'<div class="q"><span class="n">{i}</span>'
        f'<span class="en">{e(w["word"])}</span>'
        f'<span class="pos">{e(w.get("pos_cn") or "")}</span>'
        f'<span class="line short"></span></div>'
        for i, w in enumerate(paper["en2zh"], 1))
    spell = "".join(
        f'<div class="q"><span class="n">{i}</span>'
        f'<span class="pos">{e(w.get("pos_cn") or "")}</span>'
        f'<span class="zh">{e(w["gloss"])}</span>'
        f'<span class="blanked">{e(w["blanked"])}</span></div>'
        for i, w in enumerate(paper["spell"], 1))
    a1 = "　".join(f'{i}.{e(w["word"])}（{e(w.get("pos_cn") or "")}）'
                   for i, w in enumerate(paper["zh2en"], 1))
    a2 = "　".join(f'{i}.{e(w["gloss"][:14])}'
                   for i, w in enumerate(paper["en2zh"], 1))
    a3 = "　".join(f'{i}.{e(w["spell_answer"])}'
                   for i, w in enumerate(paper["spell"], 1))

    return render_template(
        "new_paper.html",
        title_esc=e(t), pid_esc=e(pid),
        zh2en=zh2en, en2zh=en2zh, spell=spell, a1=a1, a2=a2, a3=a3,
    )

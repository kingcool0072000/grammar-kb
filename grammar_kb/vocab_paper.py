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
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .vocab_exam import _grammar_db_path, VocabExamStore


def _pos_list(pos: str) -> list[str]:
    try:
        v = json.loads(pos or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _skip_for_en2zh(pos: str) -> bool:
    """英译中规避：凡 pos 含 prep（介词）或 proper（人名/地名）即跳过。

    介词义主导的词（含 like/after/per/beneath 这类多性词）单独给英文
    让考生写中文，答案发散、考察意义弱——从严全滤，宁缺毋滥。
    """
    tags = set(_pos_list(pos))
    return bool(tags & {"prep", "proper"})


def _load_words_rich(level: int) -> list[dict]:
    """本级词条：word/pos/gloss（pos 解析为列表）。"""
    path = _grammar_db_path()
    if not path or not Path(path).exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT word, pos, gloss FROM vocab_word WHERE level = ?",
            (int(level),)).fetchall()
    return [{"word": r["word"], "pos": (r["pos"] or "").strip(),
             "pos_list": _pos_list(r["pos"]),
             "gloss": (r["gloss"] or "").strip()} for r in rows]


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


def list_paper_history(level: Optional[int] = None, limit: int = 50
                       ) -> list[dict]:
    store = VocabExamStore()
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        if level is None:
            rows = conn.execute(
                "SELECT paper_id, level, words, created_at FROM vocab_paper_history"
                " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        else:
            rows = conn.execute(
                "SELECT paper_id, level, words, created_at FROM vocab_paper_history"
                " WHERE level = ? ORDER BY id DESC LIMIT ?",
                (int(level), int(limit))).fetchall()
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


def generate_paper(level: int, save: bool = True) -> dict:
    """生成一份本级考试卷。save=False 仅预览不落历史。"""
    rng = random.Random()
    words = _load_words_rich(level)
    if len(words) < 50:
        raise ValueError(f"L{level} 词量不足 50（现有 {len(words)}）")

    store = VocabExamStore()
    hist_words: set[str] = set()
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
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
        raise ValueError(f"L{level} 可选词不足 50（现有 {len(picked)}）")

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
        spell.append({**w, "blanked": blanked, "spell_answer": answer})

    overlap = sum(1 for w in picked if w["word"] in hist_words)
    if overlap > 10:
        raise ValueError(f"与历史重复 {overlap} 词超限（≤10）")
    all_words = [w["word"] for w in picked]

    paper_id = (f"L{level}-"
                + datetime.now(_tz.utc).strftime("%Y%m%d%H%M%S")
                + f"-{rng.randrange(1000, 9999)}")
    paper = {
        "paper_id": paper_id,
        "level": int(level),
        "created_at": datetime.now(_tz.utc).isoformat(timespec="seconds"),
        "overlap_with_history": overlap,
        "zh2en": [{"word": w["word"], "gloss": w["gloss"]} for w in zh2en],
        "en2zh": [{"word": w["word"], "gloss": w["gloss"]} for w in en2zh],
        "spell": spell,
        "words": all_words,
    }
    if save:
        with store._connect() as conn:
            conn.execute(_HISTORY_TABLE_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO vocab_paper_history"
                " (level, paper_id, words, created_at) VALUES (?,?,?,?)",
                (int(level), paper_id,
                 json.dumps(all_words, ensure_ascii=False),
                 paper["created_at"]),
            )
    return paper


def delete_paper(paper_id: str) -> bool:
    """删除一份已生成的试卷资产。"""
    store = VocabExamStore()
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        cur = conn.execute(
            "DELETE FROM vocab_paper_history WHERE paper_id = ?",
            (str(paper_id)[:64],))
        return cur.rowcount > 0


def get_paper(paper_id: str) -> Optional[dict]:
    """取一份历史卷（含渲染 HTML，供预览）。"""
    store = VocabExamStore()
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
    # 从词库回填词条渲染试卷（历史卷可完整重建）
    words_rich = {w["word"]: w for w in _load_words_rich(row["level"])}
    zh2en, en2zh, spell = [], [], []
    spell_map = {}
    for w in ws:
        rich = words_rich.get(w) or {}
        zh2en.append({"word": w, "gloss": rich.get("gloss", "")})
    # 历史卷只存词序：前 20 中译英，次 20 英译中，末 10 拼写（生成时的段序）
    zh2en, en2zh, spell_ws = ws[:20] and [
        {"word": w, "gloss": (words_rich.get(w) or {}).get("gloss", "")}
        for w in ws[:20]], [
        {"word": w, "gloss": (words_rich.get(w) or {}).get("gloss", "")}
        for w in ws[20:40]], ws[40:50]
    rng = random.Random(paper_id)
    for w in spell_ws:
        blanked, answer = _spell_blank(w, rng)
        spell.append({"word": w, "gloss": (words_rich.get(w) or {}).get("gloss", ""),
                      "blanked": blanked, "spell_answer": answer})
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
    t = title or f"哈一词汇考试 · L{lvl} 级"

    zh2en = "".join(
        f'<div class="q"><span class="n">{i}</span>'
        f'<span class="zh">{e(w["gloss"])}</span><span class="line"></span></div>'
        for i, w in enumerate(paper["zh2en"], 1))
    en2zh = "".join(
        f'<div class="q"><span class="n">{i}</span>'
        f'<span class="en">{e(w["word"])}</span>'
        f'<span class="line short"></span></div>'
        for i, w in enumerate(paper["en2zh"], 1))
    spell = "".join(
        f'<div class="q"><span class="n">{i}</span>'
        f'<span class="zh">{e(w["gloss"])}</span>'
        f'<span class="blanked">{e(w["blanked"])}</span></div>'
        for i, w in enumerate(paper["spell"], 1))
    a1 = "　".join(f'{i}.{e(w["word"])}'
                   for i, w in enumerate(paper["zh2en"], 1))
    a2 = "　".join(f'{i}.{e(w["gloss"][:14])}'
                   for i, w in enumerate(paper["en2zh"], 1))
    a3 = "　".join(f'{i}.{e(w["spell_answer"])}'
                   for i, w in enumerate(paper["spell"], 1))

    return f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>{e(t)}</title>
<style>
  body {{ font-family: "Songti SC","SimSun",serif; margin: 32px; color: #111; }}
  h1 {{ font-size: 20px; text-align: center; margin: 0 0 4px; }}
  .meta {{ text-align: center; font-size: 12px; color: #555; margin-bottom: 16px; }}
  h2 {{ font-size: 15px; margin: 14px 0 8px; }}
  .q {{ display: flex; align-items: baseline; gap: 8px; margin-bottom: 9px; }}
  .n {{ width: 22px; font-weight: 700; }}
  .zh {{ font-size: 14px; }}
  .en {{ font-size: 14px; font-family: Georgia,serif; }}
  .line {{ flex: 1; border-bottom: 1px solid #999; height: 14px; }}
  .line.short {{ max-width: 220px; }}
  .blanked {{ font-family: "Courier New",monospace; font-size: 15px; letter-spacing: 2px; }}
  .pagebreak {{ page-break-before: always; }}
  .ans-sec {{ margin-bottom: 12px; font-size: 13px; line-height: 1.8; }}
  .ans-sec b {{ display: block; margin-bottom: 4px; }}
  @media print {{ body {{ margin: 14px; }} }}
</style></head><body>
<h1>{e(t)}</h1>
<div class="meta">卷号 {e(pid)} · 姓名__________ · 得分______</div>
<h2>一、中译英（20 题 · 每题 2.5 分）</h2>
{zh2en}
<h2>二、英译中（20 题 · 每题 2.5 分）</h2>
{en2zh}
<h2>三、拼写（10 题 · 每题 5 分 · 在空格处填写字母）</h2>
{spell}
<div class="pagebreak"></div>
<h1>{e(t)} · 答案页</h1>
<div class="meta">卷号 {e(pid)}（本页可撕开）</div>
<div class="ans-sec"><b>一、中译英</b><p>{a1}</p></div>
<div class="ans-sec"><b>二、英译中</b><p>{a2}</p></div>
<div class="ans-sec"><b>三、拼写挖空</b><p>{a3}</p></div>
</body></html>'''

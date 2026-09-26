"""词汇级别考试：线下考卷生成 + 成绩登记（fce.db 同库）。

解锁规则（2026-09-26 起，取代教师手动 vocabUnlock）：
- L0 默认开放；通过 L{n} 级考卷（≥80 分，教师登记成绩）自动解锁 L{n+1}。
- 考卷线下进行：家长持「答案页」读题（听写部分读音），孩子在纸质卷作答。

考卷结构（50 词 × 2 分 = 100 分，及格 80）：
- A 听写 15 题：家长读英文单词，孩子拼写
- B 中译英 15 题：中文释义 → 写英文单词
- C 英译中 15 题：英文单词 → 写中文释义
- D 拼写补全 5 题：挖空字母补全（如 r_c_ive）
词从 vocab_word 指定级别均匀随机抽取（seed 可复现同一份卷）。
"""
from __future__ import annotations

import html
import json
import random
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vocab_exams (
    id         INTEGER PRIMARY KEY,
    user       TEXT NOT NULL,
    level      INTEGER NOT NULL,
    score      INTEGER NOT NULL,
    exam_date  TEXT NOT NULL,
    note       TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_vex_user ON vocab_exams(user, level, score);
"""

PASS_SCORE = 80


class VocabExamStore:
    """vocab_exams 读写 + 解锁级别计算。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def record(self, user: str, level: int, score: int, exam_date: str, note: str = "") -> dict:
        if not (0 <= int(level) <= 7):
            raise ValueError("level 须为 0-7")
        if not (0 <= int(score) <= 100):
            raise ValueError("score 须为 0-100")
        datetime.strptime(exam_date, "%Y-%m-%d")  # 非法日期抛 ValueError
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO vocab_exams (user, level, score, exam_date, note, created_at)"
                " VALUES (?,?,?,?,?,?)",
                ((user or "").strip()[:60], int(level), int(score), exam_date,
                 (note or "")[:500],
                 datetime.now(_tz.utc).isoformat(timespec="seconds")),
            )
            row = conn.execute(
                "SELECT * FROM vocab_exams WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return dict(row)

    def list(self, user: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM vocab_exams"
        args: tuple = ()
        if user:
            sql += " WHERE user = ?"
            args = (user,)
        sql += " ORDER BY exam_date DESC, id DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def unlocked_level(self, user: str) -> int:
        """学生当前最高可用级别：L0 恒开；L{n} 考试 ≥80 → 解锁 L{n+1}。"""
        passed = self.passed_levels(user)
        return min(5, max(passed) + 1) if passed else 0

    def passed_levels(self, user: str) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT level FROM vocab_exams"
                " WHERE user = ? AND score >= ?",
                ((user or "").strip()[:60], PASS_SCORE),
            ).fetchall()
        return {r["level"] for r in rows}


# ---- 考卷生成 ---------------------------------------------------------------- #

def _grammar_db_path() -> Optional[str]:
    try:
        from .ingest import default_db_path
        p = default_db_path()
        return p if Path(p).exists() else None
    except Exception:
        return None


def load_level_words(level: int, limit: int = 0) -> list[dict]:
    """取某级别词条（word/gloss/meanings），按词频或字母序。"""
    path = _grammar_db_path()
    if not path:
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        sql = ("SELECT word, pos, gloss, meanings, extra FROM vocab_word"
               " WHERE level = ?")
        rows = conn.execute(sql, (int(level),)).fetchall()
    out = []
    for r in rows:
        try:
            meanings = json.loads(r["meanings"] or "[]")
        except (ValueError, TypeError):
            meanings = []
        # L0 的 meanings[0] 常是语料例句而非释义（many→“都表示‘许多’…”），
        # 题面用 gloss（词典式紧凑释义）兜底切分
        gloss = (r["gloss"] or "").strip()
        meaning = gloss.split("/")[0].split(",")[0].strip() or gloss
        if not meaning and meanings and len(str(meanings[0])) <= 12:
            meaning = str(meanings[0])
        out.append({
            "word": r["word"], "gloss": gloss, "meaning": meaning or "—",
            "freq": int(json.loads(r["extra"] or "{}").get("freq") or 0),
        })
    out.sort(key=lambda x: (-(x["freq"] or 0), x["word"]))
    if limit:
        out = out[:limit]
    return out


def _blank_word(word: str, rng: random.Random) -> str:
    """挖空 1-3 个字母（避开首字母；只挖字母字符；短词少挖）。"""
    idxs = [i for i, ch in enumerate(word[1:], start=1) if ch.isalpha()]
    n = min(3, max(1, len(idxs) - 1)) if idxs else 0
    holes = set(rng.sample(idxs, n)) if n else set()
    return "".join("_" if i in holes else ch for i, ch in enumerate(word))


def build_paper(level: int, seed: Optional[int] = None) -> dict:
    """抽 50 词组卷。返回 {sections, words}；词量不足时按比例缩减各部分。"""
    rng = random.Random(seed)
    words = load_level_words(level)
    if len(words) < 50:
        raise ValueError(f"L{level} 词量不足 50（现有 {len(words)}）")
    # 覆盖优先：词频前 120 抽 25（核心必考）+ 其余随机 25（覆盖面）
    core = rng.sample(words[:120], 25)
    core_words = {x["word"] for x in core}
    rest_pool = [w for w in words if w["word"] not in core_words]
    rest = rng.sample(rest_pool, 25)
    picked = core + rest
    rng.shuffle(picked)
    a, b, c, d = picked[:15], picked[15:30], picked[30:45], picked[45:50]
    return {
        "level": level,
        "seed": seed,
        "sections": {
            "dictation": a,     # 听写（家长读音）
            "zh2en": b,         # 中译英
            "en2zh": c,         # 英译中
            "spell": d,         # 拼写补全（挑 ≥5 字母词）
        },
        "words": picked,
    }


def render_paper_html(paper: dict, student: str = "") -> str:
    """渲染可打印 A4 考卷（试卷页 + 答案页，答案页独立成页便于线下撕开）。"""
    lvl = paper["level"]
    rng = random.Random((paper.get("seed") or 0) + 7)
    s = paper["sections"]
    today = datetime.now().strftime("%Y-%m-%d")
    esc = html.escape

    def rows(items, inner):
        return "".join(
            f'<div class="q"><span class="no">{i+1}.</span>{inner(w, i)}</div>'
            for i, w in enumerate(items)
        )

    sec_a = rows(s["dictation"], lambda w, i: '<span class="dict-hint">🎧 听录音写单词</span><span class="blank-line"></span>')
    sec_b = rows(s["zh2en"], lambda w, i: f'<b class="zh">{esc(w["meaning"] or w["gloss"] or "—")}</b><span class="blank-line"></span>')
    sec_c = rows(s["en2zh"], lambda w, i: f'<b class="en">{esc(w["word"])}</b><span class="blank-line short"></span>')
    spell_items = [w for w in s["spell"]]
    sec_d = rows(spell_items, lambda w, i: f'<b class="en mono">{esc(_blank_word(w["word"], rng))}</b><span class="blank-line short"></span>')

    ans_a = rows(s["dictation"], lambda w, i: f'<b class="en">{esc(w["word"])}</b><span class="zh muted">{esc(w["meaning"] or "")}</span>')
    ans_b = rows(s["zh2en"], lambda w, i: f'<b class="en">{esc(w["word"])}</b>')
    ans_c = rows(s["en2zh"], lambda w, i: f'<span class="zh">{esc(w["meaning"] or w["gloss"] or "")}</span>')
    ans_d = rows(spell_items, lambda w, i: f'<b class="en">{esc(w["word"])}</b>')

    name_line = esc(student) or "＿＿＿＿＿"
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>词汇级别考卷 L{lvl} → L{lvl+1}</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "PingFang SC","Songti SC","Times New Roman",serif;
         color:#222; line-height:1.7; margin:0; padding:24px; max-width:800px; margin:0 auto; }}
  h1 {{ font-size:20px; text-align:center; margin:0 0 4px; letter-spacing:2px; }}
  .meta {{ text-align:center; font-size:13px; color:#555; margin-bottom:18px; }}
  .meta b {{ color:#222; }}
  .head-line {{ display:flex; justify-content:space-between; font-size:14px;
                border-bottom:2px solid #222; padding-bottom:8px; margin-bottom:14px; }}
  h2 {{ font-size:15px; margin:18px 0 6px; }}
  h2 small {{ font-weight:400; color:#666; font-size:12px; }}
  .q {{ display:flex; align-items:baseline; gap:8px; padding:7px 2px; border-bottom:1px dotted #ccc; }}
  .no {{ flex:0 0 24px; color:#555; font-size:13px; }}
  .en {{ font-family:"Times New Roman",serif; }}
  .mono {{ letter-spacing:1px; font-family:"Menlo",monospace; }}
  .zh {{ font-size:14px; }}
  .muted {{ color:#777; font-size:12px; margin-left:8px; }}
  .blank-line {{ flex:1; border-bottom:1px solid #999; min-height:20px; }}
  .blank-line.short {{ flex:0 1 200px; }}
  .dict-hint {{ color:#999; font-size:12px; }}
  .pagebreak {{ page-break-before:always; }}
  .score-box {{ float:right; border:2px solid #222; padding:6px 18px; font-size:15px; }}
  .tips {{ background:#f6f2e7; border:1px solid #e2d9c0; border-radius:8px;
           padding:10px 14px; font-size:12.5px; color:#555; margin:10px 0 0; }}
  ol.tips-list {{ margin:4px 0 0; padding-left:18px; }}
  @media print {{ body {{ padding:0; }} .tips {{ background:#fff; }} }}
</style></head><body>
<div class="score-box">得分：＿＿＿＿</div>
<h1>词汇级别考卷 · L{lvl} → L{lvl+1}</h1>
<div class="meta">共 50 题 · 每题 2 分 · 满分 100 · <b>80 分解锁 L{lvl+1}</b> · {today}</div>
<div class="head-line"><span>姓名：{name_line}</span><span>用时：＿＿＿ 分钟</span></div>
<div class="tips">考试说明：独立完成，不查词不提示。
<ol class="tips-list">
<li>第一部分「听写」由家长按答案页朗读英文单词（每词读两遍），孩子听音拼写；</li>
<li>「中译英」写英文单词（注意拼写），「英译中」写中文意思；</li>
<li>「拼写补全」把下划线处补全成完整单词。</li>
</ol></div>

<h2>一、听写（家长读音，写英文单词）<small>15 题 × 2 分</small></h2>
{sec_a}
<h2>二、中译英（写英文单词）<small>15 题 × 2 分</small></h2>
{sec_b}
<h2>三、英译中（写中文意思）<small>15 题 × 2 分</small></h2>
{sec_c}
<h2>四、拼写补全<small>5 题 × 2 分</small></h2>
{sec_d}

<div class="pagebreak"></div>
<h1>答案页（家长保管）</h1>
<div class="meta">L{lvl} → L{lvl+1} · 听写部分按行朗读（每词两遍）· 每题 2 分</div>
<h2>一、听写答案<small>（读音用）</small></h2>
{ans_a}
<h2>二、中译英答案</h2>
{ans_b}
<h2>三、英译中答案</h2>
{ans_c}
<h2>四、拼写补全答案</h2>
{ans_d}
</body></html>"""

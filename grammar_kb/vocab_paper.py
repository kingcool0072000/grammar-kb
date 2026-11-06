"""新版单词试卷生成（考试资产：与具体学生无关的 level 卷 + 按学生的混合卷）。

v2 重构口径（与前端 vocabLevel 出题配置、routes_vocab 同契）：

- **统一形态**：生成、存档、取回共用同一 paper dict，``sections`` 存什么
  渲染什么——修复旧版「落库已 shuffle 的词序、取回按位置还原分段」导致
  的分段错乱。
- **五题型**：词形变化(form) / 拼写挖空(spell) / 例句填空(ctx) /
  中译英(zh2en) / 英译中(en2zh)，按 ``ratios`` 配比出题（默认 12/8/10/10/10）。
- **分层出题**：简单词（vocab_word.level ≤ SIMPLE_MAX_LEVEL）优先词形变化
  题（extra.forms，ECDICT 实测优先）；复杂词优先例句填空题（example.en 里
  定位该词或其变形挖空）；各自不可行时按序退化，保证每词恰进一个题型段。
- **选词来源可配**（preset / source_mix）：本级或已学词池之外，还能按比例
  混入学生的「积压错词」——艾宾浩斯队列未攻克且已到期（recite_review）
  ∪ 错过且未掌握（recite_word_progress）。
- **seed 可复现**：同 seed 同卷（含题目顺序与挖空位置）。

历史兼容：vocab_paper_history 旧行只存 50 词词序（前 20 中译英、次 20
英译中、末 10 拼写），get_paper 对无 structure 的旧行按此还原并组装成
sections 形态返回。
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

from .atlas_vocab import exam_allowed
from .clock import today_cn
from .dbreg import connect as dbreg_connect
from .dbreg import fce_db_path as _default_fce_db
from .dbreg import grammar_db_path as _default_grammar_db
from .paper_html import render_template
from .vocabulary import word_forms

# 简单词阈值：vocab_word.level ≤ 该值的词优先出词形变化题
SIMPLE_MAX_LEVEL = 1

# 词形变化键 → 中文标签（与 web/src/fmt.js FORM_CN 全称口径一致）
FORM_CN = {
    "past": "过去式", "past_participle": "过去分词", "present_participle": "现在分词",
    "third_singular": "第三人称单数", "plural": "复数",
    "comparative": "比较级", "superlative": "最高级",
}

# 题型段顺序与中文标题（sections 的固定排列顺序）
SECTION_ORDER = ("form", "spell", "ctx", "zh2en", "en2zh")
SECTION_TITLES = {"form": "词形变化", "spell": "拼写挖空", "ctx": "例句填空",
                  "zh2en": "中译英", "en2zh": "英译中"}

# 默认题型配比（合计 50 题）；custom ratios 只允许覆盖这些键
DEFAULT_RATIOS = {"form": 12, "spell": 8, "ctx": 10, "zh2en": 10, "en2zh": 10}

# 预设选词来源：standard=纯词池；reinforce=六成积压错词 + 四成词池；
# recycle=全量积压错词回炉。显式 source_mix 覆盖 preset。
PRESET_SOURCE_MIX: dict[str, dict | None] = {
    "standard": None,
    "reinforce": {"backlog_wrong": 0.6, "pool": 0.4},
    "recycle": {"backlog_wrong": 1.0},
}

# 与历史试卷重复 ≤10 词
MAX_HISTORY_OVERLAP = 10

# 例句质量护栏：英文字段混入中日韩字符视为语料脏数据（答案串等），不出例句题
_CJK_CHAR = re.compile(r"[一-鿿]")


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
        _GLOSS_POS_PREFIX = re.compile(
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
        _NAME_GLOSS_RE = re.compile(r"[（(][^)）]*名[)）]")
    return bool(_NAME_GLOSS_RE.search(gl))


def _skip_for_en2zh(pos: str) -> bool:
    """英译中规避：凡 pos 含 prep（介词）或 proper（人名/地名）即排后。

    介词义主导的词（含 like/after/per/beneath 这类多性词）单独给英文
    让考生写中文，答案发散、考察意义弱——优先回避，不够时才兜底。
    """
    tags = set(_pos_list(pos))
    return bool(tags & {"prep", "proper"})


def _grammar_db_path() -> str | None:
    """grammar.db 路径（存在性校验；不可用返回 None）。

    本模块自持一份而非从 vocab_exam 导入私有实现；测试会 monkeypatch
    此名字指到临时库。
    """
    try:
        p = _default_grammar_db()
        return p if Path(p).exists() else None
    except Exception:
        return None


def _loads_dict(raw) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _rich_rows(rows) -> list[dict]:
    """vocab_word 原始行 → 富词条（pos 解析、forms/example 解析、专名规避）。

    列按存在性取（测试临时库可能没有 extra/example 列）；forms 值只留
    非空字符串。row 可能来自 sqlite3.Row，用 keys() 探测列。
    """
    out = []
    for r in rows:
        keys = set(r.keys())
        pos = (r["pos"] or "").strip()
        gloss = (r["gloss"] or "").strip()
        if _is_name_word(pos, gloss):
            continue
        extra = _loads_dict(r["extra"]) if "extra" in keys else {}
        forms_raw = extra.get("forms")
        forms = {}
        if isinstance(forms_raw, dict):
            forms = {k: v.strip() for k, v in forms_raw.items()
                     if k in FORM_CN and isinstance(v, str) and v.strip()}
        if not forms:
            # L1+ 词库未做语料增强（extra.forms 仅 L0 有）：按词性现算
            # （ECDICT exchange 实测优先，规则兜底）。与原形相同的变形
            # 不出题（原形不变类）。
            try:
                derived, _note = word_forms(r["word"], _pos_list(pos))
                forms = {k: v.strip() for k, v in derived.items()
                         if k in FORM_CN and isinstance(v, str) and v.strip()
                         and v.strip().lower() != r["word"].lower()}
            except Exception:
                forms = {}
        example = _loads_dict(r["example"]) if "example" in keys else {}
        out.append({"word": r["word"],
                    "level": r["level"] if "level" in keys else None,
                    "pos": pos, "pos_list": _pos_list(pos), "pos_cn": _pos_cn(pos),
                    "gloss": gloss, "gloss_clean": _strip_gloss_pos(gloss),
                    "forms": forms,
                    "example": {"en": str(example.get("en") or ""),
                                "zh": str(example.get("zh") or "")}})
    return out


def _load_words_rich(level: int) -> list[dict]:
    """本级词条：word/level/pos/gloss/forms/example（pos 解析为列表）。

    人名/地名/专名全局规避（pos=proper 或 gloss 标注 男子名/女子名/
    人名）：这类词进考题没有考察意义（tom→汤姆、beijing→北京），
    且中译英答案发散。exam_allowed 过滤词光标记 exam=false 的词。
    """
    path = _grammar_db_path()
    if not path:
        return []
    with dbreg_connect(path, readonly=True) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(vocab_word)")}
        sql = "SELECT word, level, pos, gloss"
        if "extra" in cols:
            sql += ", extra"
        if "example" in cols:
            sql += ", example"
        sql += " FROM vocab_word WHERE level = ?"
        rows = conn.execute(sql, (int(level),)).fetchall()
    rows = [r for r in rows if "extra" not in cols or exam_allowed(r["extra"])]
    return _rich_rows(rows)


def _load_words_by_names(names: list[str]) -> dict[str, dict]:
    """按词面批量取富词条（跨级别；混合卷/积压错词重建用），{word: rich}。"""
    if not names:
        return {}
    path = _grammar_db_path()
    if not path:
        return {}
    out: dict[str, dict] = {}
    chunk = 400
    with dbreg_connect(path, readonly=True) as conn:
        for i in range(0, len(names), chunk):
            part = names[i:i + chunk]
            rows = conn.execute(
                "SELECT word, level, pos, gloss, extra, example FROM vocab_word"
                f" WHERE word IN ({','.join('?' * len(part))})",
                part).fetchall()
            for w in _rich_rows(rows):
                out[w["word"]] = w
    return out


def _load_learned_words(user: str | None, fce_db_path: str | None = None) -> list[dict]:
    """某学生「已学」词（跨级别混合）。

    「已学」= recite_word_progress 里出现过的词，含尚未掌握
    （mastered_at 为空、只错过）的词。不是「已掌握」子集。回
    vocab_word 取释义/词性；词库里没有的词面跳过。
    """
    user = (user or "").strip()[:60]
    if not user:
        return []
    path = fce_db_path or _default_fce_db()
    if not Path(path).exists():
        return []
    try:
        with dbreg_connect(path, readonly=True) as conn:
            rows = conn.execute(
                "SELECT DISTINCT word FROM recite_word_progress WHERE user = ?",
                (user,)).fetchall()
    except sqlite3.OperationalError:
        return []
    learned = [r["word"] for r in rows if r["word"]]
    if not learned:
        return []
    rich = _load_words_by_names(learned)
    return [rich[w] for w in learned if w in rich]


def _load_backlog_wrong(user: str | None, fce_db_path: str | None = None) -> list[dict]:
    """学生「积压错词」= 艾宾浩斯到期未攻克 ∪ 错过且未掌握（并集去重）。

    - recite_review：conquered_at IS NULL 且 next_review <= 北京今天
      （到期还没复习完的词，与 recite 模块 due_now 同口径）
    - recite_word_progress：wrong > 0 且 mastered_at IS NULL（错过、尚未掌握）
    经 _load_words_by_names 富化，词库里没有的词面跳过。
    """
    user = (user or "").strip()[:60]
    if not user:
        return []
    path = fce_db_path or _default_fce_db()
    if not Path(path).exists():
        return []
    today = today_cn().isoformat()
    sql = (
        "SELECT word FROM recite_review"
        " WHERE user = ? AND conquered_at IS NULL"
        " AND next_review IS NOT NULL AND next_review <= ?"
        " UNION SELECT word FROM recite_word_progress"
        " WHERE user = ? AND wrong > 0 AND mastered_at IS NULL"
    )
    try:
        with dbreg_connect(path, readonly=True) as conn:
            rows = conn.execute(sql, (user, today, user)).fetchall()
    except sqlite3.OperationalError:
        return []
    names = [r["word"] for r in rows if r["word"]]
    if not names:
        return []
    rich = _load_words_by_names(names)
    out, seen = [], set()
    for w in names:  # 保持查询顺序（复习队列在前）
        if w in rich and w not in seen:
            seen.add(w)
            out.append(rich[w])
    return out


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

# 旧表幂等补列（structure 存整卷 paper dict；config 冗余存配置便于直查）
_MIGRATE_COLUMNS = (
    ("structure", "TEXT"),
    ("config", "TEXT"),
    ("scope", "TEXT"),
    ("user", "TEXT DEFAULT ''"),
    ("seed", "INTEGER"),
)


def _normalize_ratios(ratios: dict | None) -> dict[str, int]:
    """题型配比：默认 12/8/10/10/10；custom 只允许覆盖已知题型键。"""
    base = {k: int(v) for k, v in DEFAULT_RATIOS.items()}
    if ratios:
        for k, v in ratios.items():
            if k not in base:
                raise ValueError(f"未知题型 {k}（可选：{'/'.join(SECTION_ORDER)}）")
            v = int(v)
            if v < 0:
                raise ValueError("ratios 各项须 ≥ 0")
            base[k] = v
    if sum(base.values()) < 1:
        raise ValueError("ratios 题量之和须 ≥ 1")
    return base


# ---- 题型 item 构建 -------------------------------------------------------- #

def _pick_form(w: dict, rng: random.Random) -> tuple[str | None, str | None]:
    """从词条 forms 里随机选一个可用变形键。无可用返回 (None, None)。"""
    usable = [(k, v) for k, v in (w.get("forms") or {}).items() if k in FORM_CN]
    if not usable:
        return None, None
    k, v = rng.choice(sorted(usable))
    return k, v


def _locate_ctx(word: str, forms: dict, en: str) -> tuple[str, str] | None:
    """在例句里定位该词或其任一变形（大小写不敏感、词边界匹配）。

    例句质量护栏：en 含中日韩字符（语料答案串混入，如「答案：didn't，…」）
    或已带挖空记号的，不出例句填空题。定位失败返回 None。
    """
    en = (en or "").strip()
    if not en or "____" in en or _CJK_CHAR.search(en):
        return None
    cands: list[str] = []
    seen: set[str] = set()
    for c in [word, *(forms or {}).values()]:
        c = (c or "").strip()
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            cands.append(c)
    for c in cands:
        m = re.search(rf"(?<!\w){re.escape(c)}(?!\w)", en, re.IGNORECASE)
        if m:
            return en[:m.start()] + "____" + en[m.end():], m.group(0)
    return None


def _form_item(w: dict, form_key: str, answer: str) -> dict:
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss_clean"],
            "form_key": form_key, "form_label": FORM_CN[form_key], "answer": answer}


def _spell_item(w: dict, blanked: str, answer: str) -> dict:
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss_clean"],
            "blanked": blanked, "answer": answer}


def _ctx_item(w: dict, located: tuple[str, str]) -> dict:
    sentence, answer = located
    ex = w.get("example") or {}
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss_clean"],
            "sentence": sentence, "answer": answer, "zh": str(ex.get("zh") or "")}


def _zh2en_item(w: dict) -> dict:
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss_clean"]}


def _en2zh_item(w: dict) -> dict:
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss"]}


def _is_simple_word(w: dict) -> bool:
    lvl = w.get("level")
    return (isinstance(lvl, int) and not isinstance(lvl, bool)
            and lvl <= SIMPLE_MAX_LEVEL)


def _build_sections(words: list[dict], quota: dict[str, int],
                    rng: random.Random) -> list[dict]:
    """分层题型分配：每词恰进一个题型段，段内题数按 quota 凑。

    - 简单词（level ≤ SIMPLE_MAX_LEVEL）优先 form，复杂词优先 ctx；
      分层是「排序优先」而非「资格限制」——form/ctx 的可出词集合放开到
      全部词（form 需有可用 forms，ctx 需例句定位成功），同级内按
      picked 的多样顺序稳定排序。否则纯简单词卷（如 L0）ctx 配额永远
      落空，例句资源全废。
    - 拼写段长词优先（≥2 字母可挖空，沿用旧「挑最长词」口径）。
    - 某段可出词不足时，富余词自然落到后面的段（zh2en/en2zh 兜底交替承接），
      保证选中词全部成题；各段实际题数以 items 为准。
    """
    form_ready: dict[str, tuple[str, str]] = {}
    for w in words:
        fk, fa = _pick_form(w, rng)
        if fk:
            form_ready[w["word"]] = (fk, fa)
    ctx_ready: dict[str, tuple[str, str]] = {}
    for w in words:
        ex = w.get("example") or {}
        hit = _locate_ctx(w["word"], w.get("forms") or {}, ex.get("en") or "")
        if hit:
            ctx_ready[w["word"]] = hit

    used: set[str] = set()
    buckets: dict[str, list[dict]] = {t: [] for t in SECTION_ORDER}

    def grab(n: int, pred=None, order_key=None) -> list[dict]:
        cands = [w for w in words
                 if w["word"] not in used and (pred is None or pred(w))]
        if order_key:
            cands.sort(key=order_key)  # 稳定排序：同级保持 picked 顺序
        got = cands[:max(0, int(n))]
        for w in got:
            used.add(w["word"])
        return got

    # 1) 词形变化：有可用 forms 的词，简单词排前
    for w in grab(quota["form"], lambda w: w["word"] in form_ready,
                  order_key=lambda w: 0 if _is_simple_word(w) else 1):
        fk, fa = form_ready[w["word"]]
        buckets["form"].append(_form_item(w, fk, fa))
    # 2) 拼写挖空：剩余词里挑最长的（好挖空）
    for w in grab(quota["spell"], lambda w: len(w["word"]) >= 2,
                  order_key=lambda w: -len(w["word"])):
        blanked, ans = _spell_blank(w["word"], rng)
        buckets["spell"].append(_spell_item(w, blanked, ans))
    # 3) 例句填空：例句定位成功的词，复杂词排前
    for w in grab(quota["ctx"], lambda w: w["word"] in ctx_ready,
                  order_key=lambda w: 1 if _is_simple_word(w) else 0):
        buckets["ctx"].append(_ctx_item(w, ctx_ready[w["word"]]))
    # 4) 中译英：任意词
    for w in grab(quota["zh2en"]):
        buckets["zh2en"].append(_zh2en_item(w))
    # 5) 英译中：回避介词/专名优先，不够时兜底
    for w in grab(quota["en2zh"],
                  order_key=lambda w: 1 if _skip_for_en2zh(w["pos"]) else 0):
        buckets["en2zh"].append(_en2zh_item(w))
    # 兜底：quota 凑不满（form/ctx 可出词不足）时，富余词交替落中译英/英译中
    for i, w in enumerate(w for w in words if w["word"] not in used):
        used.add(w["word"])
        if i % 2 == 0:
            buckets["zh2en"].append(_zh2en_item(w))
        else:
            buckets["en2zh"].append(_en2zh_item(w))

    return [{"type": t, "title": SECTION_TITLES[t], "items": buckets[t]}
            for t in SECTION_ORDER if buckets[t]]


# ---- 选词 ------------------------------------------------------------------ #

def _select_words(total: int, backlog: list[dict], pool: list[dict],
                  hist: set[str], frac_backlog: float,
                  rng: random.Random) -> list[dict]:
    """按来源配比选词：积压错词优先填满再词池补；历史未用词优先。

    历史已用词只作兜底补齐（重叠计入 overlap，≤10 由调用方把关）。
    """
    picked: list[dict] = []
    used: set[str] = set()

    def take(ws: list[dict], n: int) -> None:
        if n <= 0:
            return
        got = _pick_diverse([w for w in ws if w["word"] not in used], n, rng)
        for w in got:
            used.add(w["word"])
        picked.extend(got)

    fresh_backlog = [w for w in backlog if w["word"] not in hist]
    fresh_pool = [w for w in pool if w["word"] not in hist]
    take(fresh_backlog, int(round(total * frac_backlog)) if frac_backlog else 0)
    if frac_backlog >= 1:
        # 全量回炉（frac=1）不碰词池；fresh 积压不足时用历史已用积压词兜底
        # （重叠计入 overlap，≤10 由调用方把关）
        take(backlog, total - len(picked))
    else:
        take(fresh_pool, total - len(picked))
        if len(picked) < total:  # 历史已用词兜底
            take(backlog, total - len(picked))
            take(pool, total - len(picked))
    return picked


# ---- 存档（fce.db vocab_paper_history） ------------------------------------ #

class VocabPaperStore:
    """vocab_paper_history 读写：试卷资产存档/取回/列表/删除。

    DDL 一次化：__init__ 建表 + 幂等补列（structure/config/scope/user/seed）。
    ``structure`` 存整卷 paper dict（含 sections），取回即所见——存什么渲染
    什么；``words`` 列保留词面列表，供历史去重与批改中心沿用。
    """

    def __init__(self, fce_db_path: str | None = None):
        self.db_path = fce_db_path or _default_fce_db()
        with dbreg_connect(self.db_path) as conn:
            conn.execute(_HISTORY_TABLE_SQL)
            cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(vocab_paper_history)")}
            for name, ddl in _MIGRATE_COLUMNS:
                if name not in cols:
                    conn.execute(
                        f"ALTER TABLE vocab_paper_history ADD COLUMN {name} {ddl}")

    def save_paper(self, paper: dict) -> dict:
        """存档一份卷（生成→预览→保存 的第三步，或生成时直接落库）。

        paper_id 形如 L{n}-... 或 MIX-...（混合卷 level 记 -1）。
        词数须与 config.total 一致（旧口径无 config 时为 50）。
        幂等（INSERT OR REPLACE）。返回 {paper_id, level, count}。
        """
        paper = paper or {}
        pid = str(paper.get("paper_id") or "")[:64]
        ws = list(paper.get("words") or [])
        m = re.match(r"^(?:L(\d+)|MIX)-", pid)
        if not m or not ws:
            raise ValueError("试卷数据不完整（paper_id/words）")
        expected = int((paper.get("config") or {}).get("total") or 0) or 50
        if len(ws) != expected:
            raise ValueError(f"试卷须为 {expected} 词（现有 {len(ws)}）")
        raw_level = paper.get("level")
        if isinstance(raw_level, int) and not isinstance(raw_level, bool):
            level = int(raw_level)
        else:
            level = int(m.group(1)) if m.group(1) else -1
        created = (str(paper.get("created_at") or "")[:40]
                   or datetime.now(_tz.utc).isoformat(timespec="seconds"))
        seed = paper.get("seed")
        row = (
            level, pid, json.dumps(ws, ensure_ascii=False), created,
            json.dumps(paper, ensure_ascii=False),
            json.dumps(paper.get("config") or {}, ensure_ascii=False),
            str(paper.get("scope") or ("learned" if level < 0 else "level")),
            str(paper.get("user") or "")[:60],
            int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else None,
        )
        with dbreg_connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vocab_paper_history"
                " (level, paper_id, words, created_at, structure, config,"
                "  scope, user, seed) VALUES (?,?,?,?,?,?,?,?,?)", row)
        return {"paper_id": pid, "level": level, "count": len(ws)}

    def get_paper(self, paper_id: str) -> dict | None:
        """取一份历史卷（统一形态；旧卷走位置还原 fallback）。"""
        pid = str(paper_id)[:64]
        with dbreg_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT paper_id, level, words, created_at, structure, config,"
                " scope, user, seed FROM vocab_paper_history WHERE paper_id = ?",
                (pid,)).fetchone()
        if not row:
            return None
        try:
            ws = json.loads(row["words"] or "[]")
        except (ValueError, TypeError):
            ws = []
        if not isinstance(ws, list):
            ws = []

        paper: dict = {}
        if row["structure"]:
            loaded = _loads_dict(row["structure"])
            if loaded:
                paper = loaded
        # 列值是权威：paper_id/level/words/created_at 以列为准回填
        paper.update({"paper_id": row["paper_id"], "level": row["level"],
                      "words": ws, "created_at": row["created_at"]})
        paper.setdefault("scope", (row["scope"] if row["scope"] else
                                   ("learned" if int(row["level"] or 0) < 0 else "level")))
        paper.setdefault("user", row["user"] or "")
        paper.setdefault("seed", row["seed"])
        paper.setdefault("overlap_with_history", 0)
        if not isinstance(paper.get("config"), dict):
            paper["config"] = _legacy_config(ws)
        if not paper.get("sections"):
            paper["sections"] = _sections_from_wordlist(
                ws, row["level"], row["paper_id"])
        return paper

    def delete_paper(self, paper_id: str) -> bool:
        """删除一份已生成的试卷资产，返回是否确有删除。"""
        with dbreg_connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM vocab_paper_history WHERE paper_id = ?",
                (str(paper_id)[:64],))
            return cur.rowcount > 0

    def list_history(self, level: int | None = None, limit: int = 50,
                     include_mix: bool = False) -> list[dict]:
        """已生成试卷。level=None 为全部（含混合卷）。

        include_mix：指定级别时再带上混合卷（level<0）。两桶各自取
        limit 条再按 id 合并，避免「先取全局最近 N 条再在客户端筛本级」
        时本级卷被别的级别挤出窗口。
        """
        limit = max(1, min(int(limit), 200))
        cols = "id, paper_id, level, words, created_at"
        with dbreg_connect(self.db_path) as conn:
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

    def _hist_words(self, scope: str, level: int | None) -> set[str]:
        """历史去重词集：learned 只看先前混合卷（level<0）；level 只看本级。"""
        if scope == "learned":
            sql = "SELECT words FROM vocab_paper_history WHERE level < 0"
            params: tuple = ()
        else:
            sql = "SELECT words FROM vocab_paper_history WHERE level = ?"
            params = (int(level or 0),)
        out: set[str] = set()
        with dbreg_connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        for r in rows:
            try:
                out.update(json.loads(r["words"] or "[]"))
            except (ValueError, TypeError):
                pass
        return out


def _legacy_config(ws: list) -> dict:
    """旧版卷的配置回填（位置还原口径：20 中译英 / 20 英译中 / 末段拼写）。"""
    n = len(ws)
    n_zh = min(20, n)
    n_en = min(20, max(0, n - 20))
    n_sp = max(0, n - 40)
    return {"total": n,
            "ratios": {"form": 0, "spell": n_sp, "ctx": 0,
                       "zh2en": n_zh, "en2zh": n_en},
            "simple_max_level": SIMPLE_MAX_LEVEL, "preset": "standard",
            "source_mix": None}


def _sections_from_wordlist(ws: list, level, paper_id: str) -> list[dict]:
    """旧版卷（只存词序）→ sections：前 20 中译英、次 20 英译中、末 10 拼写。

    词条从词库回填（历史卷可完整重建；词性一并带上）；拼写挖空用
    paper_id 作随机种子，同一卷多次取回挖空位置一致。
    """
    if not ws:
        return []
    if level is not None and int(level) >= 0:
        rich = {w["word"]: w for w in _load_words_rich(int(level))}
    else:
        rich = _load_words_by_names([w for w in ws if w])

    def _entry(w: str, use_clean: bool) -> dict:
        r = rich.get(w) or {}
        gloss = (r.get("gloss_clean") if use_clean else r.get("gloss")) \
            or r.get("gloss", "")
        return {"word": w, "pos_cn": r.get("pos_cn", ""), "gloss": gloss}

    zh2en = [_entry(w, True) for w in ws[:20]]
    en2zh = [_entry(w, False) for w in ws[20:40]]
    rng = random.Random(paper_id)
    spell = []
    for w in ws[40:]:
        blanked, answer = _spell_blank(w, rng)
        spell.append({**_entry(w, True), "blanked": blanked, "answer": answer})
    out = []
    if zh2en:
        out.append({"type": "zh2en", "title": SECTION_TITLES["zh2en"],
                    "items": zh2en})
    if en2zh:
        out.append({"type": "en2zh", "title": SECTION_TITLES["en2zh"],
                    "items": en2zh})
    if spell:
        out.append({"type": "spell", "title": SECTION_TITLES["spell"],
                    "items": spell})
    return out


# ---- 生成 ------------------------------------------------------------------ #

def _scale_ratios(ratio_map: dict[str, int], total: int) -> dict[str, int]:
    """把题型配比按比例缩放到指定总题量（最大余数法，和恰为 total）。"""
    total = max(10, min(200, int(total)))
    src_total = sum(ratio_map.values())
    if src_total == total:
        return dict(ratio_map)
    scaled = {k: v * total // src_total for k, v in ratio_map.items()}
    rest = total - sum(scaled.values())
    if rest > 0:  # 余数给占比大的段（同占比按键名稳定排序）
        order = sorted(ratio_map,
                       key=lambda k: (-(ratio_map[k] * total % src_total), k))
        for k in order[:rest]:
            scaled[k] += 1
    return {k: v for k, v in scaled.items() if v > 0}


def generate_paper(level: int, save: bool = True, scope: str = "level",
                   user: str | None = None, fce_db_path: str | None = None,
                   seed: int | None = None, preset: str = "standard",
                   ratios: dict | None = None, total: int | None = None,
                   source_mix: dict | None = None) -> dict:
    """生成一份考试卷。save=True 经 VocabPaperStore 落库（含 sections）。

    scope="level"：本级词池（exam_allowed 过滤）。
    scope="learned"：user 的全部已学词（进度里出现过即可，含未掌握），
    跨级别混合。卷号 MIX- 前缀，level 存 -1。去重只对先前的混合卷。

    preset / source_mix：选词来源配比。standard=纯词池；reinforce=60% 积压
    错词 + 40% 词池；recycle=全量积压错词。显式 source_mix 覆盖 preset；
    含 backlog_wrong>0 时必须给 user。积压错词 = 艾宾浩斯到期未攻克 ∪
    错过未掌握（并集去重，仅留词库有的词面）。

    seed 非 None 时同 seed 同卷（题目顺序、挖空位置、变形键选择全一致）。

    total：指定总题量（10-200，越界取边界）。给定时按比例缩放各题型
    配比（最大余数法）；不给则总题量 = 配比之和（默认 50）。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    scope = scope or "level"
    if scope not in ("level", "learned"):
        raise ValueError("scope 须为 level 或 learned")
    preset = preset or "standard"
    if preset not in PRESET_SOURCE_MIX:
        raise ValueError(f"preset 须为 {'/'.join(PRESET_SOURCE_MIX)}")
    mix = dict(source_mix) if source_mix else PRESET_SOURCE_MIX[preset]
    frac_backlog = 0.0
    if mix:
        try:
            frac_backlog = float(mix.get("backlog_wrong") or 0)
        except (TypeError, ValueError) as e:
            raise ValueError("source_mix.backlog_wrong 须为 0-1 数值") from e
        if not 0 <= frac_backlog <= 1:
            raise ValueError("source_mix.backlog_wrong 须在 0-1")
    user_clean = (user or "").strip()[:60]
    if frac_backlog > 0 and not user_clean:
        raise ValueError("选词含积压错词（backlog_wrong>0）时须指定 user")

    ratio_map = _normalize_ratios(ratios)
    if total is not None:
        try:
            total = int(total)
        except (TypeError, ValueError) as e:
            raise ValueError("total 须为整数") from e
        ratio_map = _scale_ratios(ratio_map, total)
    total = sum(ratio_map.values())
    lvl_out, prefix = ((-1, "MIX") if scope == "learned"
                       else (int(level), f"L{int(level)}"))

    backlog: list[dict] = []
    if frac_backlog > 0:
        backlog = _load_backlog_wrong(user_clean, fce_db_path)
    if scope == "level":
        pool = _load_words_rich(int(level))
    else:
        pool = _load_learned_words(user_clean, fce_db_path)

    backlog_n = len({w["word"] for w in backlog})
    if frac_backlog >= 1:
        # 全量积压回炉：不用词池补，不足直接报现有量
        if backlog_n < total:
            raise ValueError(
                f"积压错词不足 {total}（现有 {backlog_n}），无法出全错词回炉卷")
    else:
        combined_n = len({w["word"] for w in backlog} | {w["word"] for w in pool})
        if combined_n < total:
            if frac_backlog > 0:
                raise ValueError(
                    f"可用词不足 {total}（词池 {len(pool)} + 积压错词 {backlog_n}）")
            if scope == "learned":
                who = user_clean or "该学生"
                raise ValueError(f"{who} 已学词不足 {total}（现有 {combined_n}）")
            raise ValueError(f"L{level} 词量不足 {total}（现有 {combined_n}）")

    store = VocabPaperStore(fce_db_path)
    hist = store._hist_words(scope, lvl_out)
    picked = _select_words(total, backlog, pool, hist, frac_backlog, rng)
    overlap = sum(1 for w in picked if w["word"] in hist)
    if overlap > MAX_HISTORY_OVERLAP:
        raise ValueError(
            f"与历史重复 {overlap} 词超限（≤{MAX_HISTORY_OVERLAP}）")

    sections = _build_sections(picked, ratio_map, rng)

    now = datetime.now(_tz.utc)
    paper_id = (f"{prefix}-" + now.strftime("%Y%m%d%H%M%S")
                + f"-{rng.randrange(1000, 9999)}")
    paper = {
        "paper_id": paper_id,
        "level": lvl_out,
        "scope": scope,
        "user": user_clean if scope == "learned" else "",
        "created_at": now.isoformat(timespec="seconds"),
        "seed": int(seed) if seed is not None else None,
        "overlap_with_history": overlap,
        "config": {"total": total, "ratios": {k: ratio_map[k] for k in SECTION_ORDER},
                   "simple_max_level": SIMPLE_MAX_LEVEL, "preset": preset,
                   "source_mix": dict(mix) if mix else None},
        "words": [w["word"] for w in picked],
        "sections": sections,
    }
    if save:
        store.save_paper(paper)
    return paper


# ---- 渲染 ------------------------------------------------------------------ #

_CN_SECTION_NUMS = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")

_TYPE_HINTS = {"form": "写出指定形式", "spell": "在空格处填写字母",
               "ctx": "根据句意填空", "zh2en": "写出英文", "en2zh": "写出中文意思"}


def _section_label(idx: int) -> str:
    return _CN_SECTION_NUMS[idx] if idx < len(_CN_SECTION_NUMS) else str(idx + 1)


def _paper_sections(paper: dict) -> list[dict]:
    """paper → sections 列表（sections 键优先；旧版 zh2en/en2zh/spell 键兼容）。"""
    secs = paper.get("sections")
    if isinstance(secs, list) and secs:
        return [s for s in secs if isinstance(s, dict)]
    out = []
    for t in ("zh2en", "en2zh", "spell"):
        items = paper.get(t) or []
        if items:
            out.append({"type": t, "title": SECTION_TITLES[t], "items": list(items)})
    return out


def _render_item(qtype: str, i: int, it: dict, e) -> str:
    """单个题行：沿用 .q/.n/.pos/.zh/.en/.line/.blanked 版式类。"""
    n = f'<span class="n">{i}</span>'
    if qtype == "form":
        label = it.get("form_label") or FORM_CN.get(it.get("form_key") or "", "")
        return (f'<div class="q">{n}<span class="en">{e(it.get("word") or "")}</span>'
                f'<span class="pos">{e(it.get("pos_cn") or "")}</span>'
                f'<span class="zh">的{e(label)}</span>'
                f'<span class="line short"></span></div>')
    if qtype == "spell":
        return (f'<div class="q">{n}<span class="pos">{e(it.get("pos_cn") or "")}</span>'
                f'<span class="zh">{e(it.get("gloss") or "")}</span>'
                f'<span class="blanked">{e(it.get("blanked") or "")}</span></div>')
    if qtype == "ctx":
        return (f'<div class="q">{n}<span class="en">{e(it.get("sentence") or "")}</span>'
                f'<span class="zh">{e(it.get("zh") or it.get("gloss") or "")}</span>'
                f'<span class="line short"></span></div>')
    if qtype == "zh2en":
        return (f'<div class="q">{n}<span class="pos">{e(it.get("pos_cn") or "")}</span>'
                f'<span class="zh">{e(it.get("gloss") or "")}</span>'
                f'<span class="line"></span></div>')
    # en2zh 及未知类型兜底
    return (f'<div class="q">{n}<span class="en">{e(it.get("word") or "")}</span>'
            f'<span class="pos">{e(it.get("pos_cn") or "")}</span>'
            f'<span class="line short"></span></div>')


def _answer_text(qtype: str, it: dict) -> str:
    if qtype == "zh2en":
        pos = it.get("pos_cn") or ""
        return f'{it.get("word") or ""}（{pos}）' if pos else str(it.get("word") or "")
    if qtype == "en2zh":
        return str(it.get("gloss") or "")[:14]
    return str(it.get("answer") or it.get("spell_answer") or "")


def render_new_paper_html(paper: dict, title: str = "") -> str:
    """可打印考卷：sections 分区 + 答案页（独立成页可撕）。

    存什么渲染什么：sections 里每段一个标题块；旧版形态（zh2en/en2zh/
    spell 键）经 _paper_sections 兼容转换。
    """
    e = html.escape
    paper = paper or {}
    lvl = paper.get("level")
    pid = str(paper.get("paper_id") or "")
    if title:
        t = title
    elif lvl is not None and int(lvl) < 0:
        who = (paper.get("user") or "").strip()
        t = f"哈一词汇考试 · 已学全部混合卷（{who}）" if who else "哈一词汇考试 · 已学全部混合卷"
    else:
        t = f"哈一词汇考试 · L{lvl} 级"

    sections = _paper_sections(paper)
    total = sum(len(s.get("items") or []) for s in sections)
    per = f"{round(100 / total, 1):g}" if total else "0"
    per_txt = f"共 {total} 题 · 每题 {per} 分 · " if total else ""
    meta_line = (f"卷号 {e(pid)} · {per_txt}姓名__________ · 得分______")

    q_blocks: list[str] = []
    a_blocks: list[str] = []
    for idx, sec in enumerate(sections):
        qtype = str(sec.get("type") or "")
        tt = str(sec.get("title") or SECTION_TITLES.get(qtype, qtype or "题目"))
        items = sec.get("items") or []
        head = f"{_section_label(idx)}、{tt}"
        hint = _TYPE_HINTS.get(qtype, "")
        sub = f"（{len(items)} 题 · {hint}）" if hint else f"（{len(items)} 题）"
        q_blocks.append(
            f"<h2>{e(head)}{e(sub)}</h2>"
            + "".join(_render_item(qtype, i, it, e)
                      for i, it in enumerate(items, 1)))
        answers = "　".join(f"{i}.{e(_answer_text(qtype, it))}"
                            for i, it in enumerate(items, 1))
        a_blocks.append(
            f'<div class="ans-sec"><b>{e(head)}</b><p>{answers}</p></div>')

    return render_template(
        "new_paper.html",
        title_esc=e(t), pid_esc=e(pid), meta_line=meta_line,
        sections_html="".join(q_blocks), answers_html="".join(a_blocks),
    )


# ---- 模块级薄委托（兼容既有调用方/测试；内部转调 VocabPaperStore） ---------- #

def save_paper(paper: dict, fce_db_path: str | None = None) -> dict:
    return VocabPaperStore(fce_db_path).save_paper(paper)


def get_paper(paper_id: str, fce_db_path: str | None = None) -> dict | None:
    return VocabPaperStore(fce_db_path).get_paper(paper_id)


def delete_paper(paper_id: str, fce_db_path: str | None = None) -> bool:
    return VocabPaperStore(fce_db_path).delete_paper(paper_id)


def list_paper_history(level: int | None = None, limit: int = 50,
                       fce_db_path: str | None = None,
                       include_mix: bool = False) -> list[dict]:
    return VocabPaperStore(fce_db_path).list_history(
        level, limit=limit, include_mix=include_mix)

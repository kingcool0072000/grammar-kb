# -*- coding: utf-8 -*-
"""微练习（fce.db micro_question）离线出题管线：错题回捞 + 时态专项 + GLM 变形/词块库。

典型顺序：
    python3 scripts/gen_micro_bank.py report                  # 只读盘点
    python3 scripts/gen_micro_bank.py seed-wrong              # 错题 MCQ 回捞入库
    python3 scripts/gen_micro_bank.py seed-tense              # 时态讲次 MCQ 入库
    python3 scripts/gen_micro_bank.py gen-variants --dry-run  # 预览变形题 prompt
    python3 scripts/gen_micro_bank.py gen-variants            # 调 GLM 生成变形题（draft）
    python3 scripts/gen_micro_bank.py rewrite-explain --dry-run  # 预览四步解析改写计划
    python3 scripts/gen_micro_bank.py rewrite-explain           # 已有题解析批量改写为四步
    python3 scripts/gen_micro_bank.py gen-phrasal             # 短语动词库 → data/micro/*.json
    # （人工修订 data/micro/*.json 后）
    python3 scripts/gen_micro_bank.py import-bank --file data/micro/phrasal_verbs.json --category phrasal

依赖（并行开发中，文件头标注）：
- grammar_kb.micro.MicroStore：fce 库 micro_question 表的建表与幂等 upsert。
  签名 MicroStore(fce_db_path) / upsert_questions(rows) -> (inserted, skipped)，
  行字段 dict：category/topic/stem/options(list)/answer/explain/source/
  ref_lecture/ref_qnum/status。micro.py 尚未合入时，只读子命令照常可用，
  写库子命令会报错提示。
- GLM：scripts/glm_client.chat_prose / extract_json（AI 已退役出 grammar_kb 生产包，
  仅本离线脚本触网）；apiKey/model 读 library.db 的 ai_json 遗留列（历史教师配置）。
  所有 --dry-run 完全不触网（不取 key、不 import glm_client、不调 chat_prose）。
- 库路径一律走 grammar_kb.dbreg（$GRAMMAR_KB_* 环境变量 → 仓库根 data/）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# glm_client.py 与本脚本同目录（AI 已退役出 grammar_kb 生产包，仅离线脚本使用）；
# 真正 import 放在触网函数内，--dry-run 不依赖 glm_client 可导入
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from grammar_kb import dbreg  # noqa: E402
from grammar_kb.homework_admin import load_wrong_bank  # noqa: E402

try:
    # micro.py 由并行实例开发中；未合入时只读子命令不受影响
    from grammar_kb.micro import MicroStore  # noqa: E402
except ImportError:  # pragma: no cover
    MicroStore = None  # type: ignore[assignment,misc]

MICRO_DIR = ROOT / "data" / "micro"
LETTERS = "ABCD"
BATCH_SIZE = 10          # gen-phrasal / gen-idiom 每批词条数
GLM_TEMPERATURE = 0.6
GLM_SLEEP_SECONDS = 1.0  # 限速：两次 GLM 调用间隔
REWRITE_BATCH_SIZE = 5     # rewrite-explain 每批题数
REWRITE_TEMPERATURE = 0.3  # 改写要忠实题面，温度压低
FOUR_STEP_STEPS = ("①", "②", "③", "④")  # 四步解题法：结构/锚点/约束/答案
FOUR_STEP_MIN_LEN = 40                   # 四步解析最小长度（字符），防偷懒输出


# ---------------------------------------------------------------- 基础工具


def _readonly(path: str) -> sqlite3.Connection:
    """只读打开 sqlite（库不存在会直接报错，符合 report/统计场景）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _is_letter(value: object) -> bool:
    """MCQ 字母答案判定：strip 后单字符 A~D。"""
    v = str(value or "").strip().upper()
    return len(v) == 1 and v in LETTERS


def _is_four_step(explain: str) -> bool:
    """四步解题法解析判定：同时含 ①②③④ 四个标记字符，缺一不可。"""
    return all(step in explain for step in FOUR_STEP_STEPS)


def _json_loads(text: object, default):
    try:
        return json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _require_micro() -> "type":
    if MicroStore is None:
        raise SystemExit(
            "grammar_kb/micro.py 尚未合入（并行开发中），写库子命令暂不可用；"
            "report 等只读子命令不受影响。"
        )
    return MicroStore


# ---------------------------------------------------------------- 数据装配


def _load_homework(conn: sqlite3.Connection) -> dict:
    """homework_question → {(lecture_number, qnum): {stem, options, answer}}。

    MCQ 判定：is_cell=0 且 options_json 解析后 ≥2 项（与全仓口径一致）。
    """
    out: dict = {}
    rows = conn.execute(
        "SELECT lecture_number, qnum, stem, options_json, answer"
        " FROM homework_question WHERE is_cell = 0"
    ).fetchall()
    for ln, qnum, stem, options_json, answer in rows:
        options = _json_loads(options_json, [])
        if not isinstance(options, list) or len(options) < 2:
            continue
        out[(int(ln), int(qnum))] = {
            "stem": str(stem or "").strip(),
            "options": [str(o) for o in options],
            "answer": str(answer or "").strip(),
        }
    return out


def _load_wrong_pairs(conn: sqlite3.Connection, user: str | None = None) -> set:
    """exam_records.wrong（JSON 题号数组）展开去重 → {(lecture, qnum)}。"""
    if user:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(exam_records)")}
        if "user" in cols:
            rows = conn.execute(
                "SELECT lecture, wrong FROM exam_records WHERE user = ?", (user,)
            ).fetchall()
        else:  # 老库无 user 列：忽略过滤
            rows = conn.execute("SELECT lecture, wrong FROM exam_records").fetchall()
    else:
        rows = conn.execute("SELECT lecture, wrong FROM exam_records").fetchall()
    pairs: set = set()
    for lecture, wrong in rows:
        for qnum in _json_loads(wrong, []) or []:
            try:
                pairs.add((int(lecture), int(qnum)))
            except (TypeError, ValueError):
                continue
    return pairs


def _lecture_meta(conn: sqlite3.Connection) -> tuple:
    """lecture 表 → (titles: {number: 标题}, tense_numbers: [讲次号])。"""
    titles: dict = {}
    tense: list = []
    for row in conn.execute("SELECT number, title, category FROM lecture"):
        titles[int(row["number"])] = str(row["title"] or f"第{row['number']}讲")
        if "时态" in str(row["category"] or ""):
            tense.append(int(row["number"]))
    return titles, sorted(tense)


def _wrongbank_letter(wrongbank: dict, lecture: int, qnum: int) -> str | None:
    """wrongbank 里该题的答案；是 A~D 字母才返回（填空题文本答案不算）。"""
    entry = (wrongbank.get(str(lecture)) or {}).get(str(qnum)) or {}
    q = str(entry.get("q") or "").strip().upper()
    return q if _is_letter(q) else None


def _wrongbank_exp(wrongbank: dict, lecture: int, qnum: int) -> str:
    entry = (wrongbank.get(str(lecture)) or {}).get(str(qnum)) or {}
    return str(entry.get("exp") or "").strip()


def _micro_rows(conn: sqlite3.Connection) -> list:
    """micro_question 按 category×status 计数；表不存在返回 []。"""
    if not _table_exists(conn, "micro_question"):
        return []
    return conn.execute(
        "SELECT category, status, COUNT(*) AS n FROM micro_question"
        " GROUP BY category, status ORDER BY category, status"
    ).fetchall()


# ---------------------------------------------------------------- report


def cmd_report(_args: argparse.Namespace) -> int:
    """零写库：错题/可回捞/时态/micro_question 现状统计。"""
    grammar = _readonly(dbreg.grammar_db_path())
    exam = _readonly(dbreg.exam_db_path())
    fce = _readonly(dbreg.fce_db_path())
    wrongbank = load_wrong_bank()

    homework = _load_homework(grammar)
    wrong_all = _load_wrong_pairs(exam)
    titles, tense_numbers = _lecture_meta(grammar)

    hit_hw = {p for p in wrong_all if p in homework}
    hit_letter = {p for p in wrong_all if _wrongbank_letter(wrongbank, *p)}
    recoverable = hit_hw & hit_letter
    missing_answer = wrong_all - hit_letter
    missing_stem = {p for p in wrong_all if p not in homework or not homework[p]["stem"]}

    print("== 错题（exam_records 展开）==")
    per_user = {}
    cols = {r[1] for r in exam.execute("PRAGMA table_info(exam_records)")}
    if "user" in cols:
        for row in exam.execute(
            "SELECT user, COUNT(*) FROM exam_records GROUP BY user"
        ):
            per_user[row[0]] = row[1]
    print(f"  测验记录: {exam.execute('SELECT COUNT(*) FROM exam_records').fetchone()[0]} 场"
          f"（per user: {per_user or '-'}）")
    print(f"  展开去重错题 (讲次, 题号): {len(wrong_all)}")
    print(f"  其中 MCQ 可回捞（homework 命中 + wrongbank 字母答案）: {len(recoverable)}")
    print(f"  缺 MCQ 字母答案（含填空题，无法回捞为选择题）: {len(missing_answer)}")
    print(f"  缺题干（homework 无对应行或题干为空）: {len(missing_stem)}")

    tense_mcq = {
        p for p in homework
        if p[0] in tense_numbers and (
            _is_letter(homework[p]["answer"]) or _wrongbank_letter(wrongbank, *p)
        )
    } if tense_numbers else set()
    print("== 时态讲次（lecture.category 含「时态」）==")
    print(f"  讲次: {tense_numbers or '-'}")
    print(f"  MCQ 题量（有字母答案）: {len(tense_mcq)}")
    print(f"  其中在本生错题中: {len(tense_mcq & wrong_all)}")

    print(f"== micro_question 现状（{dbreg.fce_db_path()}）==")
    rows = _micro_rows(fce)
    if not rows:
        print("  表不存在或为空：全 0")
    total = 0
    for row in rows:
        print(f"  {row['category']:<10} {row['status']:<10} {row['n']}")
        total += row["n"]
    print(f"  合计: {total}")

    grammar.close()
    exam.close()
    fce.close()
    return 0


# ---------------------------------------------------------------- seed-wrong / seed-tense


def _build_seed_row(category: str, topic: str, stem: str, options: list,
                    answer: str, explain: str, lecture: int | None, qnum: int | None) -> dict:
    return {
        "category": category,
        "topic": topic,
        "stem": stem,
        "options": options,
        "answer": answer,
        "explain": explain,
        "source": "seed",
        "ref_lecture": lecture,
        "ref_qnum": qnum,
        "status": "published",
    }


def _upsert(rows: list, dry_run: bool, label: str) -> int:
    """写库公共口：打印目标与计划；dry-run 只打印样本。返回 0/非 0 退出码。"""
    fce_path = dbreg.fce_db_path()
    if dry_run:
        print(f"[dry-run] {label}：拟写入 {len(rows)} 行 → {fce_path}（未写）")
        if rows:
            sample = rows[0]
            print(f"  样本: [{sample['category']}] {sample['topic']} | "
                  f"{sample['stem'][:50]} | 答案 {sample['answer']} | "
                  f"选项 {sample['options'][:2]}...")
        return 0
    store = _require_micro()(fce_path)
    inserted, skipped = store.upsert_questions(rows)
    print(f"  upsert 完成：inserted={inserted} skipped={skipped} → {fce_path}")
    return 0


def cmd_seed_wrong(args: argparse.Namespace) -> int:
    """该生 exam_records.wrong → homework MCQ + wrongbank 答案 → category='wrong'。"""
    if not args.dry_run:
        _require_micro()  # 快速失败：micro.py 未合入时立刻报错，不做无谓扫描
    fce_path = dbreg.fce_db_path()
    print(f"将扫描 exam_records（user={args.user}）并把可回捞 MCQ 写入 {fce_path}"
          f"（category='wrong', source='seed', status='published'）"
          f"{' [dry-run，不写库]' if args.dry_run else ''}")
    grammar = _readonly(dbreg.grammar_db_path())
    exam = _readonly(dbreg.exam_db_path())
    wrongbank = load_wrong_bank()
    homework = _load_homework(grammar)
    titles, _tense = _lecture_meta(grammar)
    wrong = _load_wrong_pairs(exam, user=args.user)

    rows = []
    no_answer, no_stem = [], []
    for lecture, qnum in sorted(wrong):
        letter = _wrongbank_letter(wrongbank, lecture, qnum)
        hw = homework.get((lecture, qnum))
        if not letter:
            no_answer.append((lecture, qnum))
            continue
        if not hw or not hw["stem"]:
            no_stem.append((lecture, qnum))
            continue
        idx = LETTERS.index(letter)
        if idx >= len(hw["options"]):  # 字母答案越界，放弃该题
            no_stem.append((lecture, qnum))
            continue
        explain = _wrongbank_exp(wrongbank, lecture, qnum)
        if not explain:
            explain = f"正确答案 {letter}. {hw['options'][idx]}"
        rows.append(_build_seed_row(
            "wrong", titles.get(lecture, f"第{lecture}讲"), hw["stem"],
            hw["options"], letter, explain, lecture, qnum))

    print(f"  错题 {len(wrong)} → 可回捞 {len(rows)}"
          f"（缺字母答案 {len(no_answer)}，缺题干/选项越界 {len(no_stem)}）")
    if not rows:
        print("  没有可写入的行。")
        return 0
    ret = _upsert(rows, args.dry_run, "seed-wrong")
    if not args.dry_run:
        coverage = len(rows) / len(wrong) * 100 if wrong else 0
        print(f"  覆盖率：{len(rows)}/{len(wrong)} = {coverage:.1f}%")
    grammar.close()
    exam.close()
    return ret


def cmd_seed_tense(args: argparse.Namespace) -> int:
    """时态讲次全部 MCQ（有字母答案）→ category='tense'。"""
    if not args.dry_run:
        _require_micro()
    fce_path = dbreg.fce_db_path()
    print(f"将把时态讲次 MCQ 写入 {fce_path}"
          f"（category='tense', source='seed', status='published'）"
          f"{' [dry-run，不写库]' if args.dry_run else ''}")
    grammar = _readonly(dbreg.grammar_db_path())
    wrongbank = load_wrong_bank()
    homework = _load_homework(grammar)
    titles, tense_numbers = _lecture_meta(grammar)

    rows, skipped = [], 0
    for lecture, qnum in sorted(homework):
        if lecture not in tense_numbers:
            continue
        hw = homework[(lecture, qnum)]
        letter = _wrongbank_letter(wrongbank, lecture, qnum) or hw["answer"]
        if not _is_letter(letter):
            skipped += 1
            continue
        idx = LETTERS.index(letter)
        if idx >= len(hw["options"]):
            skipped += 1
            continue
        explain = _wrongbank_exp(wrongbank, lecture, qnum)
        if not explain:
            explain = f"正确答案 {letter}. {hw['options'][idx]}"
        rows.append(_build_seed_row(
            "tense", titles.get(lecture, f"第{lecture}讲"), hw["stem"],
            hw["options"], letter, explain, lecture, qnum))

    print(f"  时态讲次 {tense_numbers}：MCQ 入选 {len(rows)}，无字母答案/越界跳过 {skipped}")
    if not rows:
        print("  没有可写入的行。")
        return 0
    return _upsert(rows, args.dry_run, "seed-tense")


# ---------------------------------------------------------------- GLM 相关（小函数封装，dry-run 不触网）


def _ai_settings() -> dict:
    """取 ai 设置（apiKey/model）：只读打开 library.db 读 ai_json 遗留列。

    AI 退役后设置页不再写该列；历史教师配置的 key 仍供本离线脚本使用。
    只读打开（不构造 LibraryStore、不跑建表），dry-run 与实跑路径一致且零写库。
    """
    try:
        conn = _readonly(dbreg.library_db_path())
    except sqlite3.Error:
        return {}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(library_settings)")}
        if "ai_json" not in cols:  # 后续版本若删列，静默视为未配置
            return {}
        row = conn.execute(
            "SELECT ai_json FROM library_settings WHERE id = 1"
        ).fetchone()
        return _json_loads(row["ai_json"] if row else None, {}) or {}
    finally:
        conn.close()


def _glm_call(system_prompt: str, user_prompt: str, ai: dict,
              temperature: float = GLM_TEMPERATURE) -> str:
    """chat_prose 薄封装：统一 key/model/温度/异常文案。"""
    import glm_client  # noqa: PLC0415 触网入口，按需导入（scripts/ 同目录）
    api_key = str(ai.get("apiKey") or "")
    model = str(ai.get("model") or "glm-4-flash")
    if not api_key:
        raise glm_client.GlmError("library.db 未配置智谱 apiKey（ai_json 遗留列为空）")
    return glm_client.chat_prose(
        api_key, model, system_prompt, user_prompt,
        temperature=temperature, max_tokens=8192)


VARIANT_SYSTEM_PROMPT = """你是初中/FCE 英语语法老师，为错题生成一道「同考点变形题」。
只输出严格 JSON（不要 markdown 围栏、不要多余文字）：
{"stem": "英文题干", "options": ["A...", "B...", "C...", "D..."], "answer": "X", "explain": "四步解析", "topic": "考点中文短语"}
要求：
1. 同一考点、换一个场景/句子；正确答案考查的语法点不变。
2. 4 个选项均为英文；干扰项要合理、有迷惑性；4 个选项互不相同。
3. 正确答案的位置必须随机分布（A/B/C/D 打乱），不要总集中在同一个字母。
4. stem 用英文；topic 用中文短语（如「现在完成时」）。
5. explain 必须是「四步解题法」格式：四步各占一行，分别以 ①②③④ 开头、行间用 \\n 分隔。
   ① 结构：该句的句法架构（从句类型/句型/句式转换等；短语搭配类考点写句子架构即可）。
   ② 锚点：题干中触发考点的关键信号词/线索（时间词/连词/空格位置/介词/宾语为代词等），
   不要生搬不适用的时态术语。
   ③ 约束：语法约束推导（主语单复数/时态呼应/词形变化/搭配对象等）。
   ④ 答案：正确答案+为什么，必须带排除法——逐项简斥其余选项为何错。
四步解析样例（explain 请保持同样的格式与颗粒度）：
① 结构：since 引导时间状语从句 + 主句。
② 锚点：since + 过去动作 began → 公式① have/has done。
③ 约束：主语 our lives 复数 → have。
④ 答案：B. have changed（A 过去时与 since「延续至今」冲突；C has 三单错位；D 过去完成时缺过去锚点）。"""


_OPT_PREFIX_RE = re.compile(r"^[A-D][.、．]\s*")


def _validate_variant(obj: object) -> tuple:
    """校验 GLM 变形题：(ok, 题目dict或None, 失败原因)。"""
    if not isinstance(obj, dict):
        return False, None, "输出不是 JSON 对象"
    stem = str(obj.get("stem") or "").strip()
    options = obj.get("options")
    answer = str(obj.get("answer") or "").strip().upper()
    if not stem:
        return False, None, "stem 为空"
    if not isinstance(options, list) or len(options) != 4:
        return False, None, f"选项数不是 4（{len(options) if isinstance(options, list) else '非数组'}）"
    # GLM 常给选项自带 "A. " 字母前缀，而前端按下标生成字母，须剥掉防双重字母
    options = [_OPT_PREFIX_RE.sub("", str(o).strip()) for o in options]
    if not all(options):
        return False, None, "存在空选项"
    if len(set(options)) != 4:
        return False, None, "选项重复"
    if not _is_letter(answer):
        return False, None, f"answer 非法: {answer!r}"
    explain = str(obj.get("explain") or "").strip()
    if not _is_four_step(explain):
        return False, None, "explain 缺四步标记（须同时含 ①②③④）"
    return True, {
        "stem": stem,
        "options": options,
        "answer": answer,
        "explain": explain,
        "topic": str(obj.get("topic") or "").strip(),
    }, ""


def _load_mothers(conn: sqlite3.Connection, category: str, limit: int) -> list:
    """从 micro_question 取变形题母题（只读直查，不依赖 MicroStore 其余 API）。"""
    if not _table_exists(conn, "micro_question"):
        return []
    return conn.execute(
        "SELECT category, topic, stem, options_json, answer, explain,"
        " ref_lecture, ref_qnum FROM micro_question"
        " WHERE category = ? AND status IN ('published', 'draft')"
        " ORDER BY id LIMIT ?", (category, limit)
    ).fetchall()


def cmd_gen_variants(args: argparse.Namespace) -> int:
    """category='wrong' 的题逐题调 GLM 生成 1 道变形题（category='variant'，draft）。"""
    fce_path = dbreg.fce_db_path()
    print(f"将对 {fce_path} 中 category='wrong' 的题（前 {args.limit} 条）逐题调 GLM 生成变形题"
          f"（category='variant', source='ai', status='draft'）"
          f"{' [dry-run：只打印 prompt，不触网、不写库]' if args.dry_run else ''}")
    fce = _readonly(fce_path)
    mothers = _load_mothers(fce, "wrong", args.limit)
    fce.close()
    if not mothers:
        print("  没有母题：先运行 seed-wrong。")
        return 0

    ai = _ai_settings()
    if args.dry_run:
        m = mothers[0]
        print(f"  [dry-run] 模型: {ai.get('model') or '(未配置)'}；"
              f"将逐题调用 {len(mothers)} 次，温度 {GLM_TEMPERATURE}，限速 {GLM_SLEEP_SECONDS}s/题")
        print("  ---- system_prompt ----")
        print("  " + VARIANT_SYSTEM_PROMPT.replace("\n", "\n  "))
        print("  ---- user_prompt 样本（母题 1 条）----")
        sample = _variant_user_prompt({
            "stem": m["stem"],
            "options": _json_loads(m["options_json"], []) or [],
            "answer": m["answer"],
            "explain": m["explain"],
        })
        print("  " + sample.replace("\n", "\n  "))
        return 0

    inserted, failed = 0, 0
    answer_dist: dict = {}
    rows: list = []
    for i, m in enumerate(mothers, 1):
        mother = {
            "stem": m["stem"],
            "options": _json_loads(m["options_json"], []) or [],
            "answer": m["answer"],
            "explain": m["explain"],
        }
        try:
            text = _glm_call(VARIANT_SYSTEM_PROMPT, _variant_user_prompt(mother), ai)
            import glm_client  # noqa: PLC0415 触网入口，按需导入
            ok, variant, reason = _validate_variant(glm_client.extract_json(text))
            if not ok:
                raise glm_client.GlmError(f"输出校验失败: {reason}")
        except Exception as e:  # GlmError / 解析失败：记一笔续跑
            failed += 1
            print(f"  [{i}/{len(mothers)}] 第 {m['ref_lecture']}讲 {m['ref_qnum']}题 失败: {e}")
            time.sleep(GLM_SLEEP_SECONDS)
            continue
        rows.append({
            "category": "variant",
            "topic": variant["topic"] or m["topic"],
            "stem": variant["stem"],
            "options": variant["options"],
            "answer": variant["answer"],
            "explain": variant["explain"],
            "source": "ai",
            "ref_lecture": m["ref_lecture"],
            "ref_qnum": m["ref_qnum"],
            "status": "draft",
        })
        answer_dist[variant["answer"]] = answer_dist.get(variant["answer"], 0) + 1
        inserted += 1
        print(f"  [{i}/{len(mothers)}] ok: {variant['stem'][:60]}... 答案 {variant['answer']}")
        if i < len(mothers):
            time.sleep(GLM_SLEEP_SECONDS)

    ret = _upsert(rows, args.dry_run, "gen-variants")
    print(f"  生成 {inserted} / 失败 {failed}；答案分布 {dict(sorted(answer_dist.items()))}")
    return ret


def _variant_user_prompt(mother: dict) -> str:
    return (
        "母题如下，请生成一道同考点变形题：\n"
        f"题干：{mother['stem']}\n"
        f"选项：{json.dumps(mother['options'], ensure_ascii=False)}\n"
        f"正确答案：{mother['answer']}\n"
        f"解析：{mother['explain'] or '（无）'}\n"
        "只输出 JSON。"
    )


# ---------------------------------------------------------------- rewrite-explain（解析批量改写为四步）

REWRITE_SYSTEM_PROMPT = """你是初中/FCE 英语语法老师。把给定的英语选择题解析批量改写成「四步解题法」格式。
只输出严格 JSON 数组（不要 markdown 围栏、不要多余文字），每道题对应一个元素：
[{"id": <原题id 原样照抄>, "explain": "<四步解析>"}]
四步解析要求：四步各占一行，分别以 ①②③④ 开头、行间用 \\n 分隔。
① 结构：该句的句法架构（从句类型/句型/句式转换等）。
② 锚点：题干中触发考点的关键信号词/线索（时间词/连词/空格位置/介词/宾语为代词等）；
短语动词、习语搭配题写触发该搭配的线索即可，不要生搬时态术语。
③ 约束：语法约束推导（主语单复数/时态呼应/词形变化/搭配对象等）。
④ 答案：正确答案+为什么，必须带排除法——逐项简斥其余选项为何错，
形如「④ 答案：B. have written（A 三单错位；C 过去时与 since 冲突；D 进行时不能接完成义）」。
原解析是素材：可吸收其得分点，但必须重构成四步，不得照抄原文；不得改变题意与正确答案。
四步解析样例（改写时保持同样的格式与颗粒度）：
题干：Our lives ________ a lot since computers began to enter our homes.（正确答案 B. have changed）
① 结构：since 引导时间状语从句 + 主句。
② 锚点：since + 过去动作 began → 公式① have/has done。
③ 约束：主语 our lives 复数 → have。
④ 答案：B. have changed（A 过去时与 since「延续至今」冲突；C has 三单错位；D 过去完成时缺过去锚点）。"""


def _rewrite_user_prompt(items: list) -> str:
    """一批（≤5）待改写题 → user_prompt：题干/选项/正确答案/原解析都给到。"""
    blocks = []
    for it in items:
        blocks.append(
            f"id：{it['id']}\n"
            f"题干：{it['stem']}\n"
            f"选项：{json.dumps(it['options'], ensure_ascii=False)}\n"
            f"正确答案：{it['answer']}\n"
            f"原解析：{it['explain'] or '（无）'}"
        )
    return (
        f"共 {len(items)} 道题，把每道题的解析改写成四步解题法格式（id 原样照抄，逐题输出一个对象）：\n"
        + "\n----\n".join(blocks)
        + "\n只输出 JSON 数组。"
    )


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def _extract_json_array(text: str) -> list:
    """容错提取第一个完整 JSON 数组（glm_client.extract_json 只支持对象，这里补数组版）。"""
    s = (text or "").strip()
    s = _THINK_RE.sub("", s)
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
    start = s.find("[")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(s)):
            c = s[i]
            if escaped:
                escaped = False
                continue
            if c == "\\":
                if in_str:
                    escaped = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    candidate = s[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list):
                            return parsed
                        break  # 该起点不是数组，换下一个 '['
                    except ValueError:
                        break  # 该起点不完整，换下一个 '['
        start = s.find("[", start + 1)
    return []


def _validate_rewrites(obj: object, batch: list) -> tuple:
    """校验一批改写结果：(成功 {id: explain}, 失败条数)。

    每条要求：id 匹配批次内且不重复；explain 同时含 ①②③④ 且长度 ≥ FOUR_STEP_MIN_LEN。
    缺条/重复/不合格都计入失败，交由调用方跳过。
    """
    if not isinstance(obj, list):
        return {}, len(batch)
    ids = {int(it["id"]) for it in batch}
    ok: dict = {}
    for item in obj:
        if not isinstance(item, dict):
            continue
        try:
            rid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if rid not in ids or rid in ok:
            continue
        explain = str(item.get("explain") or "").strip()
        if not _is_four_step(explain) or len(explain) < FOUR_STEP_MIN_LEN:
            continue
        ok[rid] = explain
    return ok, len(batch) - len(ok)


def _load_rewrite_rows(conn: sqlite3.Connection, category: str, limit: int) -> list:
    """某类别解析尚非四步的行（已是四步的含①、跳过）。"""
    if not _table_exists(conn, "micro_question"):
        return []
    sql = ("SELECT id, topic, stem, options_json, answer, explain FROM micro_question"
           " WHERE category = ? AND explain NOT LIKE '%①%' ORDER BY id")
    params: list = [category]
    if limit and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def cmd_rewrite_explain(args: argparse.Namespace) -> int:
    """把已有题的解析批量改写为四步解题法：每批 5 题调一次 GLM，只 UPDATE explain 字段。"""
    categories = list(dict.fromkeys(
        c.strip() for c in str(args.categories or "").split(",") if c.strip()))
    if not categories:
        print("--categories 解析后为空，无事可做。", file=sys.stderr)
        return 1
    fce_path = dbreg.fce_db_path()
    print(f"将把 {fce_path} micro_question 中类别 {categories} 内、解析尚非四步格式的题"
          f"批量改写为四步解题法（每批 {REWRITE_BATCH_SIZE} 题、温度 {REWRITE_TEMPERATURE}、"
          f"限速 {GLM_SLEEP_SECONDS}s/批，只改 explain 字段，不碰 stem/options/answer）"
          f"{' [dry-run：只统计与打印样例，不触网、不写库]' if args.dry_run else ''}")

    if args.dry_run:
        grand = 0
        for category in categories:
            fce = _readonly(fce_path)
            rows = _load_rewrite_rows(fce, category, args.limit)
            fce.close()
            batches = (len(rows) + REWRITE_BATCH_SIZE - 1) // REWRITE_BATCH_SIZE
            print(f"  [{category}] 待改写 {len(rows)} 条（{batches} 批）")
            if rows:
                r = rows[0]
                print(f"    样例 id={r['id']} [{r['topic']}] {r['stem'][:50]}")
                print(f"    现解析: {str(r['explain'] or '')[:70]}")
            grand += len(rows)
        print(f"  合计待改写 {grand} 条（dry-run 未触网、未写库）")
        return 0

    rows_by_cat: dict = {}
    for category in categories:
        fce = _readonly(fce_path)
        rows_by_cat[category] = _load_rewrite_rows(fce, category, args.limit)
        fce.close()
    if not any(rows_by_cat.values()):
        print("  所选类别都没有待改写的题，无事可做。")
        return 0

    ai = _ai_settings()
    conn = sqlite3.connect(f"file:{fce_path}?mode=rw", uri=True)  # 库不存在时报错，不建空库
    try:
        grand_ok = grand_failed = 0
        for category in categories:
            rows = rows_by_cat[category]
            total = len(rows)
            batches = (total + REWRITE_BATCH_SIZE - 1) // REWRITE_BATCH_SIZE
            ok = failed = 0
            for b in range(batches):
                batch = rows[b * REWRITE_BATCH_SIZE:(b + 1) * REWRITE_BATCH_SIZE]
                items = [{
                    "id": r["id"],
                    "stem": r["stem"],
                    "options": _json_loads(r["options_json"], []) or [],
                    "answer": r["answer"],
                    "explain": r["explain"],
                } for r in batch]
                try:
                    text = _glm_call(REWRITE_SYSTEM_PROMPT, _rewrite_user_prompt(items), ai,
                                     temperature=REWRITE_TEMPERATURE)
                    rewritten, batch_failed = _validate_rewrites(_extract_json_array(text), items)
                except Exception as e:  # GLM 网络失败/超时：整批记失败，续跑
                    rewritten, batch_failed = {}, len(batch)
                    print(f"  [{category}] 批 {b + 1}/{batches} 调用失败: {e}")
                else:
                    for rid, explain in rewritten.items():  # 只改 explain 字段
                        conn.execute("UPDATE micro_question SET explain = ? WHERE id = ?",
                                     (explain, rid))
                    conn.commit()
                    print(f"  [{category}] 批 {b + 1}/{batches}: ok {len(rewritten)}"
                          f" / failed {batch_failed}")
                ok += len(rewritten)
                failed += batch_failed
                if b < batches - 1:
                    time.sleep(GLM_SLEEP_SECONDS)
            print(f"  [{category}] 汇总：processed {ok + failed} / ok {ok} / failed {failed}")
            grand_ok += ok
            grand_failed += failed
        print(f"  总计：ok {grand_ok} / failed {grand_failed}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------- gen-phrasal / gen-idiom


WORD_BANK_SPECS = {
    "phrasal": {
        "label": "短语动词（phrasal verbs）",
        "file": MICRO_DIR / "phrasal_verbs.json",
        "field": "phrase",
    },
    "idiom": {
        "label": "习语/固定搭配（idioms）",
        "file": MICRO_DIR / "idioms.json",
        "field": "phrase",
    },
}

WORD_SYSTEM_PROMPT = """你是初中~FCE 水平的英语词块老师，为中学生生成常用的{label}练习。
只输出严格 JSON（不要 markdown 围栏、不要多余文字）：
{{"entries": [{{"phrase": "...", "meaning_zh": "中文释义", "example_en": "英文例句", "example_zh": "例句中文", "level": "初中|中考|FCE"}}], "questions": [{{"topic": "<phrase>", "stem": "英文题干", "options": ["A...", "B...", "C...", "D..."], "answer": "X", "explain": "中文解析"}}]}}
要求：
1. 每批 {batch} 个词条，覆盖 初中→FCE 难度带，偏常考，避开生僻/俚语项。
2. 每条 entry 恰好配 1 道 questions 题（topic = 该词条 phrase 原文）。
3. 题目考「含义/用法辨析」，两种题型轮换：给含空格（____）的英文例句从 4 个短语里选正确的一个；或给英文短语从 4 个中文释义里选正确含义。
4. 干扰项取本批其他短语（或其释义），要合理。
5. stem/options 英文，explain 中文一句话；answer 是单个字母 A/B/C/D，位置打乱分布。"""


def _load_bank_file(path: Path) -> dict:
    if not path.exists():
        return {"entries": [], "questions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"entries": [], "questions": []}
    data.setdefault("entries", [])
    data.setdefault("questions", [])
    return data


def _validate_word_batch(obj: object, batch: int) -> tuple:
    """校验词条批次：(entries, questions, 失败原因)。失败返回空列表+原因。"""
    if not isinstance(obj, dict):
        return [], [], "输出不是 JSON 对象"
    entries = obj.get("entries")
    questions = obj.get("questions")
    if not isinstance(entries, list) or not entries:
        return [], [], "entries 为空"
    if not isinstance(questions, list) or not questions:
        return [], [], "questions 为空"
    clean_entries = []
    phrases = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        phrase = str(e.get("phrase") or "").strip()
        if not phrase:
            continue
        clean_entries.append({
            "phrase": phrase,
            "meaning_zh": str(e.get("meaning_zh") or "").strip(),
            "example_en": str(e.get("example_en") or "").strip(),
            "example_zh": str(e.get("example_zh") or "").strip(),
            "level": str(e.get("level") or "中考").strip(),
        })
        phrases.append(phrase)
    if not clean_entries:
        return [], [], "entries 全部无效"
    clean_questions = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        topic = str(q.get("topic") or "").strip()
        stem = str(q.get("stem") or "").strip()
        options = q.get("options")
        answer = str(q.get("answer") or "").strip().upper()
        if not topic or not stem or not isinstance(options, list) or len(options) != 4:
            continue
        options = [_OPT_PREFIX_RE.sub("", str(o).strip()) for o in options]
        if not all(options) or not _is_letter(answer):
            continue
        clean_questions.append({
            "topic": topic,
            "stem": stem,
            "options": options,
            "answer": answer,
            "explain": str(q.get("explain") or "").strip(),
        })
    if not clean_questions:
        return [], [], "questions 全部无效"
    return clean_entries[:batch], clean_questions, ""


def cmd_gen_word_bank(args: argparse.Namespace, kind: str) -> int:
    spec = WORD_BANK_SPECS[kind]
    out_path = spec["file"]
    data = _load_bank_file(out_path)
    have = {e.get("phrase") for e in data["entries"]}
    existing_phrases = [str(p) for p in have if p]
    remain = max(0, args.n - len(have))
    print(f"将生成{spec['label']}（{kind}）→ {out_path}"
          f"（已有 {len(have)} 条，目标 {args.n}，还需 {remain}，每批 {BATCH_SIZE} 条）"
          f"{' [dry-run：不触网、不落盘]' if args.dry_run else ''}")
    if remain == 0:
        print("  已达标，无需生成。")
        return 0

    ai = _ai_settings()
    system_prompt = WORD_SYSTEM_PROMPT.format(label=spec["label"], batch=BATCH_SIZE)
    if args.dry_run:
        print(f"  [dry-run] 模型: {ai.get('model') or '(未配置)'}；"
              f"将分批调用 ceil({remain}/{BATCH_SIZE}) 次，温度 {GLM_TEMPERATURE}")
        print("  ---- system_prompt ----")
        print("  " + system_prompt.replace("\n", "\n  "))
        print("  ---- user_prompt 样本（第 1 批）----")
        print("  " + _word_user_prompt(1, BATCH_SIZE, existing_phrases).replace("\n", "\n  "))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    batches = (remain + BATCH_SIZE - 1) // BATCH_SIZE
    added, failed = 0, 0
    for b in range(1, batches + 1):
        try:
            text = _glm_call(system_prompt, _word_user_prompt(b, BATCH_SIZE, existing_phrases), ai)
            import glm_client  # noqa: PLC0415 触网入口，按需导入
            entries, questions, reason = _validate_word_batch(
                glm_client.extract_json(text), BATCH_SIZE)
            if not entries:
                raise glm_client.GlmError(f"批次校验失败: {reason}")
        except Exception as e:
            failed += 1
            print(f"  [batch {b}/{batches}] 失败: {e}")
            continue
        new_phrases = {e["phrase"] for e in entries} - have
        if not new_phrases:
            print(f"  [batch {b}/{batches}] 全部与已有重复，跳过")
        data["entries"].extend(e for e in entries if e["phrase"] in new_phrases)
        data["questions"].extend(
            q for q in questions if q["topic"] in new_phrases or q["topic"] in have)
        have |= new_phrases
        existing_phrases.extend(sorted(new_phrases))
        added += len(new_phrases)
        # 每批落盘一次（断点续生：中断后重跑按 phrase 去重跳过）
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [batch {b}/{batches}] +{len(new_phrases)}，累计 {len(have)}/{args.n}，已写盘")
        if len(have) >= args.n:
            break
        time.sleep(GLM_SLEEP_SECONDS)
    print(f"  完成：新增 {added}，失败批次 {failed}，共 {len(have)} 条 → {out_path}")
    return 0


def _word_user_prompt(batch: int, size: int, existing: list) -> str:
    avoid = existing[-200:] if existing else []
    return (
        f"第 {batch} 批，请生成 {size} 个词条+配题。\n"
        + (f"以下词条已存在，务必避开：{json.dumps(avoid, ensure_ascii=False)}\n" if avoid else "")
        + "只输出 JSON。"
    )


# ---------------------------------------------------------------- import-bank / publish-drafts


def cmd_import_bank(args: argparse.Namespace) -> int:
    """data/micro/*.json → micro_question（category=指定值，published）。"""
    if not args.dry_run:
        _require_micro()
    src = Path(args.file)
    if not src.is_absolute():
        src = ROOT / src
    fce_path = dbreg.fce_db_path()
    print(f"将读取 {src} 并写入 {fce_path}"
          f"（category='{args.category}', source='seed', status='published', topic=phrase）"
          f"{' [dry-run，不写库]' if args.dry_run else ''}")
    if not src.exists():
        print(f"  文件不存在：{src}", file=sys.stderr)
        return 1
    data = _load_bank_file(src)
    questions = [q for q in data["questions"] if isinstance(q, dict)]
    if not questions:
        print("  文件里没有 questions，无事可做。")
        return 0
    rows = []
    for q in questions:
        topic = str(q.get("topic") or "").strip()
        stem = str(q.get("stem") or "").strip()
        options = [_OPT_PREFIX_RE.sub("", str(o).strip())
                   for o in (q.get("options") or []) if str(o).strip()]
        answer = str(q.get("answer") or "").strip().upper()
        if not topic or not stem or len(options) != 4 or not _is_letter(answer):
            continue
        rows.append(_build_seed_row(
            args.category, topic, stem, options, answer,
            str(q.get("explain") or "").strip(), None, None))
    print(f"  有效题 {len(rows)}/{len(questions)}（无效行丢弃）")
    if not rows:
        print("  没有有效题可写。")
        return 0
    return _upsert(rows, args.dry_run, "import-bank")


def cmd_publish_drafts(args: argparse.Namespace) -> int:
    """variant draft 全部/前 N 条置 published（直接 UPDATE，应急用）。"""
    fce_path = dbreg.fce_db_path()
    scope = f"前 {args.limit} 条" if args.limit else "全部"
    print(f"将把 {fce_path} micro_question 中 category='variant' 且 status='draft' 的"
          f"{scope}置为 published{' [dry-run，不写库]' if args.dry_run else ''}")
    conn = sqlite3.connect(f"file:{fce_path}?mode=rw", uri=True)  # 库不存在时报错，不建空库
    try:
        if not _table_exists(conn, "micro_question"):
            print("  micro_question 表不存在（先跑 seed-* / import-bank）。", file=sys.stderr)
            return 1
        cond = "category = 'variant' AND status = 'draft'"
        pending = conn.execute(f"SELECT COUNT(*) FROM micro_question WHERE {cond}").fetchone()[0]
        if not pending:
            print("  没有 draft 的 variant，无事可做。")
            return 0
        limit = args.limit if args.limit else pending
        if args.dry_run:
            print(f"  [dry-run] draft {pending} 条，将发布 {min(pending, limit)} 条（未写）")
            return 0
        cur = conn.execute(
            "UPDATE micro_question SET status = 'published' WHERE id IN ("
            f"  SELECT id FROM micro_question WHERE {cond} ORDER BY id LIMIT ?"
            ")", (limit,))
        conn.commit()
        print(f"  已发布 {cur.rowcount} 条（原有 draft {pending}）")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------- CLI


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_micro_bank.py",
        description="微练习离线出题管线：report → seed-wrong → seed-tense → gen-variants"
                    " → gen-phrasal/gen-idiom → import-bank（详见模块 docstring）")
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("report", help="只读统计：错题量/可回捞 MCQ/时态题库/micro_question 现状（零写库）")
    p.set_defaults(func=cmd_report)

    p = subs.add_parser("seed-wrong", help="该生 exam_records 错题中的 MCQ 回捞入库（category='wrong'）")
    p.add_argument("--user", default="malin", help="学生名（exam_records.user，默认 malin）")
    p.add_argument("--dry-run", action="store_true", help="只统计与打印，不写库")
    p.set_defaults(func=cmd_seed_wrong)

    p = subs.add_parser("seed-tense", help="时态讲次（lecture.category 含「时态」）MCQ 入库（category='tense'）")
    p.add_argument("--dry-run", action="store_true", help="只统计与打印，不写库")
    p.set_defaults(func=cmd_seed_tense)

    p = subs.add_parser("gen-variants", help="对 category='wrong' 的题调 GLM 生成变形题（category='variant'，draft）")
    p.add_argument("--limit", type=int, default=50, help="最多处理的母题数（默认 50）")
    p.add_argument("--dry-run", action="store_true", help="只打印 prompt 与计划，不触网、不写库")
    p.set_defaults(func=cmd_gen_variants)

    p = subs.add_parser("rewrite-explain",
                        help="把已有题解析批量改写为四步解题法格式（只改 explain；GLM 每批 5 题）")
    p.add_argument("--categories", default="wrong,phrasal,idiom",
                   help="逗号分隔的类别（默认 wrong,phrasal,idiom）")
    p.add_argument("--limit", type=int, default=0, help="每类最多改写条数；0=不限（默认 0）")
    p.add_argument("--dry-run", action="store_true", help="只统计与打印将改写的样例，不触网、不写库")
    p.set_defaults(func=cmd_rewrite_explain)

    p = subs.add_parser("gen-phrasal",
                        help="GLM 分批生成短语动词词条+题目 → data/micro/phrasal_verbs.json（断点续生）")
    p.add_argument("--n", type=int, default=100, help="目标词条数（默认 100）")
    p.add_argument("--dry-run", action="store_true", help="只打印 prompt 与计划，不触网、不落盘")
    p.set_defaults(func=lambda a, k="phrasal": cmd_gen_word_bank(a, k))

    p = subs.add_parser("gen-idiom",
                        help="GLM 分批生成习语词条+题目 → data/micro/idioms.json（断点续生）")
    p.add_argument("--n", type=int, default=80, help="目标词条数（默认 80）")
    p.add_argument("--dry-run", action="store_true", help="只打印 prompt 与计划，不触网、不落盘")
    p.set_defaults(func=lambda a, k="idiom": cmd_gen_word_bank(a, k))

    p = subs.add_parser("import-bank", help="把 data/micro/*.json 里的 questions 导入 micro_question（published）")
    p.add_argument("--file", required=True, help="JSON 文件路径（默认口径 data/micro/phrasal_verbs.json）")
    p.add_argument("--category", required=True, help="写入的 category（如 phrasal / idiom）")
    p.add_argument("--dry-run", action="store_true", help="只统计与打印，不写库")
    p.set_defaults(func=cmd_import_bank)

    p = subs.add_parser("publish-drafts", help="把 variant draft（全部/前 N 条）置为 published（应急用）")
    p.add_argument("--limit", type=int, default=0, help="最多发布的条数；0=全部（默认 0）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    p.set_defaults(func=cmd_publish_drafts)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except SystemExit as e:  # _require_micro 等主动失败
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        raise
    except sqlite3.Error as e:
        print(f"数据库错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 主流程兜底，带退出码
        print(f"失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

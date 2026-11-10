"""微练习：学生每日 3 道选择题（fce.db 同库）。

数据模型（契约与其他模块共享，勿单方面更改）：
- ``micro_question``：题库。category 六类 wrong/variant/tense/phrasal/idiom/
  grammar；seed 题带 ref_lecture/ref_qnum 溯源到作业卷 (讲次, 题号)。
- ``micro_daily``：每人每天一行（PRIMARY KEY user+date），qids_json 记当日
  3 题、answered_json 记 {qid: choice}、done 全部答完置 1。plan 层用裸 SQL
  读 ``SELECT done, answered_json FROM micro_daily WHERE user=? AND date=?``
  做打卡信号——列名与语义按此契约保持稳定。
- ``micro_log``：逐题作答流水（每题首次作答落一条，重复提交不重复落），
  抽题的「翻正/最久没考/近 3 天答对降权」都从它读。

去重口径：(ref_lecture, ref_qnum, stem) 相同视为同题；ref 为 NULL 时按
stem 全等。upsert 幂等跳过，批量脚本可重复跑。

抽题（:meth:`MicroStore.today` 首次调用生成，确定性随机
``random.Random(f"{user}:{day}")`` 保证同日同题）：
1. 错题未翻正最优先，并集「category='wrong' 且该题最近一次作答非
   correct」∪「近 3 天答错且此后未答对过」（micro_log 读出，当日错题
   次日自动回炉）取 1；
2. 记忆库保底：phrasal/idiom/tense 里「最久没考」的类随机取 1（空类跳过）；
3. 其余位置按 topic 最久没考（NULL 最先）→ 本周 focus_kps 倾斜 → 随机，
   近 3 天答对过的题降权不排除。
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import dbreg
from .clock import today_cn
from .store_base import clamp_limit, connect, ensure_schema, loads_dict, loads_json

# 题目类目（契约枚举；入口处校验）
CATEGORIES = frozenset({"wrong", "variant", "tense", "phrasal", "idiom", "grammar"})
# 记忆库保底类：每天至少见一题（phrasal/idiom 由脚本导入，seed 只有 tense）
MEMORY_CATEGORIES = ("phrasal", "idiom", "tense")
# status 枚举
STATUSES = frozenset({"draft", "published"})
# 每日题数（契约固定 3）
DAILY_N = 3
# 近 N 天答对过的题降权（北京日历，含当天）
RECENT_CORRECT_DAYS = 3
# 近 N 天答错过的题并入优先级①回炉（北京日历，含当天；当日错次日见）
RECENT_WRONG_DAYS = 3
# update_question 白名单字段
EDITABLE_FIELDS = frozenset({"category", "topic", "stem", "options", "answer",
                             "explain", "status"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS micro_question (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,              -- wrong/variant/tense/phrasal/idiom/grammar
  topic TEXT NOT NULL DEFAULT '',
  stem TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '[]',
  answer TEXT NOT NULL,                -- 大写字母 A-D
  explain TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'seed', -- seed/ai/manual
  ref_lecture INTEGER, ref_qnum INTEGER,
  status TEXT NOT NULL DEFAULT 'published', -- draft/published
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS micro_daily (
  user TEXT NOT NULL, date TEXT NOT NULL,
  qids_json TEXT NOT NULL DEFAULT '[]',
  answered_json TEXT NOT NULL DEFAULT '{}',
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user, date));
CREATE TABLE IF NOT EXISTS micro_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT NOT NULL, qid INTEGER NOT NULL, date TEXT NOT NULL,
  choice TEXT NOT NULL, is_correct INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_micro_log_user_date ON micro_log(user, date);
CREATE INDEX IF NOT EXISTS idx_micro_log_qid ON micro_log(qid);
CREATE INDEX IF NOT EXISTS idx_micro_question_status_cat
  ON micro_question(status, category);
"""


def _ans_letter(raw) -> Optional[str]:
    """答案归一：A-D 单字母（容忍小写/首尾空白）→ 大写；其余 None。"""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if len(s) == 1 and s in "ABCD" else None


def _norm_choice(raw) -> str:
    """作答归一：去空白、取大写首字母（非 A-D 也照存，判分自然为错）。"""
    return str(raw or "").strip().upper()[:1]


def _opt_int(v) -> Optional[int]:
    """宽松转 int；空/坏值 → None（ref 双双为 NULL 时按 stem 去重）。"""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_options(raw) -> list:
    """options_json 解析；坏 JSON/非数组 → []。"""
    try:
        v = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return v if isinstance(v, list) else []


def _canonical_row(row: dict) -> Optional[dict]:
    """行字段校验与归一；不合法返回 None（批量导入跳过该行）。"""
    stem = str(row.get("stem") or "").strip()
    answer = _ans_letter(row.get("answer"))
    category = str(row.get("category") or "").strip()
    options = row.get("options")
    if options is None:
        options = []
    if not stem or answer is None or category not in CATEGORIES \
            or not isinstance(options, list):
        return None
    status = str(row.get("status") or "published").strip()
    return {
        "category": category,
        "topic": str(row.get("topic") or "").strip()[:120],
        "stem": stem[:2000],
        "options": options,
        "answer": answer,
        "explain": str(row.get("explain") or "").strip()[:2000],
        "source": str(row.get("source") or "manual").strip()[:16] or "manual",
        "ref_lecture": _opt_int(row.get("ref_lecture")),
        "ref_qnum": _opt_int(row.get("ref_qnum")),
        "status": status if status in STATUSES else "published",
    }


def _dedup_key(row: dict) -> tuple:
    """同题键：(ref_lecture, ref_qnum, stem)；ref 任一为空 → (None, None, stem)。"""
    ref_l, ref_q = _opt_int(row.get("ref_lecture")), _opt_int(row.get("ref_qnum"))
    if ref_l is None or ref_q is None:
        ref_l = ref_q = None
    return (ref_l, ref_q, str(row.get("stem") or "").strip())


class MicroStore:
    """micro_question / micro_daily / micro_log 读写与每日抽题。"""

    def __init__(self, fce_db_path, *, exam_db_path: Optional[str] = None,
                 grammar_db_path: Optional[str] = None):
        # fce.db 显式传入（服务端 create_app 与脚本同参）；grammar.db（作业
        # 题源）与 exam.db（已学范围/错题）按 PlanStore 惯例可选显式覆盖，
        # 缺省走 dbreg 环境变量口径。
        self.db_path = fce_db_path or dbreg.fce_db_path()
        self.exam_db_path = exam_db_path or dbreg.exam_db_path()
        self.grammar_db_path = grammar_db_path or dbreg.grammar_db_path()
        ensure_schema(self.db_path, SCHEMA)

    def _connect(self):
        return connect(self.db_path)

    # ---- 日期与行读取 ----

    @staticmethod
    def _valid_day(day: str) -> str:
        d = str(day or "").strip()
        try:
            return date.fromisoformat(d).isoformat()
        except ValueError as e:
            raise ValueError(f"日期格式须为 YYYY-MM-DD：{day!r}") from e

    def _daily_row(self, conn, user: str, day: str):
        return conn.execute(
            "SELECT * FROM micro_daily WHERE user = ? AND date = ?",
            (user, day),
        ).fetchone()

    # ---- 学生端：今日 3 题 ----

    def today(self, user: str, day: str) -> dict:
        """当日练习。首次调用抽 3 题落 micro_daily，之后幂等直接返回。

        返回 {"date", "questions": [{id,category,topic,stem,options}],
        "answered": {qid: choice}, "done"}。options 不含答案。
        """
        user = (user or "").strip()[:60]
        day = self._valid_day(day)
        with self._connect() as conn:
            row = self._daily_row(conn, user, day)
        if row is None:
            self._sync_pool(user)
            qids = self._pick_daily(user, day)
            if qids:
                # 池空不落行：当天晚些出了新成绩/新题，下次调用还能补上
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO micro_daily (user, date, qids_json)"
                        " VALUES (?,?,?)",
                        (user, day, json.dumps(qids)),
                    )
        else:
            qids = loads_json(row["qids_json"], [])
        with self._connect() as conn:
            row = self._daily_row(conn, user, day)
            answered_raw = loads_dict(row["answered_json"]) if row else {}
            qmap: dict[int, dict] = {}
            if qids:
                marks = ",".join("?" * len(qids))
                for r in conn.execute(
                    f"SELECT id, category, topic, stem, options_json"
                    f" FROM micro_question WHERE id IN ({marks})", qids,
                ):
                    qmap[r["id"]] = {
                        "id": r["id"], "category": r["category"],
                        "topic": r["topic"], "stem": r["stem"],
                        "options": _parse_options(r["options_json"]),
                    }
        answered = {}
        for k, v in (answered_raw or {}).items():
            try:
                answered[int(k)] = v
            except (TypeError, ValueError):
                continue
        return {
            "date": day,
            "questions": [qmap[q] for q in qids if q in qmap],
            "answered": answered,
            "done": bool(row["done"]) if row else False,
        }

    def wrong_open_qids(self, user: str, day: str) -> list[int]:
        """明日回炉题集合（闭环卡 next_queue 与 _pick_daily 优先级① 同源）：

        category='wrong' ∪ 近 RECENT_WRONG_DAYS 天答错过，且最近一次作答
        非正确。返回按 id 升序的稳定列表（rng.choice 语义不受影响）。
        """
        try:
            floor = (date.fromisoformat(day)
                     - timedelta(days=RECENT_WRONG_DAYS - 1)).isoformat()
            with self._connect() as conn:
                pool: dict[int, str] = {r[0]: r[1] for r in conn.execute(
                    "SELECT id, category FROM micro_question"
                    " WHERE status = 'published'")}
                if not pool:
                    return []
                last: dict[int, int] = {}
                recent_wrong: set[int] = set()
                for r in conn.execute(
                    "SELECT qid, is_correct, date FROM micro_log"
                    " WHERE user = ? ORDER BY id", (user,),
                ):
                    last[r["qid"]] = r["is_correct"]
                    if not r["is_correct"] and r["date"] >= floor:
                        recent_wrong.add(r["qid"])
            return sorted(q for q, cat in pool.items()
                          if last.get(q, 0) != 1
                          and (cat == "wrong" or q in recent_wrong))
        except (sqlite3.Error, ValueError):
            return []

    def _pick_daily(self, user: str, day: str) -> list[int]:
        """抽题（见模块 docstring 优先级）。返回 qid 有序列表（≤3）。"""
        rng = random.Random(f"{user}:{day}")
        with self._connect() as conn:
            pool = [dict(r) for r in conn.execute(
                "SELECT id, category, topic, ref_lecture FROM micro_question"
                " WHERE status = 'published' ORDER BY id",
            )]
            # 该生逐题最近答对日（近 N 天答对降权用；按 id 升序覆盖 → 最近一条）
            last_correct: dict[int, str] = {}
            for r in conn.execute(
                "SELECT qid, is_correct, date FROM micro_log"
                " WHERE user = ? ORDER BY id", (user,),
            ):
                if r["is_correct"]:
                    prev = last_correct.get(r["qid"])
                    if prev is None or r["date"] > prev:
                        last_correct[r["qid"]] = r["date"]
            # 类目/专题最近被考日（今天窗口按练习日 day 比，与 log.date 同口径）
            cat_last: dict[str, str] = {}
            topic_last: dict[str, str] = {}
            for r in conn.execute(
                "SELECT l.date, q.category, q.topic FROM micro_log l"
                " JOIN micro_question q ON q.id = l.qid WHERE l.user = ?",
                (user,),
            ):
                cat_last[r["category"]] = max(cat_last.get(r["category"], ""),
                                              r["date"])
                topic_last[r["topic"]] = max(topic_last.get(r["topic"], ""),
                                             r["date"])
            # 本周 focus 讲次（plan_weeks 最近一周，表缺失容错）
            focus: set[int] = set()
            try:
                wk = conn.execute(
                    "SELECT tasks FROM plan_weeks WHERE user = ? AND week_start <= ?"
                    " ORDER BY week_start DESC LIMIT 1", (user, day),
                ).fetchone()
                if wk:
                    tasks = json.loads(wk["tasks"] or "{}")
                    focus = {_opt_int(x) for x in (tasks.get("focus_kps") or [])
                             if _opt_int(x) is not None}
            except (sqlite3.Error, ValueError, TypeError):
                focus = set()

        picked: list[int] = []

        # 1) 错题未翻正最优先（与闭环卡明日回炉同一集合，见 wrong_open_qids）
        wrong_open = self.wrong_open_qids(user, day)
        if wrong_open:
            picked.append(rng.choice(wrong_open))

        # 2) 记忆库保底：phrasal/idiom/tense 里最久没考的类随机取 1（空类跳过）
        mem_cats = [c for c in MEMORY_CATEGORIES
                    if any(q["category"] == c for q in pool)]
        if mem_cats:
            mem_cats.sort(key=lambda c: (0, "") if not cat_last.get(c)
                          else (1, cat_last[c]))
            cands = sorted(q["id"] for q in pool
                           if q["category"] == mem_cats[0]
                           and q["id"] not in picked)
            if cands:
                picked.append(rng.choice(cands))

        # 3) 覆盖轮转填满剩余位置：topic 最久没考（NULL 最先）→ focus 倾斜
        #    → 近 3 天答对降权 → 确定性随机
        if len(picked) < DAILY_N:
            recent_floor = (date.fromisoformat(day)
                            - timedelta(days=RECENT_CORRECT_DAYS - 1)).isoformat()

            def _rank(q: dict) -> tuple:
                tl = topic_last.get(q["topic"] or "")
                lc = last_correct.get(q["id"])
                return (
                    0 if not tl else 1,        # 从未被考过的 topic 最先
                    tl or "9999-12-31",        # 更久没考的先
                    0 if q["ref_lecture"] in focus else 1,  # 本周 focus 加权
                    1 if lc and lc >= recent_floor else 0,  # 近 3 天答对降权
                    rng.random(),
                )

            cands = sorted((q for q in pool if q["id"] not in picked), key=_rank)
            for q in cands:
                if len(picked) >= DAILY_N:
                    break
                picked.append(q["id"])
        return picked

    # ---- 学生端：提交判分 ----

    def submit(self, user: str, day: str, answers: list[dict]) -> dict:
        """服务端判分。返回 {"results", "done", "correct_n"}。

        - choice 归一（去空白、大写首字母）后与 answer 严格比对；
        - 每题首次作答落 micro_log，重复提交只更新 answered_json 不重复落；
        - 当日题全部有作答 → micro_daily.done = 1（一经置 1 不回退）。
        """
        user = (user or "").strip()[:60]
        day = self._valid_day(day)
        with self._connect() as conn:
            row = self._daily_row(conn, user, day)
        if row is None:
            # 当天还没拉过题：当日现场生成（历史日期没有记录则拒绝）
            if day != today_cn().isoformat():
                raise ValueError(f"{day} 没有微练习记录，请先获取当日题目")
            self.today(user, day)
            with self._connect() as conn:
                row = self._daily_row(conn, user, day)
            if row is None:
                raise ValueError("今日题池为空，无法提交")
        qids = loads_json(row["qids_json"], [])
        answered = {str(k): v for k, v in loads_dict(row["answered_json"]).items()}
        qmap = {}
        if qids:
            with self._connect() as conn:
                marks = ",".join("?" * len(qids))
                for r in conn.execute(
                    f"SELECT id, answer, explain, topic, category"
                    f" FROM micro_question WHERE id IN ({marks})", qids,
                ):
                    qmap[r["id"]] = r
        logged = set(answered)  # 快照：本次提交内也不重复落 log
        today_iso = today_cn().isoformat()
        results: list[dict] = []
        fresh_logs: list[tuple] = []
        for a in answers or []:
            try:
                qid = int(a.get("id"))
            except (TypeError, ValueError):
                continue
            q = qmap.get(qid)
            if q is None:
                continue  # 不在当日题集内的 id 直接忽略
            choice = _norm_choice(a.get("choice"))
            ok = 1 if choice == q["answer"] else 0
            results.append({
                "id": qid, "choice": choice, "answer": q["answer"],
                "correct": bool(ok), "explain": q["explain"],
                "topic": q["topic"], "category": q["category"],
            })
            answered[str(qid)] = choice
            if str(qid) not in logged:
                logged.add(str(qid))
                fresh_logs.append((user, qid, today_iso, choice, ok))
        done = bool(qids) and all(str(q) in answered for q in qids)
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO micro_log (user, qid, date, choice, is_correct)"
                " VALUES (?,?,?,?,?)", fresh_logs,
            )
            conn.execute(
                "UPDATE micro_daily SET answered_json = ?,"
                " done = CASE WHEN ? = 1 THEN 1 ELSE done END"
                " WHERE user = ? AND date = ?",
                (json.dumps(answered, ensure_ascii=False), 1 if done else 0,
                 user, day),
            )
        return {
            "results": results,
            "done": done,
            "correct_n": sum(1 for r in results if r["correct"]),
        }

    def day_status(self, user: str, day: str) -> dict:
        """当日完成状态 {"done", "n", "total"}；表缺失/无行容错零值。"""
        user = (user or "").strip()[:60]
        day = self._valid_day(day)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT done, qids_json, answered_json FROM micro_daily"
                    " WHERE user = ? AND date = ?", (user, day),
                ).fetchone()
        except sqlite3.OperationalError:  # 表不存在（plan 层裸 SQL 同款容错）
            row = None
        if row is None:
            return {"done": False, "n": 0, "total": 0}
        try:
            total = len(json.loads(row["qids_json"] or "[]"))
            n = len(json.loads(row["answered_json"] or "{}"))
        except (ValueError, TypeError):
            total = n = 0
        return {"done": bool(row["done"]), "n": n, "total": total}

    # ---- 池同步（today 前置，幂等轻量） ----

    def _sync_pool(self, user: str) -> None:
        """把已学范围的作业卷 MCQ 入池（幂等），再做该生错题覆盖。"""
        last = self._max_lecture(user)
        if last < 2:
            return  # 无成绩记录 → 空池
        lectures = self._lecture_meta(2, last)
        homework = self._homework_mcq(2, last)
        from . import homework_admin  # 惰性导入：测试可替换 load_wrong_bank

        bank = homework_admin.load_wrong_bank()
        rows: list[dict] = []
        for lec in range(2, last + 1):
            meta = lectures.get(lec)
            if meta is None:
                continue
            title, cat = meta
            category = "tense" if "时态" in cat else "grammar"
            lecture_bank = bank.get(str(lec)) or {}
            for q in homework.get(lec, []):
                entry = lecture_bank.get(str(q["qnum"])) or {}
                answer = _ans_letter(entry.get("q"))
                if answer is None:
                    continue  # wrongbank 无 A-D 答案 → 不入池（不阻塞）
                rows.append({
                    "category": category, "topic": title,
                    "stem": q["stem"], "options": q["options"],
                    "answer": answer, "explain": str(entry.get("exp") or ""),
                    "source": "seed", "status": "published",
                    "ref_lecture": lec, "ref_qnum": q["qnum"],
                })
        if rows:
            self.upsert_questions(rows)
        self._apply_wrong_overrides(user)

    def _max_lecture(self, user: str) -> int:
        """该生已学到的最大讲号（exam_records；库缺失/无记录 → 0）。"""
        if not Path(self.exam_db_path).exists():
            return 0
        try:
            with dbreg.connect(self.exam_db_path, readonly=True) as conn:
                row = conn.execute(
                    "SELECT MAX(lecture) m FROM exam_records WHERE user = ?",
                    (user,),
                ).fetchone()
                return int(row["m"] or 0)
        except sqlite3.Error:
            return 0

    def _exam_wrong(self, user: str) -> set[tuple[int, int]]:
        """该生全部历史错题 (lecture, qnum) 集合。"""
        pairs: set[tuple[int, int]] = set()
        if not Path(self.exam_db_path).exists():
            return pairs
        try:
            with dbreg.connect(self.exam_db_path, readonly=True) as conn:
                rows = conn.execute(
                    "SELECT lecture, wrong FROM exam_records WHERE user = ?",
                    (user,),
                ).fetchall()
        except sqlite3.Error:
            return pairs
        for r in rows:
            wrong = loads_json(r["wrong"], [])
            if not isinstance(wrong, list):
                continue
            for qn in wrong:
                q = _opt_int(qn)
                if q is not None:
                    pairs.add((int(r["lecture"]), q))
        return pairs

    def _lecture_meta(self, lo: int, hi: int) -> dict[int, tuple[str, str]]:
        """讲号 → (标题, category)。grammar.db 缺失返回空。"""
        if not Path(self.grammar_db_path).exists():
            return {}
        try:
            with dbreg.connect(self.grammar_db_path, readonly=True) as conn:
                rows = conn.execute(
                    "SELECT number, title, category FROM lecture"
                    " WHERE number BETWEEN ? AND ?", (lo, hi),
                ).fetchall()
        except sqlite3.Error:
            return {}
        return {r["number"]: (r["title"] or "", r["category"] or "") for r in rows}

    def _homework_mcq(self, lo: int, hi: int) -> dict[int, list[dict]]:
        """已学范围内作业卷 MCQ：is_cell=0 且选项 ≥2 项，按讲次分组。"""
        if not Path(self.grammar_db_path).exists():
            return {}
        try:
            with dbreg.connect(self.grammar_db_path, readonly=True) as conn:
                rows = conn.execute(
                    "SELECT lecture_number, qnum, stem, options_json"
                    " FROM homework_question"
                    " WHERE is_cell = 0 AND lecture_number BETWEEN ? AND ?"
                    " ORDER BY lecture_number, qnum", (lo, hi),
                ).fetchall()
        except sqlite3.Error:
            return {}
        out: dict[int, list[dict]] = {}
        for r in rows:
            options = _parse_options(r["options_json"])
            if len(options) < 2:
                continue  # 非 MCQ（填空/表格）
            out.setdefault(r["lecture_number"], []).append({
                "qnum": r["qnum"],
                "stem": (r["stem"] or "").strip(),
                "options": options,
            })
        return out

    def _apply_wrong_overrides(self, user: str) -> None:
        """该生历史错题若已在池 → 覆盖 category='wrong'（优先级最高）。"""
        pairs = self._exam_wrong(user)
        if not pairs:
            return
        with self._connect() as conn:
            # 只覆盖 seed 题：教师手动/脚本导入的题不被成绩数据翻类目
            conn.executemany(
                "UPDATE micro_question SET category = 'wrong'"
                " WHERE source = 'seed' AND ref_lecture = ? AND ref_qnum = ?"
                " AND category != 'wrong'",
                sorted(pairs),
            )

    # ---- 题库管理 ----

    def add_question(self, *, category, stem, options, answer, topic="",
                     explain="", source="manual", ref_lecture=None,
                     ref_qnum=None, status="draft") -> int:
        """新增一题（教师手动加题默认 draft），返回新 id。"""
        canon = _canonical_row({
            "category": category, "topic": topic, "stem": stem,
            "options": options, "answer": answer, "explain": explain,
            "source": source, "ref_lecture": ref_lecture,
            "ref_qnum": ref_qnum, "status": status,
        })
        if canon is None:
            raise ValueError("题目字段不合法：category 须为六类之一、"
                             "stem 非空、answer 须为 A-D 单字母")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO micro_question (category, topic, stem,"
                " options_json, answer, explain, source, ref_lecture,"
                " ref_qnum, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (canon["category"], canon["topic"], canon["stem"],
                 json.dumps(canon["options"], ensure_ascii=False),
                 canon["answer"], canon["explain"], canon["source"],
                 canon["ref_lecture"], canon["ref_qnum"], canon["status"]),
            )
            return int(cur.lastrowid)

    def list_questions(self, status=None, category=None, limit=500) -> list[dict]:
        """题库列表（管理端）。status/category 可选过滤，id 倒序。"""
        sql = ("SELECT id, category, topic, stem, options_json, answer,"
               " explain, source, ref_lecture, ref_qnum, status, created_at"
               " FROM micro_question WHERE 1=1")
        args: list = []
        if status:
            sql += " AND status = ?"
            args.append(str(status))
        if category:
            sql += " AND category = ?"
            args.append(str(category))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(clamp_limit(limit, 500, 2000))
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [{
            "id": r["id"], "category": r["category"], "topic": r["topic"],
            "stem": r["stem"], "options": _parse_options(r["options_json"]),
            "answer": r["answer"], "explain": r["explain"],
            "source": r["source"], "ref_lecture": r["ref_lecture"],
            "ref_qnum": r["ref_qnum"], "status": r["status"],
            "created_at": r["created_at"],
        } for r in rows]

    def update_question(self, qid: int, fields: dict) -> None:
        """白名单字段部分更新；非法值抛 ValueError。"""
        unknown = set(fields or {}) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"不可编辑的字段：{', '.join(sorted(unknown))}")
        sets: list[str] = []
        args: list = []
        if "category" in fields:
            c = str(fields["category"] or "").strip()
            if c not in CATEGORIES:
                raise ValueError(f"category 须为 {'/'.join(sorted(CATEGORIES))}")
            sets.append("category = ?")
            args.append(c)
        if "topic" in fields:
            sets.append("topic = ?")
            args.append(str(fields["topic"] or "").strip()[:120])
        if "stem" in fields:
            s = str(fields["stem"] or "").strip()
            if not s:
                raise ValueError("stem 不能为空")
            sets.append("stem = ?")
            args.append(s[:2000])
        if "options" in fields:
            o = fields["options"]
            if o is None:
                o = []
            if not isinstance(o, list):
                raise ValueError("options 须为列表")
            sets.append("options_json = ?")
            args.append(json.dumps(o, ensure_ascii=False))
        if "answer" in fields:
            a = _ans_letter(fields["answer"])
            if a is None:
                raise ValueError("answer 须为 A-D 单字母")
            sets.append("answer = ?")
            args.append(a)
        if "explain" in fields:
            sets.append("explain = ?")
            args.append(str(fields["explain"] or "").strip()[:2000])
        if "status" in fields:
            s = str(fields["status"] or "").strip()
            if s not in STATUSES:
                raise ValueError("status 须为 draft/published")
            sets.append("status = ?")
            args.append(s)
        if not sets:
            return
        args.append(int(qid))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE micro_question SET {', '.join(sets)} WHERE id = ?",
                args,
            )

    def delete_question(self, qid: int) -> None:
        """删题（作答流水保留：历史统计按 join 自然收敛）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM micro_question WHERE id = ?", (int(qid),))

    def upsert_questions(self, rows: list[dict]) -> tuple[int, int]:
        """批量幂等导入：同题键已存在 → 跳过。返回 (inserted, skipped)。"""
        inserted = skipped = 0
        canon_rows: list[dict] = []
        for row in rows or []:
            canon = _canonical_row(row)
            if canon is None:
                skipped += 1  # 坏行不计入，不阻塞整批
                continue
            canon_rows.append(canon)
        if not canon_rows:
            return 0, skipped
        with self._connect() as conn:
            existing = {
                _dedup_key({"ref_lecture": r["ref_lecture"],
                            "ref_qnum": r["ref_qnum"], "stem": r["stem"]})
                for r in conn.execute(
                    "SELECT ref_lecture, ref_qnum, stem FROM micro_question",
                )
            }
            for canon in canon_rows:
                key = _dedup_key(canon)
                if key in existing:
                    skipped += 1
                    continue
                existing.add(key)  # 批内同题也去重
                conn.execute(
                    "INSERT INTO micro_question (category, topic, stem,"
                    " options_json, answer, explain, source, ref_lecture,"
                    " ref_qnum, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (canon["category"], canon["topic"], canon["stem"],
                     json.dumps(canon["options"], ensure_ascii=False),
                     canon["answer"], canon["explain"], canon["source"],
                     canon["ref_lecture"], canon["ref_qnum"], canon["status"]),
                )
                inserted += 1
        return inserted, skipped

    def stats(self) -> dict:
        """管理端统计：类目 × 状态题数 + 类目 × 作答次数/答对次数。"""
        with self._connect() as conn:
            by_category: dict[str, dict] = {}
            for r in conn.execute(
                "SELECT category, status, COUNT(*) n FROM micro_question"
                " GROUP BY category, status",
            ):
                d = by_category.setdefault(
                    r["category"], {"published": 0, "draft": 0})
                d[r["status"] or "published"] = r["n"]
            log: dict[str, dict] = {}
            for r in conn.execute(
                "SELECT q.category c, COUNT(*) asked, SUM(l.is_correct) ok"
                " FROM micro_log l JOIN micro_question q ON q.id = l.qid"
                " GROUP BY q.category",
            ):
                log[r["c"]] = {"asked": r["asked"],
                               "correct": int(r["ok"] or 0)}
        return {"by_category": by_category, "log": log}

#!/usr/bin/env python3
"""一次性回填（2026-11 精简）：部署新代码前，把被删掉的懒迁移负责的存量数据补齐。

新代码删除了三处「读时补救」的懒迁移（recite 别名折叠 / recite 错词回填 /
vocab_paper 旧卷还原）和 plan videos 旧键读回退，改由本脚本对生产库跑一次：

  (a) recite 别名折叠 —— 移植 recite._fold_alias_progress/_fold_table/
      _rewrite_session_words：派生词/拼写变体上的背词进度并到词光词头
      （读 data/vocab_alias.json 的 aliases，词表没变则跳过）；
  (b) recite 复习回填 —— 移植 recite._backfill_review：历史会话错过但尚无
      recite_review 行的词按最近答错日补进艾宾浩斯周期。懒迁移首次只看
      最近 500 条，这里扫每个用户的全部历史，比原口径更彻底；
  (c) vocab_paper 旧卷 structure 回填 —— 移植 vocab_paper._legacy_config +
      _sections_from_wordlist：structure 为空的历史卷行按 20 中译英 /
      20 英译中 / 末段拼写的位置口径还原整卷，写回 structure 与 config 列
      （新代码取卷只认 structure 列，不再现场还原）；
  (d) plan videos 旧字段兜底 —— plan_weeks.tasks 里仍有平铺 videos 的周
      转成 video_units 一组 mode=daily（与 planEditor 装载/保存迁移同格式：
      去重保序、旧键删除），新排程读法与转换前一致。

每步幂等：跑一遍打印处理行数，紧接着再跑一遍应全为 0。
用法：
    uv run python scripts/oneoff_2026-11_legacy_backfill.py /path/to/fce.db
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grammar_kb import dbreg  # noqa: E402
from grammar_kb import vocab_paper as vp  # noqa: E402
from grammar_kb.atlas_vocab import canonical_word, load_alias_file  # noqa: E402
from grammar_kb.clock import today_cn  # noqa: E402
from grammar_kb.recite import EB_STAGES, ReciteStore, _add_days, _cn_day_of  # noqa: E402


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _calendar_today() -> str:
    """艾宾浩斯「今天」：北京日历（与 recite 模块同一口径）。"""
    return today_cn().isoformat()


# ---- (a) recite 别名折叠（移植自 recite.py，行为逐行一致） ------------------ #

def _fold_table(conn: sqlite3.Connection, table: str,
                targets: dict[str, str], *, progress: bool) -> int:
    """把表里别名词的行折到词头名下（词头不存在则改名，存在则合并后删行）。

    返回处理的别名行数；幂等：折完后表里不再有别名词，重跑为 0。
    """
    if table not in _tables(conn):
        return 0
    moved = 0
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    for row in rows:
        alias = row["word"]
        canon = targets.get(alias)
        if not canon or canon == alias:
            continue
        parent = conn.execute(
            f"SELECT * FROM {table} WHERE user = ? AND word = ?",
            (row["user"], canon),
        ).fetchone()
        if parent is None:
            conn.execute(
                f"UPDATE {table} SET word = ? WHERE user = ? AND word = ?",
                (canon, row["user"], alias),
            )
            moved += 1
            continue
        if progress:
            firsts = [v for v in (parent["first_seen"], row["first_seen"]) if v]
            lasts = [v for v in (parent["last_seen"], row["last_seen"]) if v]
            mastered_vals = [v for v in (parent["mastered_at"], row["mastered_at"]) if v]
            conn.execute(
                "UPDATE recite_word_progress SET right = ?, wrong = ?, "
                "mastered_at = ?, first_seen = ?, last_seen = ? "
                "WHERE user = ? AND word = ?",
                (
                    parent["right"] + row["right"],
                    parent["wrong"] + row["wrong"],
                    min(mastered_vals) if mastered_vals else None,
                    min(firsts) if firsts else None,
                    max(lasts) if lasts else None,
                    row["user"], canon,
                ),
            )
        else:
            conquered = parent["conquered_at"]
            stage = parent["stage"]
            nxt = parent["next_review"]
            if not conquered and row["next_review"]:
                if not nxt or row["next_review"] < nxt:
                    nxt = row["next_review"]
                    stage = row["stage"]
            conn.execute(
                "UPDATE recite_review SET wrong_count = ?, stage = ?, next_review = ? "
                "WHERE user = ? AND word = ?",
                (
                    parent["wrong_count"] + row["wrong_count"],
                    stage, nxt, row["user"], canon,
                ),
            )
        conn.execute(
            f"DELETE FROM {table} WHERE user = ? AND word = ?",
            (row["user"], alias),
        )
        moved += 1
    return moved


def _rewrite_session_words(conn: sqlite3.Connection,
                           targets: dict[str, str]) -> int:
    """历史会话 wrong_words 里的别名词改写为词头。返回改写的会话数。"""
    if "recite_sessions" not in _tables(conn):
        return 0
    changed_n = 0
    rows = conn.execute("SELECT id, wrong_words FROM recite_sessions").fetchall()
    for row in rows:
        try:
            words = json.loads(row["wrong_words"] or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(words, list):
            continue
        changed = False
        rewritten = []
        for word in words:
            text = str(word).strip().lower()
            canon = targets.get(text, text)
            if canon != text:
                changed = True
            if canon:
                rewritten.append(canon)
        if changed:
            conn.execute(
                "UPDATE recite_sessions SET wrong_words = ? WHERE id = ?",
                (json.dumps(rewritten, ensure_ascii=False), row["id"]),
            )
            changed_n += 1
    return changed_n


def fold_alias_progress(conn: sqlite3.Connection, aliases: dict) -> str:
    """折叠别名进度并记别名表版本。返回人读的处理摘要。

    与 recite._fold_alias_progress 一致：版本号（sha256 前 16 位）已记过
    就不再扫——所有写入路径（submit/enter_review/merge）本来就把词折成
    词头，版本没变时表里不会有新别名词。
    """
    if not aliases:
        return "别名表为空，跳过"
    revision = hashlib.sha256(
        json.dumps(aliases, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    conn.execute(
        "CREATE TABLE IF NOT EXISTS recite_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = conn.execute(
        "SELECT value FROM recite_meta WHERE key = 'atlas_alias_revision'"
    ).fetchone()
    if row and row[0] == revision:
        return f"别名表未变（revision={revision}），跳过"
    targets = {alias: canonical_word(alias, aliases) for alias in aliases}
    n_prog = _fold_table(conn, "recite_word_progress", targets, progress=True)
    n_rev = _fold_table(conn, "recite_review", targets, progress=False)
    n_sess = _rewrite_session_words(conn, targets)
    conn.execute(
        "INSERT INTO recite_meta(key, value) VALUES('atlas_alias_revision', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (revision,),
    )
    return (f"进度行折叠 {n_prog}，周期行折叠 {n_rev}，"
            f"会话改写 {n_sess}（revision={revision}）")


# ---- (b) recite 复习回填（移植自 recite._backfill_review） ------------------ #

def backfill_review(conn: sqlite3.Connection, user: str,
                    levels_fn) -> int:
    """某用户历史会话错过但尚无 recite_review 行的词，按最近答错日回填。

    口径与懒迁移逐行一致（stage=0、next_review=最近错日+1、INSERT OR
    IGNORE 幂等）；差异只有一条：这里扫全部历史会话，懒迁移首次只看
    最近 500 条。最后推进 recite_meta 回填水位（回滚安全 + 万一回滚到
    旧代码，懒迁移也不会重扫）。
    """
    rows = conn.execute(
        "SELECT id, wrong_words, created_at FROM recite_sessions"
        " WHERE user = ? ORDER BY id ASC",
        (user,),
    ).fetchall()
    if not rows:
        return 0
    today = _calendar_today()
    wrong_at: dict[str, str] = {}
    counts: dict[str, int] = {}
    for r in reversed(rows):  # 由新到旧：setdefault 拿到最近答错时间
        try:
            ws = json.loads(r["wrong_words"] or "[]")
        except (ValueError, TypeError):
            ws = []
        for w in ws:
            w = str(w).strip().lower()[:60]
            if not w:
                continue
            counts[w] = counts.get(w, 0) + 1
            wrong_at.setdefault(w, r["created_at"] or "")
    inserted = 0
    if counts:
        have = {r[0] for r in conn.execute(
            "SELECT word FROM recite_review WHERE user = ?", (user,)
        ).fetchall()}
        missing = [w for w in counts if w not in have]
        levels = levels_fn(missing) if missing else {}
        for w in missing:
            last_day = _cn_day_of(wrong_at.get(w) or "") or today
            # OR IGNORE：并发同读时另一连接可能已插同一词，幂等不炸
            cur = conn.execute(
                "INSERT OR IGNORE INTO recite_review (user, word, level,"
                " wrong_count, stage, first_wrong_at, next_review, last_review_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user, w, levels.get(w), counts[w], 0, last_day,
                 _add_days(last_day, EB_STAGES[0]), last_day),
            )
            inserted += cur.rowcount if cur.rowcount > 0 else 0
    # 水位最后写：上面任何一步抛错，整个事务回滚，指针不动（不丢数据）
    max_ts = max((r["created_at"] or "" for r in rows), default="")
    conn.executemany(
        "INSERT INTO recite_meta(key, value) VALUES(?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(f"backfill_max_id:{user}", str(rows[-1]["id"])),
         (f"backfill_max_ts:{user}", max_ts)],
    )
    return inserted


# ---- (c) vocab_paper 旧卷 structure 回填（移植自 vocab_paper.py） ---------- #

def _legacy_config(ws: list) -> dict:
    """旧版卷的配置回填（位置还原口径：20 中译英 / 20 英译中 / 末段拼写）。"""
    n = len(ws)
    n_zh = min(20, n)
    n_en = min(20, max(0, n - 20))
    n_sp = max(0, n - 40)
    return {"total": n,
            "ratios": {"form": 0, "spell": n_sp, "ctx": 0,
                       "zh2en": n_zh, "en2zh": n_en},
            "simple_max_level": vp.SIMPLE_MAX_LEVEL, "preset": "standard",
            "source_mix": None}


def _sections_from_wordlist(ws: list, level, paper_id: str) -> list[dict]:
    """旧版卷（只存词序）→ sections：前 20 中译英、次 20 英译中、末 10 拼写。

    词条从词库回填（历史卷可完整重建；词性一并带上）；拼写挖空用
    paper_id 作随机种子，同一卷多次取回挖空位置一致。
    词库相关 helper 直接复用 vocab_paper 模块的现役实现（不在本脚本
    复制一份，避免口径漂移；这些 helper 新代码仍在用）。
    """
    if not ws:
        return []
    if level is not None and int(level) >= 0:
        rich = {w["word"]: w for w in vp._load_words_rich(int(level))}
    else:
        rich = vp._load_words_by_names([w for w in ws if w])

    def _entry(w: str, use_clean: bool) -> dict:
        r = rich.get(w) or {}
        gloss = (r.get("gloss_clean") if use_clean else r.get("gloss")) \
            or r.get("gloss", "")
        return {"word": w, "pos_cn": r.get("pos_cn", ""), "gloss": gloss}

    zh2en = []
    for w in ws[:20]:
        item = _entry(w, True)
        item["gloss"] = vp._short_senses(item.get("gloss") or "")
        zh2en.append(item)
    en2zh = [_entry(w, False) for w in ws[20:40]]
    rng = random.Random(paper_id)
    spell = []
    for w in ws[40:]:
        blanked, answer = vp._spell_blank(w, rng)
        spell.append({**_entry(w, True), "blanked": blanked, "answer": answer})
    out = []
    if zh2en:
        out.append({"type": "zh2en", "title": vp.SECTION_TITLES["zh2en"],
                    "items": zh2en})
    if en2zh:
        out.append({"type": "en2zh", "title": vp.SECTION_TITLES["en2zh"],
                    "items": en2zh})
    if spell:
        out.append({"type": "spell", "title": vp.SECTION_TITLES["spell"],
                    "items": spell})
    return out


def backfill_paper_structure(conn: sqlite3.Connection) -> tuple[int, int]:
    """structure 为空的历史卷行按位置口径整卷写回。返回 (回填, 跳过)。

    整卷形态与旧 get_paper 现场还原的输出逐键一致（scope/user/seed 按
    列值或级别兜底），新代码 get_paper 读 structure 即所得。
    """
    if "vocab_paper_history" not in _tables(conn):
        return 0, 0  # 无表（全新库）：无事可做
    rows = conn.execute(
        "SELECT paper_id, level, words, created_at, scope, user, seed"
        " FROM vocab_paper_history WHERE structure IS NULL OR structure = ''"
    ).fetchall()
    filled = skipped = 0
    for row in rows:
        try:
            ws = json.loads(row["words"] or "[]")
        except (ValueError, TypeError):
            ws = []
        if not isinstance(ws, list) or not ws:
            skipped += 1  # 连词序都没有：无从还原，留着（列表页仍可见）
            continue
        config = _legacy_config(ws)
        paper = {
            "paper_id": row["paper_id"],
            "level": row["level"],
            "words": ws,
            "created_at": row["created_at"],
            "config": config,
            "sections": _sections_from_wordlist(ws, row["level"], row["paper_id"]),
            "scope": row["scope"] or ("learned" if int(row["level"] or 0) < 0
                                      else "level"),
            "user": row["user"] or "",
            "seed": row["seed"],
            "overlap_with_history": 0,
        }
        conn.execute(
            "UPDATE vocab_paper_history SET structure = ?, config = ?"
            " WHERE paper_id = ?",
            (json.dumps(paper, ensure_ascii=False),
             json.dumps(config, ensure_ascii=False), row["paper_id"]),
        )
        filled += 1
    return filled, skipped


# ---- (d) plan videos 旧字段兜底转换 ---------------------------------------- #

def backfill_plan_videos(conn: sqlite3.Connection) -> int:
    """tasks.videos 平铺键 → video_units 一组 mode=daily，旧键删除。

    与 planEditor 装载迁移（migrateLegacyUnits → tasksForSave）落库形态
    一致：vids 去重保序转字符串，unit 追加在已有 video_units 之后。
    转换前后的排程读法相同（旧回退也把平铺键读成一个 daily 组）。
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_weeks)")]
    if not cols:
        return 0  # 无表：全新库，无事可做
    if "user" not in cols:
        raise SystemExit("plan_weeks 缺 user 列（旧表结构），请先部署带 "
                         "_migrate_plan_weeks 的版本再跑本脚本")
    changed = 0
    rows = conn.execute(
        "SELECT user, week_start, tasks FROM plan_weeks").fetchall()
    for row in rows:
        try:
            tasks = json.loads(row["tasks"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(tasks, dict) or "videos" not in tasks:
            continue
        vids: list[str] = []
        for v in (tasks.get("videos") or []):
            s = str(v).strip()
            if s and s not in vids:
                vids.append(s)
        if vids:
            units = [u for u in (tasks.get("video_units") or [])
                     if isinstance(u, dict)]
            units.append({"vids": vids, "mode": "daily"})
            tasks["video_units"] = units
        del tasks["videos"]  # 空串只剩噪声时同样把旧键清掉（planEditor 同款）
        conn.execute(
            "UPDATE plan_weeks SET tasks = ? WHERE user = ? AND week_start = ?",
            (json.dumps(tasks, ensure_ascii=False), row["user"], row["week_start"]),
        )
        changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="2026-11 精简一次性回填（别名折叠/错词回填/旧卷 structure/"
                    "plan videos 旧键）。幂等可重复跑。")
    ap.add_argument("fce_db", help="fce.db 路径")
    args = ap.parse_args(argv)
    db = args.fce_db
    if not Path(db).is_file():
        print(f"库不存在：{db}", file=sys.stderr)
        return 2

    # (a) 别名折叠：别名表由 data 目录决定，与库路径无关
    aliases = load_alias_file()
    with dbreg.connect(db) as conn:
        print(f"(a) recite 别名折叠：{fold_alias_progress(conn, aliases)}")

    # (b) 错词回填：逐用户扫全部历史会话；level 补全走 ReciteStore 同款
    store = ReciteStore(db_path=db)  # SCHEMA/迁移幂等，顺带保证表都在
    with dbreg.connect(db) as conn:
        if "recite_sessions" not in _tables(conn):
            print("(b) recite 复习回填：无 recite_sessions 表，跳过")
        else:
            users = [r[0] for r in conn.execute(
                "SELECT DISTINCT user FROM recite_sessions ORDER BY user")]
            total = 0
            for u in users:
                n = backfill_review(conn, u, store._vocab_levels)
                total += n
                print(f"(b) recite 复习回填：user={u} 补 {n} 词")
            if not users:
                print("(b) recite 复习回填：无会话用户，跳过")

    # (c) 旧卷 structure 回填
    with dbreg.connect(db) as conn:
        filled, skipped = backfill_paper_structure(conn)
        tail = f"，跳过 {skipped}（words 为空/坏 JSON，无从还原）" if skipped else ""
        print(f"(c) vocab_paper structure 回填：{filled} 卷{tail}")

    # (d) plan videos 旧键兜底
    with dbreg.connect(db) as conn:
        n = backfill_plan_videos(conn)
        print(f"(d) plan videos 旧键转换：{n} 周")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

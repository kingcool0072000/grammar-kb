"""视频学习：看 YouTube 视频（iframe 嵌入）+ 复述录音（教师 10 分制批改）。

与 :class:`ReadingStore` 同库（data/fce.db，环境变量 GRAMMAR_KB_FCE_DB 优先）：
- ``video_item``：视频清单（vid=YouTube 11 位 ID；vocab=生僻词 JSON；hints=复述提示）
- ``video_retell``：学生复述录音（base64，webm 自动转 m4a，与精读录音同链路；
  v3 起 ref_kind 区分对象：'video'=视频复述 / 'pattern'=句型跟读复述，
  pattern 行的 vid 语义 = pattern_item.id）

首次建库时种子 10 个 Free School 频道科普视频（真实 vid，2026-10 从频道页核对）；
教师后续增删改不回种（只在表新建那一刻写入）。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from . import dbreg, patterns
from .audioutil import MAX_AUDIO_B64, transcode_for_safari
from .clock import CN_TZ, today_cn

logger = logging.getLogger(__name__)

# 提升档打卡（plan/boost.py，并行车道）：录音提交成功不因打卡失败而失败，
# 故 import 失败（模块未就位）与调用失败（异常）都只告警不阻断。
try:
    from .plan import boost
except ImportError:  # 主 Agent 集成时保证就位；此前 pattern 门禁放行
    boost = None

VIDEO_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_item (
    vid          TEXT PRIMARY KEY,   -- YouTube 11 位视频 ID
    title        TEXT NOT NULL,
    category     TEXT DEFAULT '',    -- 动物 / 科学 / 历史 / 地理…
    duration_sec INTEGER DEFAULT 0,
    vocab        TEXT DEFAULT '[]',  -- 生僻词 JSON [{word, gloss}]
    hints        TEXT DEFAULT '',    -- 复述提示（换行分隔）
    enabled      INTEGER DEFAULT 1,
    ord          INTEGER DEFAULT 0,
    created_at   TEXT
);
CREATE TABLE IF NOT EXISTS video_retell (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user            TEXT NOT NULL,
    vid             TEXT NOT NULL,
    audio_b64       TEXT NOT NULL,
    mime            TEXT DEFAULT 'audio/webm',
    duration_sec    INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'pending',   -- pending / graded
    teacher_score   INTEGER,
    teacher_comment TEXT DEFAULT '',
    created_at      TEXT,
    graded_at       TEXT,
    ref_kind        TEXT NOT NULL DEFAULT 'video'  -- video=视频复述 / pattern=句型复述
);
CREATE INDEX IF NOT EXISTS idx_video_retell_user ON video_retell(user, created_at);
"""

# Free School（youtube.com/@freeschool）科普短片初始包：vid 均为频道页
# 实抓的真实视频 ID；duration_sec 为 watch 页 lengthSeconds（2026-10）。
# vocab=复述时会卡住的生僻词（词 + 中文提示）；hints=复述脚手架问题。
SEED_VIDEOS: list[dict] = [
    {"vid": "K-34rankRkw", "title": "10 Interesting Facts About Penguins",
     "category": "动物", "duration_sec": 354,
     "vocab": [("Antarctic", "南极洲"), ("colony", "（企鹅）群，聚居地"),
               ("huddle", "挤作一团（取暖）"), ("blubber", "厚脂肪层"),
               ("chick", "雏鸟"), ("krill", "磷虾"),
               ("leopard seal", "豹海豹"), ("waddle", "摇摇摆摆地走")],
     "hints": "Where do penguins live?\nWhat do they eat?\nHow do they stay warm in winter?\nWho hunts them?"},
    {"vid": "PmFP4K8-3NA", "title": "10 Interesting Facts About Polar Bears",
     "category": "动物", "duration_sec": 353,
     "vocab": [("Arctic", "北极"), ("predator", "掠食者"), ("prey", "猎物"),
               ("cub", "幼崽"), ("blubber", "脂肪层"), ("roam", "游荡，漫游"),
               ("seal", "海豹"), ("camouflage", "伪装")],
     "hints": "Where do polar bears live?\nWhat do they eat and how do they hunt?\nWhat problems do they face now?"},
    {"vid": "v6tUWk7vC6g", "title": "All About Bees",
     "category": "动物", "duration_sec": 649,
     "vocab": [("hive", "蜂巢"), ("nectar", "花蜜"), ("pollen", "花粉"),
               ("pollination", "授粉"), ("queen", "蜂王"), ("worker bee", "工蜂"),
               ("honeycomb", "巢脾（六边形蜂房）"), ("stinger", "螫针")],
     "hints": "What jobs do the queen, workers and drones have?\nHow is honey made from nectar?\nWhy are bees important for flowers and food?"},
    {"vid": "0tpY3sH4YTU", "title": "Lions: Life in the Pride",
     "category": "动物", "duration_sec": 403,
     "vocab": [("pride", "狮群"), ("lioness", "母狮"), ("mane", "鬃毛"),
               ("cub", "幼狮"), ("savanna", "稀树草原"), ("territory", "领地"),
               ("hunt", "捕猎")],
     "hints": "What is a pride, and who is in it?\nWho does most of the hunting?\nHow do lions mark and protect their territory?"},
    {"vid": "remX_MugsOM", "title": "All About Hummingbirds",
     "category": "动物", "duration_sec": 224,
     "vocab": [("hover", "悬停"), ("nectar", "花蜜"), ("migrate", "迁徙"),
               ("heartbeat", "心跳"), ("pollinate", "给…传粉"),
               ("wing", "翅膀"), ("backward", "向后")],
     "hints": "How do hummingbirds fly differently from other birds?\nWhat do they eat?\nHow fast is their heartbeat?"},
    {"vid": "LE8agcLlma8", "title": "What is Snow?",
     "category": "科学", "duration_sec": 262,
     "vocab": [("snowflake", "雪花"), ("crystal", "晶体"), ("moisture", "水汽"),
               ("freeze", "结冰"), ("melt", "融化"), ("unique", "独一无二的"),
               ("temperature", "温度")],
     "hints": "How is a snowflake formed?\nWhy is every snowflake different?\nWhat happens when snow melts?"},
    {"vid": "IkmAnLmXENo", "title": "Introduction to Friction",
     "category": "科学", "duration_sec": 238,
     "vocab": [("friction", "摩擦力"), ("surface", "表面"), ("rough", "粗糙的"),
               ("smooth", "光滑的"), ("grip", "抓力，抓地"), ("slide", "滑动"),
               ("force", "力")],
     "hints": "What is friction?\nHow does friction help us walk and drive?\nWhy is it easy to slip on ice?"},
    {"vid": "zta2y7cAMDk", "title": "Exploring the Grand Canyon",
     "category": "地理", "duration_sec": 353,
     "vocab": [("canyon", "峡谷"), ("erosion", "侵蚀"), ("layer", "（岩）层"),
               ("carve", "冲刷出，刻出"), ("steep", "陡峭的"),
               ("riverbed", "河床"), ("mile", "英里")],
     "hints": "How was the Grand Canyon formed?\nWhich river carved it, and how long did it take?\nWhat can scientists learn from its rock layers?"},
    {"vid": "qmd6Izx2ZkA", "title": "Egyptian Hieroglyphs and the Rosetta Stone",
     "category": "历史", "duration_sec": 433,
     "vocab": [("hieroglyph", "象形文字"), ("ancient", "古代的"),
               ("decipher", "破译"), ("scholar", "学者"), ("symbol", "符号"),
               ("carve", "刻"), ("mystery", "谜")],
     "hints": "What are hieroglyphs?\nWhy couldn't people read them for such a long time?\nHow did the Rosetta Stone solve the mystery?"},
    {"vid": "HuPGX8ycxlo", "title": "The Ancient Greek Creation Myth",
     "category": "历史", "duration_sec": 189,
     "vocab": [("myth", "神话"), ("goddess", "女神"), ("Titan", "泰坦巨神"),
               ("chaos", "混沌"), ("overthrow", "推翻"), ("rule", "统治"),
               ("Mount Olympus", "奥林匹斯山")],
     "hints": "How did the world begin in this story?\nWho were the first gods?\nHow did Zeus become the king of the gods?"},
]


def _now() -> str:
    return datetime.now(_tz.utc).isoformat(timespec="seconds")


def _today() -> str:
    """提升档「今天」：北京日历（与计划表 today_cn 同一天）。"""
    return today_cn().isoformat()


def _created_day(ts: str) -> str:
    """库存 UTC 时间戳 → 北京日历日（解析失败退回今天，兜底不拦批改）。"""
    try:
        return datetime.fromisoformat(
            (ts or "").strip().replace("Z", "+00:00")).astimezone(
            CN_TZ).date().isoformat()
    except ValueError:
        return _today()


def _norm_vocab(v) -> list[dict]:
    """vocab 入参宽容归一：[{word,gloss}] / [[word,gloss]] / ["word|gloss"] → 统一结构。"""
    out: list[dict] = []
    if isinstance(v, str):
        try:
            v = json.loads(v or "[]")
        except (ValueError, TypeError):
            v = []
    if not isinstance(v, list):
        return out
    for item in v:
        if isinstance(item, dict):
            w, g = str(item.get("word") or "").strip(), str(item.get("gloss") or "").strip()
        elif isinstance(item, (list, tuple)) and item:
            w = str(item[0]).strip()
            g = str(item[1]).strip() if len(item) > 1 else ""
        else:
            w, g = str(item or "").strip(), ""
        if w:
            out.append({"word": w[:40], "gloss": g[:60]})
    return out[:24]


def _vid_out(r: sqlite3.Row) -> dict:
    try:
        vocab = json.loads(r["vocab"] or "[]")
    except (ValueError, TypeError):
        vocab = []
    return {
        "vid": r["vid"], "title": r["title"], "category": r["category"] or "",
        "duration_sec": r["duration_sec"] or 0, "vocab": vocab,
        "hints": r["hints"] or "", "enabled": bool(r["enabled"]),
        "ord": r["ord"] or 0, "created_at": r["created_at"],
    }


def _retell_out(r: sqlite3.Row, with_audio: bool = False) -> dict:
    d = {
        "id": r["id"], "user": r["user"], "vid": r["vid"],
        "video_title": (r["video_title"] or "") if "video_title" in r.keys() else "",
        "mime": r["mime"], "duration_sec": r["duration_sec"],
        "status": r["status"], "teacher_score": r["teacher_score"],
        "teacher_comment": r["teacher_comment"],
        "created_at": r["created_at"], "graded_at": r["graded_at"],
        # 老库迁移前的行无该列，按 video 归类（存量行本就全是视频复述）
        "ref_kind": (r["ref_kind"] if "ref_kind" in r.keys() else "") or "video",
    }
    if with_audio:
        d["audio_b64"] = r["audio_b64"]
    return d


class VideoStore:
    """video_item / video_retell 读写（fce.db 同库）。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or dbreg.fce_db_path()
        if not Path(self.db_path).exists():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # 先探表再建 schema（_connect 会直接建表，探晚了种子永远不触发）
        conn = sqlite3.connect(self.db_path)
        try:
            had_table = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='video_item'"
            ).fetchone())
            conn.executescript(VIDEO_SCHEMA)
            self._migrate_ref_kind(conn)
            if not had_table:
                self._seed(conn)
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        return dbreg.connect(self.db_path)

    @staticmethod
    def _migrate_ref_kind(conn: sqlite3.Connection) -> None:
        """老库补列（ref_kind 后加的字段，ALTER 幂等；存量行默认 'video' 不动）。"""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(video_retell)")}
        if cols and "ref_kind" not in cols:
            conn.execute(
                "ALTER TABLE video_retell"
                " ADD COLUMN ref_kind TEXT NOT NULL DEFAULT 'video'")

    @staticmethod
    def _seed(conn: sqlite3.Connection) -> None:
        """表首次创建时写入初始 10 个视频（教师清空后不会回种）。"""
        for i, v in enumerate(SEED_VIDEOS):
            conn.execute(
                "INSERT INTO video_item (vid, title, category, duration_sec,"
                " vocab, hints, enabled, ord, created_at)"
                " VALUES (?,?,?,?,?,?,1,?,?)",
                (v["vid"], v["title"], v["category"], v["duration_sec"],
                 json.dumps([{"word": w, "gloss": g} for w, g in v["vocab"]],
                            ensure_ascii=False),
                 v["hints"], i, _now()),
            )

    # ---- 视频清单 ----

    def list_videos(self, include_disabled: bool = False) -> list[dict]:
        sql = "SELECT * FROM video_item"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY ord, vid"
        with self._connect() as conn:
            return [_vid_out(r) for r in conn.execute(sql).fetchall()]

    def get_video(self, vid: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM video_item WHERE vid = ?", (vid,)).fetchone()
        return _vid_out(row) if row else None

    def upsert_video(self, vid: str, title: str, category: str = "",
                     duration_sec: int = 0, vocab=None, hints: str = "",
                     enabled: bool = True, ord_: Optional[int] = None) -> dict:
        vid = (vid or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            raise ValueError("vid 须为 YouTube 视频 ID（11 位字母数字）")
        if not (title or "").strip():
            raise ValueError("标题不能为空")
        with self._connect() as conn:
            old = conn.execute(
                "SELECT ord FROM video_item WHERE vid = ?", (vid,)).fetchone()
            conn.execute(
                "INSERT INTO video_item (vid, title, category, duration_sec,"
                " vocab, hints, enabled, ord, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(vid) DO UPDATE SET title=excluded.title,"
                " category=excluded.category, duration_sec=excluded.duration_sec,"
                " vocab=excluded.vocab, hints=excluded.hints,"
                " enabled=excluded.enabled, ord=excluded.ord",
                (vid, title.strip()[:120], (category or "").strip()[:24],
                 max(0, int(duration_sec or 0)),
                 json.dumps(_norm_vocab(vocab), ensure_ascii=False),
                 (hints or "").strip()[:2000], 1 if enabled else 0,
                 ord_ if ord_ is not None else (old["ord"] if old else 999),
                 _now()),
            )
            row = conn.execute(
                "SELECT * FROM video_item WHERE vid = ?", (vid,)).fetchone()
        return _vid_out(row)

    def delete_video(self, vid: str) -> bool:
        """删视频（复述录音保留，教师仍可回看批改）。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM video_item WHERE vid = ?", (vid,))
            return cur.rowcount > 0

    # ---- 复述录音 ----

    @staticmethod
    def _require_today_boost(user: str, item_ref: str) -> None:
        """pattern 复述门禁：须在当日提升档清单里（视频复述不设此门）。

        boost 模块未就位或查询异常时放行并告警——计划信号故障不把录音
        提交卡死；不在清单属正常业务拒绝，抛 ValueError（路由转 422）。
        """
        if boost is None:
            logger.warning("plan.boost 未就位，跳过当日提升档门禁（%s）", item_ref)
            return
        try:
            items = boost.today_items(user, _today()) or []
            refs = {r for r in (boost.item_ref_of(it) for it in items
                                if isinstance(it, dict)) if r}
        except Exception:  # noqa: BLE001
            logger.warning("boost.today_items 查询失败，跳过门禁（%s）",
                           item_ref, exc_info=True)
            return
        if item_ref not in refs:
            raise ValueError("该句型不在今日提升档任务里，不能提交复述")

    def submit_retell(self, user: str, vid: str, audio_b64: str, mime: str,
                      duration_sec: int, ref_kind: str = "video") -> dict:
        ref_kind = (ref_kind or "video").strip() or "video"
        if ref_kind not in ("video", "pattern"):
            raise ValueError(f"未知的复述对象类型 {ref_kind}")
        audio_b64 = (audio_b64 or "").strip()
        if not audio_b64:
            raise ValueError("录音数据为空")
        if len(audio_b64) > MAX_AUDIO_B64:
            raise ValueError("录音过大（超过 9MB），请缩短录音时长")
        try:
            base64.b64decode(audio_b64, validate=False)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"录音数据无效: {e}") from e
        dur = max(0, min(int(duration_sec or 0), 10 * 60))
        audio_b64, mime = transcode_for_safari(audio_b64, mime or "audio/webm")
        if ref_kind == "pattern":
            # 句型跟读复述：vid 参数语义 = pattern_item.id
            pid = (vid or "").strip()
            if not pid.isdigit():
                raise ValueError("句型 id 无效")
            if patterns.get(int(pid)) is None:
                raise KeyError(f"句型 {pid} 不存在")
            self._require_today_boost(user, f"retell:{pid}")
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO video_retell (user, vid, audio_b64, mime,"
                    " duration_sec, status, ref_kind, created_at)"
                    " VALUES (?,?,?,?,?,'pending','pattern',?)",
                    (user, pid, audio_b64, mime or "audio/webm", dur, _now()),
                )
                rid = cur.lastrowid
            try:
                boost.record(user, _today(), f"retell:{pid}", "retell", passed=True)
            except Exception:  # noqa: BLE001  打卡失败不拦提交成功
                logger.warning("boost.record 失败（retell:%s user=%s）",
                               pid, user, exc_info=True)
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM video_retell WHERE id = ?", (rid,)).fetchone()
            return _retell_out(row, with_audio=False)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT vid, enabled FROM video_item WHERE vid = ?", (vid,)).fetchone()
            if row is None:
                raise KeyError(f"视频 {vid} 不存在")
            if not row["enabled"]:
                raise ValueError("该视频已下架，不能提交复述")
            cur = conn.execute(
                "INSERT INTO video_retell (user, vid, audio_b64, mime,"
                " duration_sec, status, ref_kind, created_at)"
                " VALUES (?,?,?,?,?,'pending','video',?)",
                (user, vid, audio_b64, mime or "audio/webm", dur, _now()),
            )
            row = conn.execute(
                "SELECT r.*, v.title AS video_title FROM video_retell r"
                " JOIN video_item v ON v.vid = r.vid WHERE r.id = ?",
                (cur.lastrowid,)).fetchone()
        return _retell_out(row, with_audio=False)

    def list_retells(self, user: Optional[str] = None,
                     status: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = ("SELECT r.*, v.title AS video_title FROM video_retell r"
               " LEFT JOIN video_item v ON v.vid = r.vid")
        conds, args = [], []
        if user:
            conds.append("r.user = ?")
            args.append(user)
        if status:
            conds.append("r.status = ?")
            args.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = [_retell_out(r) for r in rows]
        # 句型复述补句型原文，批改列表直接可读（缺表/无记录时静默跳过）
        pids = {o.get("vid") for o in out if o.get("ref_kind") == "pattern"}
        if pids:
            try:
                ph = ",".join("?" * len(pids))
                with self._connect() as conn:
                    sents = dict(conn.execute(
                        f"SELECT id, sentence FROM pattern_item WHERE id IN ({ph})",
                        sorted(pids)).fetchall())
                for o in out:
                    if o.get("ref_kind") == "pattern":
                        o["pattern_sentence"] = sents.get(int(o.get("vid") or 0))
            except sqlite3.Error:
                logger.debug("pattern_item 查询失败，批改行回退「句型 #id」")
        return out

    def get_retell(self, retell_id: int, with_audio: bool = True) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r.*, v.title AS video_title FROM video_retell r"
                " LEFT JOIN video_item v ON v.vid = r.vid WHERE r.id = ?",
                (retell_id,)).fetchone()
        return _retell_out(row, with_audio=with_audio) if row else None

    def retell_stats(self, user: str) -> dict[str, dict]:
        """某生逐视频复述统计：{vid: {n, best, pending, last_at, last_comment}}。

        best 只在已批改行上取 MAX；pending=存在未批改提交；
        last_comment=最近一次已批改的评语。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT vid, COUNT(*) n, MAX(teacher_score) best,"
                " MAX(status = 'pending') has_pending, MAX(created_at) last_at"
                " FROM video_retell WHERE user = ? GROUP BY vid", (user,)).fetchall()
            comments: dict[str, str] = {}
            for r in conn.execute(
                "SELECT vid, teacher_comment FROM video_retell"
                " WHERE user = ? AND status = 'graded' ORDER BY id", (user,),
            ):
                comments[r["vid"]] = r["teacher_comment"] or ""
        return {
            r["vid"]: {"n": r["n"], "best": r["best"],
                       "pending": bool(r["has_pending"]),
                       "last_at": r["last_at"],
                       "last_comment": comments.get(r["vid"], "")}
            for r in rows}

    def grade_retell(self, retell_id: int, score: int, comment: str) -> Optional[dict]:
        if not 0 <= int(score) <= 10:
            raise ValueError("分数须为 0-10")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, user, vid, ref_kind, created_at FROM video_retell"
                " WHERE id = ?", (retell_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE video_retell SET status='graded', teacher_score=?,"
                " teacher_comment=?, graded_at=? WHERE id = ?",
                (int(score), (comment or "")[:2000], _now(), retell_id),
            )
            # 句型复述过关（≥7 分）→ 回写提交当日 boost_log 的 score 列；
            # 0-6 分不动（完成打卡保留，过关不达成）。表缺失/行缺失只告警，
            # 批改本身不受影响。
            if (row["ref_kind"] or "video") == "pattern" and int(score) >= 7:
                try:
                    conn.execute(
                        "UPDATE boost_log SET score = ?"
                        " WHERE user = ? AND date = ? AND item_ref = ?",
                        (int(score), row["user"], _created_day(row["created_at"]),
                         f"retell:{row['vid']}"),
                    )
                except sqlite3.Error:
                    logger.warning(
                        "boost_log 回写失败（retell:%s user=%s）",
                        row["vid"], row["user"], exc_info=True)
            row = conn.execute(
                "SELECT r.*, v.title AS video_title FROM video_retell r"
                " LEFT JOIN video_item v ON v.vid = r.vid WHERE r.id = ?",
                (retell_id,)).fetchone()
        return _retell_out(row, with_audio=False)

    def delete_retell(self, retell_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM video_retell WHERE id = ?", (retell_id,))
            return cur.rowcount > 0

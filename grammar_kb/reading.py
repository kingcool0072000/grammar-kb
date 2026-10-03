"""阅读训练查询层：reading_article（base 原文段 / derived 派生文）+ 录音提交。

与 :class:`FcePaperStore` 同库（data/fce.db）：
- ``ReadingStore.list_articles()``：按 base_key 聚合（教师看全部；学生只看派生文）
- ``ReadingStore.add_derived()``：教师新增派生文章（挂到某段 base）
- ``ReadingStore.delete_article()``：教师删除派生文
- ``ReadingStore.record()`` / ``grade()``：学生提交录音（base64 webm）→ 教师 10 分制打分
"""
from __future__ import annotations

import base64
import re
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path
from .reading_build import READING_SCHEMA, word_count

_MAX_AUDIO_B64 = 12 * 1024 * 1024  # 12MB base64（约 9MB 音频，5 分钟 webm 足够）


def _transcode_for_safari(audio_b64: str, mime: str) -> tuple[str, str]:
    """webm/opus → m4a(aac)。

    学生端多为 Chrome（MediaRecorder 产 webm/opus），教师端若用 Safari/
    WebKit 打开，<audio> 不支持 webm/opus——表现为「听不见」。系统有
    ffmpeg 时统一转成 m4a（Safari/Chrome 双端原生可播）；无 ffmpeg 或
    转码失败则原样存（Chrome 教师端仍可播）。
    """
    if "webm" not in (mime or "").lower():
        return audio_b64, mime
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("ffmpeg"):
        return audio_b64, mime
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.webm"
            dst = Path(td) / "out.m4a"
            src.write_bytes(base64.b64decode(audio_b64))
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(src),
                 "-c:a", "aac", "-b:a", "64k", str(dst)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                return base64.b64encode(dst.read_bytes()).decode(), "audio/mp4"
    except Exception:  # noqa: BLE001
        pass
    return audio_b64, mime


def _now() -> str:
    return datetime.now(_tz.utc).isoformat(timespec="seconds")


def _ensure_selected_text_column(conn: sqlite3.Connection) -> None:
    """老库补列（selected_text 后加的字段，ALTER 幂等）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reading_recordings)")}
    if "selected_text" not in cols:
        conn.execute("ALTER TABLE reading_recordings ADD COLUMN selected_text TEXT DEFAULT ''")
        conn.commit()


class ReadingStore:
    """reading_article / reading_recordings 读写（fce.db 同库）。"""

    def __init__(self, db_path: Optional[str] = None):
        # 与 FcePaperStore 同库同路径解析（环境变量 → data/）
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(READING_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(READING_SCHEMA)
        _ensure_selected_text_column(conn)
        return conn

    # ---- 文章 ----

    def list_articles(self, kind: Optional[str] = None) -> list[dict]:
        """文章列表（不带 text 正文，列表页只展示元信息）。"""
        sql = "SELECT id, kind, base_key, title, words, source, created_at FROM reading_article"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        sql += " ORDER BY base_key, id"
        with self._connect() as conn:
            return [_art_out(r) for r in conn.execute(sql, args).fetchall()]

    def get_article(self, article_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ?", (article_id,)
            ).fetchone()
        return _art_out(row, with_text=True) if row else None

    def difficulty(self, article_ids: Optional[list[int]] = None) -> list[dict]:
        """批量难度报告（教师备课用）。ids 为空 = 全部派生文。"""
        with self._connect() as conn:
            if article_ids:
                ph = ",".join("?" * len(article_ids))
                rows = conn.execute(
                    "SELECT id, kind, base_key, title, words, text FROM reading_article"
                    f" WHERE id IN ({ph}) ORDER BY base_key, id",
                    [int(x) for x in article_ids]).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, kind, base_key, title, words, text FROM reading_article"
                    " WHERE kind = 'derived' ORDER BY base_key, id").fetchall()
        out = []
        for r in rows:
            rep = difficulty_report(r["text"] or "")
            out.append({"id": r["id"], "kind": r["kind"], "base_key": r["base_key"],
                        "title": r["title"], "words": r["words"], **rep})
        return out

    def add_derived(
        self, base_key: str, title: str, text: str, source: str = ""
    ) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("正文不能为空")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reading_article (kind, base_key, title, text, words, source, created_at)"
                " VALUES ('derived', ?, ?, ?, ?, ?, ?)",
                (base_key, title.strip() or "未命名", text, word_count(text),
                 source.strip(), _now()),
            )
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _art_out(row, with_text=True)

    def update_derived(
        self, article_id: int, title: Optional[str], text: Optional[str],
        source: Optional[str], base_key: Optional[str],
    ) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ? AND kind = 'derived'",
                (article_id,),
            ).fetchone()
            if row is None:
                return None
            sets, args = [], []
            if title is not None:
                sets.append("title = ?")
                args.append(title.strip() or "未命名")
            if text is not None and text.strip():
                sets.append("text = ?")
                args.append(text.strip())
                sets.append("words = ?")
                args.append(word_count(text))
            if source is not None:
                sets.append("source = ?")
                args.append(source.strip())
            if base_key is not None and base_key.strip():
                sets.append("base_key = ?")
                args.append(base_key.strip())
            if sets:
                args.append(article_id)
                conn.execute(
                    f"UPDATE reading_article SET {', '.join(sets)} WHERE id = ?", args
                )
            row = conn.execute(
                "SELECT * FROM reading_article WHERE id = ?", (article_id,)
            ).fetchone()
        return _art_out(row, with_text=True)

    def delete_derived(self, article_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM reading_article WHERE id = ? AND kind = 'derived'",
                (article_id,),
            )
            return cur.rowcount > 0

    # ---- 录音 ----

    def submit_recording(
        self, user: str, article_id: int, audio_b64: str, mime: str,
        duration_sec: int, selected_text: str = "",
    ) -> dict:
        audio_b64 = (audio_b64 or "").strip()
        if not audio_b64:
            raise ValueError("录音数据为空")
        if len(audio_b64) > _MAX_AUDIO_B64:
            raise ValueError("录音过大（超过 9MB），请缩短录音时长")
        # base64 有效性校验（宽松：能解出字节即可）
        try:
            base64.b64decode(audio_b64, validate=False)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"录音数据无效: {e}") from e
        dur = max(0, min(int(duration_sec or 0), 5 * 60))
        audio_b64, mime = _transcode_for_safari(audio_b64, mime or "audio/webm")
        with self._connect() as conn:
            art = conn.execute(
                "SELECT id FROM reading_article WHERE id = ?", (article_id,)
            ).fetchone()
            if art is None:
                raise KeyError(f"文章 id={article_id} 不存在")
            cur = conn.execute(
                "INSERT INTO reading_recordings (user, article_id, audio_b64, mime,"
                " duration_sec, selected_text, status, created_at)"
                " VALUES (?,?,?,?,?,?, 'pending', ?)",
                (user, article_id, audio_b64, mime or "audio/webm", dur,
                 (selected_text or "")[:4000], _now()),
            )
            row = conn.execute(
                "SELECT * FROM reading_recordings WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _rec_out(row, with_audio=False)

    def list_recordings(
        self, user: Optional[str] = None, status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = (
            "SELECT r.*, a.title AS article_title, a.base_key, a.kind AS article_kind"
            " FROM reading_recordings r JOIN reading_article a ON a.id = r.article_id"
        )
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
            return [_rec_out(r, with_audio=False) for r in conn.execute(sql, args).fetchall()]

    def get_recording(self, rec_id: int, with_audio: bool = True) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r.*, a.title AS article_title, a.base_key, a.kind AS article_kind"
                " FROM reading_recordings r JOIN reading_article a ON a.id = r.article_id"
                " WHERE r.id = ?",
                (rec_id,),
            ).fetchone()
        return _rec_out(row, with_audio=with_audio) if row else None

    def grade_recording(
        self, rec_id: int, score: int, comment: str
    ) -> Optional[dict]:
        if not 0 <= int(score) <= 10:
            raise ValueError("分数须为 0-10")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM reading_recordings WHERE id = ?", (rec_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE reading_recordings SET status='graded', teacher_score=?,"
                " teacher_comment=?, graded_at=? WHERE id = ?",
                (int(score), comment or "", _now(), rec_id),
            )
            row = conn.execute(
                "SELECT * FROM reading_recordings WHERE id = ?", (rec_id,)
            ).fetchone()
        return _rec_out(row, with_audio=False)

    def delete_recording(self, rec_id: int) -> bool:
        """删除一条录音提交（教师清理数据）。"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM reading_recordings WHERE id = ?", (rec_id,)
            )
            return cur.rowcount > 0

    def export_recordings(self, out_dir: str, user: Optional[str] = None) -> list[Path]:
        """把录音导出为音频文件（附朗读选段文本），返回文件路径列表。

        文件名：rec{id}_{user}_{YYYYMMDD_HHMM}.{m4a|webm}；每条录音旁若
        有 selected_text，同目录写 rec{id}_selected_text.txt（给外部工具
        /模型对照原文用）。
        """
        import re as _re

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        sql = (
            "SELECT r.id, r.user, r.mime, r.audio_b64, r.selected_text,"
            " r.created_at, a.title FROM reading_recordings r"
            " JOIN reading_article a ON a.id = r.article_id"
        )
        args: tuple = ()
        if user:
            sql += " WHERE r.user = ?"
            args = (user,)
        sql += " ORDER BY r.id"
        files: list[Path] = []
        with self._connect() as conn:
            for r in conn.execute(sql, args).fetchall():
                mime = (r["mime"] or "").lower()
                ext = "m4a" if "mp4" in mime else ("webm" if "webm" in mime else "bin")
                when = _re.sub(r"[-:T]", "", (r["created_at"] or "")[:16])
                name = f"rec{r['id']}_{r['user']}_{when}.{ext}"
                p = out / name
                p.write_bytes(base64.b64decode(r["audio_b64"]))
                files.append(p)
                if r["selected_text"]:
                    tp = out / f"rec{r['id']}_selected_text.txt"
                    tp.write_text(r["selected_text"], encoding="utf-8")
                    files.append(tp)
        return files


def _art_out(r: sqlite3.Row, with_text: bool = False) -> dict:
    d = {
        "id": r["id"], "kind": r["kind"], "base_key": r["base_key"],
        "title": r["title"], "words": r["words"], "source": r["source"],
        "created_at": r["created_at"],
    }
    if with_text:
        d["text"] = r["text"]
    return d


def _rec_out(r: sqlite3.Row, with_audio: bool = False) -> dict:
    d = {
        "id": r["id"], "user": r["user"], "article_id": r["article_id"],
        "article_title": r["article_title"] if "article_title" in r.keys() else "",
        "base_key": r["base_key"] if "base_key" in r.keys() else "",
        "article_kind": r["article_kind"] if "article_kind" in r.keys() else "",
        "mime": r["mime"], "duration_sec": r["duration_sec"],
        "selected_text": r["selected_text"] if "selected_text" in r.keys() else "",
        "status": r["status"], "teacher_score": r["teacher_score"],
        "teacher_comment": r["teacher_comment"],
        "created_at": r["created_at"], "graded_at": r["graded_at"],
    }
    if with_audio:
        d["audio_b64"] = r["audio_b64"]
    return d


# ---- 精读难度评价（备课中心-精读板块：LX 词汇 / 语法结构 / 题材 三维） ----
# 纯函数分析 + ReadingStore.difficulty() 批量出口；评分口径见各 _score_* 函数。

_FUNCTION_WORDS = frozenset("""
a an the and or but if then than that this these those there here of in on at to
from by for with about into over under after before between during without within
as is am are was were be been being do does did doing done have has had having
i you he she it we they me him her us them my your his its our their mine yours
hers ours theirs myself yourself himself herself itself ourselves themselves
who whom whose which what when where why how not no nor so too very just also
only ever never always often sometimes usually will would shall should can could
may might must ought because whether while once whereas unless since although
though much many more most some any all each every both few several other
another such same own else someone somebody something anything anybody anyone
everything everyone everybody nothing nobody nowhere somewhere anywhere
everywhere above below through across against along among around behind beyond
near outside toward towards upon per via still already yet again almost nearly
quite rather together away back down up out off
there's it's don't doesn't didn't won't can't couldn't
wouldn't shouldn't isn't aren't wasn't weren't hasn't haven't hadn't i'm you're
he's she's we're they're that's what's let's
""".split())

_IRREG_PP = frozenset("""
arisen awoken been borne beaten become begun bent bitten bled blown broken bred
brought built bought caught chosen come done drawn drunk driven eaten fallen fed
felt fought found flown forgotten forgiven frozen given gone grown heard hidden
hit held hurt kept known laid led left lent let lost made meant met paid put
read ridden risen run said seen sold sent shaken shone shot shown sung sunk sat
slept spoken spent split spread stood stolen struck sworn taken taught told
thought thrown understood woken worn won written
""".split())

_BASE_VERBS = frozenset("""
be am is are was were do does did have has had go goes went come came get gets
got make makes made take takes took see sees saw know knows knew think thinks
thought find finds found give gives gave tell told say says said work works
worked call called called try tries tried ask asks asked need needs needed feel
feels felt become became leave left put mean means meant keep kept let begin
began begun seem seems help helps show showed show talk talked turn turned start
started lose lost run ran move moved live lived play played believe believed
bring brought happen happened write wrote written provide provided sit sat stand
stood pay paid meet met include included continue continued learn learned read
spend spent grow grew open opened walk walked win won offer offered remember
remembered love loved consider considered appear appeared buy bought wait waited
serve served die died send sent expect expected build built stay stayed fall fell
cut hit reach reached remain remained suggest suggested raise raised pass passed
sell sold require required report reported decide decided pull pulled
""".split())

_TOPIC_CATS = [
    # (key, 名称, 难度权重 1易-4难, 关键词, 说明)
    ("daily", "日常生活", 1,
     "school family home friend party holiday food cook dinner mum dad brother "
     "sister teacher class classroom homework birthday game weekend shop",
     "贴近学生日常经验，理解门槛最低"),
    ("sport", "运动与冒险", 2,
     "race racing bmx bike bicycle ride riding climb climbing surf surfing "
     "swim swimming run runner sport sports team match competition coach "
     "adventure journey trip camp explore explorer sail kayak",
     "动作与情节驱动，词汇集中于运动场景"),
    ("animal", "动物与自然", 2,
     "animal animals bird dolphin dolphins whale sharks shark insect dog cat "
     "horse horses plant plants tree forest nature natural species wild wildlife "
     "sea ocean zoo farm monkey bear lion tiger elephant penguin camel",
     "科普类高频题材，专有动物词需预习"),
    ("story", "小说叙事", 2,
     "story chapter suddenly whispered shouted cried laughed stared nodded "
     "sighed climbed jumped girl boy they'd mr mrs",
     "叙事文本：有人物/情节链，句式多为过去时"),
    ("history", "历史与文化", 3,
     "history historical ancient king queen century war battle empire tradition "
     "traditional culture cultural museum art artist painting famous explorer "
     "egypt greek roman medieval dynasty",
     "需背景知识，词汇含时期/人物专名"),
    ("tech", "科技与发明", 3,
     "computer computers phone mobile internet website online app technology "
     "robot robots machine machines engine invent invention inventor invention "
     "screen electric electronic device design software digital code",
     "抽象概念词多，常涉被动语态与复合句"),
    ("space", "太空与科学前沿", 4,
     "space planet planets moon rocket astronaut astronauts satellite mars "
     "universe solar galaxy orbit gravity telescope nasa stars light-year",
     "科普前沿题材，概念抽象、术语密度高"),
    ("env", "环境与全球议题", 4,
     "climate environment environmental pollution pollute polluted recycle "
     "recycling global warming energy plastic plastics carbon earth protect "
     "protection endangered extinct rainforest waste",
     "全球议题类说明文，抽象名词与议论文式句法"),
]

# 语法结构检出：正则按出现次数计数（约值），映射哈1讲次
_GRAMMAR_DEFS = [
    ("passive", "被动语态", "第29-30讲", 2.0,
     r"\b(?:am|is|are|was|were|been|being|get|gets|got)\s+"
     r"(?:\w{2,}ed|%s)\b" % "|".join(sorted(_IRREG_PP))),
    ("relcl", "定语从句", "第47讲", 3.0,
     r"\b(?:who|whom|whose|which)\s+"
     r"(?:is|are|was|were|am|has|have|had|can|could|will|would|do|does|did|"
     r"\w+ed|\w+s)\b"
     r"|,\s*(?:who|which|that)\s+\w+"),
    ("objcl", "宾语从句", "第45讲", 2.5,
     r"\b(?:say|says|said|think|thinks|thought|know|knows|knew|believe|believes|"
     r"believed|hope|hopes|hoped|ask|asks|asked|wonder|wonders|explained?|"
     r"realis?ed|notice|noticed|found|feel|feels|felt)\s+"
     r"(?:that|if|whether|what|where|why|how|who)\b"),
    ("advcl", "状语从句", "第46讲", 1.2,
     r"\b(?:when|while|because|although|though|if|until|after|before|since|"
     r"as soon as|so that|even though|whereas|unless)\b"),
    ("presperf", "现在完成时", "第22-27讲", 2.0,
     r"\b(?:have|has)\s+(?:\w{2,}ed|%s)\b" % "|".join(sorted(_IRREG_PP))),
    ("pastperf", "过去完成时", "第26-27讲", 2.5,
     r"\bhad\s+(?:\w{2,}ed|%s)\b" % "|".join(sorted(_IRREG_PP))),
    ("modal", "情态动词", "第31-32讲", 1.0,
     r"\b(?:can|could|may|might|must|should|would|shall|ought to|"
     r"have to|has to|had to)\b"),
    ("gerund", "动名词", "第36-37讲", 1.5,
     r"\b(?:enjoy|enjoys|enjoyed|finish|finishes|finished|mind|minds|practis|"
     r"practic|suggest|suggests|avoid|avoids|keep|keeps|stop|stops|start|starts|"
     r"begin|begins|love|loves|like|likes|hate|hates|prefer|prefers|"
     r"remember|remembers|forget|forgets|try|tries)\w*\s+\w+ing\b"
     r"|\b(?:at|in|on|of|for|with|about|by|from|without|before|after)\s+"
     r"\w+ing\b"),
    ("infinitive", "不定式", "第34-35讲", 1.2,
     r"\bto\s+(?:%s)\b" % "|".join(sorted(_BASE_VERBS))),
    ("progressive", "进行时", "第22-27讲", 1.0,
     r"\b(?:am|is|are|was|were)\s+\w+ing\b"),
    ("comparative", "比较级/最高级", "第15-18讲", 0.8,
     r"\b\w+er\s+than\b|\bmore\s+\w+\s+than\b|\b(?:most|best|worst)\b|\bthe\s+\w+est\b"),
    ("inversion", "倒装句", "第42讲", 3.0,
     r"\b(?:Never|Seldom|Hardly|Not only|Only then)\s+\w+\s+"
     r"(?:do|does|did|is|are|was|were|have|has|had|can|will|would)\b"),
]
_GRAMMAR_RE = [(k, n, ls, w, re.compile(p, re.IGNORECASE))
               for k, n, ls, w, p in _GRAMMAR_DEFS]

_LEVEL_LABELS = ["入门", "进阶", "中阶", "中高", "高阶"]

_vocab_map_cache: dict = {}


def _vocab_level_map() -> dict[str, int]:
    """vocab_word 全量 {word: level}（进程内缓存；库缺失返回空表）。"""
    if _vocab_map_cache:
        return _vocab_map_cache
    try:
        from .ingest import default_db_path as _grammar_db
        path = _grammar_db()
        if not Path(path).exists():
            return _vocab_map_cache
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            _vocab_map_cache.update(
                dict(conn.execute("SELECT word, level FROM vocab_word").fetchall()))
    except Exception:  # noqa: BLE001
        pass  # 词库缺失时词汇维度降级（按库外词处理）
    return _vocab_map_cache


_IRREG_FORMS = {
    "said": "say", "says": "say", "known": "know", "knew": "know", "shown": "show",
    "showed": "show", "told": "tell", "made": "make", "went": "go", "gone": "go",
    "came": "come", "taken": "take", "took": "take", "found": "find", "began": "begin",
    "begun": "begin", "thought": "think", "felt": "feel", "kept": "keep",
    "left": "leave", "meant": "mean", "met": "meet", "paid": "pay", "built": "build",
    "sat": "sit", "stood": "stand", "lost": "lose", "sent": "send", "spent": "spend",
    "brought": "bring", "bought": "buy", "taught": "teach", "caught": "catch",
    "ran": "run", "grew": "grow", "grown": "grow", "drew": "draw", "drawn": "draw",
    "fell": "fall", "fallen": "fall", "held": "hold", "heard": "hear", "sold": "sell",
    "wore": "wear", "worn": "wear", "won": "win", "wrote": "write", "written": "write",
    "rode": "ride", "ridden": "ride", "chose": "choose", "chosen": "choose",
    "broke": "break", "broken": "break", "drove": "drive", "driven": "drive",
    "ate": "eat", "eaten": "eat", "gave": "give", "given": "give", "saw": "see",
    "seen": "see", "flew": "fly", "flown": "fly", "rose": "rise", "risen": "rise",
    "spoke": "speak", "spoken": "speak", "woke": "wake", "woken": "wake",
    "sang": "sing", "sung": "sing", "swam": "swim", "swum": "swim",
    "men": "man", "women": "woman", "children": "child", "feet": "foot",
    "teeth": "tooth", "mice": "mouse", "died": "die", "dying": "die",
    "lay": "lie", "lain": "lie", "laid": "lay", "lying": "lie", "tried": "try",
    "studied": "study", "carried": "carry", "worried": "worry",
}


def _lemma_candidates(w: str):
    """屈折变形 → 原形候选（vocab_word 只存原形；按命中次序尝试）。"""
    yield w
    if w.endswith("ies"):
        yield w[:-3] + "y"
    if w.endswith("ves"):
        yield w[:-3] + "f"
        yield w[:-3] + "fe"
    if w.endswith("es"):
        yield w[:-2]
        yield w[:-1]
    if w.endswith("s"):
        yield w[:-1]
    if w.endswith("ied"):
        yield w[:-3] + "y"
    if w.endswith("ed"):
        stem = w[:-2]
        yield stem + "e"        # liked → like
        yield stem              # walked → walk
        if len(stem) > 1 and stem[-1] == stem[-2]:
            yield stem[:-1]     # stopped → stop
    if w.endswith("ing"):
        stem = w[:-3]
        yield stem + "e"        # making → make
        yield stem              # reading → read
        if len(stem) > 1 and stem[-1] == stem[-2]:
            yield stem[:-1]     # running → run
    if w.endswith("est"):
        yield w[:-3]            # toughest → tough
        yield w[:-2]
        if len(w) > 4 and w[-4] == w[-5]:
            yield w[:-4]        # biggest → big
    if w.endswith("er"):
        yield w[:-2]
        yield w[:-1]
        if len(w) > 4 and w[-3] == w[-4]:
            yield w[:-3]        # bigger → big


def _lookup_level(vm: dict, w: str) -> tuple[Optional[int], str]:
    """查词层级；命中非原形时返回还原后的原形（供词形统计）。"""
    if w in vm:
        return vm[w], w
    base = _IRREG_FORMS.get(w)
    if base is not None and base in vm:
        return vm[base], base
    for c in _lemma_candidates(w):
        if c in vm:
            return vm[c], c
    return None, w


def _sentence_stats(text: str) -> tuple[float, int]:
    sents = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    lens = [len(re.findall(r"[A-Za-z']+", s)) for s in sents]
    if not lens:
        return 0.0, 0
    return sum(lens) / len(lens), max(lens)


def _proper_nouns(text: str) -> set[str]:
    """句首（非句首）仍大写的词 → 专名（人名/地名等，不计难度）。"""
    out: set[str] = set()
    for m in re.finditer(r"\b[A-Z][a-z']+\b", text):
        prev = text[:m.start()].rstrip()
        if not prev or prev[-1] in ".!?\"':—-\n":
            continue  # 句首/段首/引语开头的大写不是专名
        out.add(m.group(0).lower())
    return out


def difficulty_report(text: str) -> dict:
    """三维难度报告：词汇（LX 分层）/ 语法（结构检出×哈1讲次）/ 题材。

    纯静态分析（正则 + vocab_word 查询），不依赖学生数据；分数量纲均 0-100。
    """
    text = text or ""
    words_iter = re.finditer(r"[A-Za-z']+", text)
    raw_tokens = [m.group(0) for m in words_iter]
    proper = _proper_nouns(text)
    content = [t.lower() for t in raw_tokens
               if t.lower() not in _FUNCTION_WORDS and t.lower() not in proper]
    n_content = len(content) or 1

    lv_map = _vocab_level_map()
    levels: list[int] = []
    offlist: dict[str, int] = {}
    hard_words: dict[str, int] = {}
    dist = {str(i): 0 for i in range(8)}
    for w in content:
        lv, lemma = _lookup_level(lv_map, w)
        if lv is None:
            offlist[w] = offlist.get(w, 0) + 1
        else:
            levels.append(lv)
            dist[str(lv)] += 1
            if lv >= 5:
                hard_words[lemma] = hard_words.get(lemma, 0) + 1
    avg_lv = sum(levels) / len(levels) if levels else 0.0
    off_pct = sum(offlist.values()) / n_content * 100
    hard_pct = sum(hard_words.values()) / n_content * 100
    vocab_score = min(100.0, avg_lv / 7 * 55
                      + min(hard_pct, 15) * 1.8 + min(off_pct, 15) * 1.2)

    n_words = len(raw_tokens) or 1
    structures = []
    for key, name, lessons, weight, rx in _GRAMMAR_RE:
        found = rx.findall(text)
        if found:
            structures.append({"key": key, "name": name, "lessons": lessons,
                               "count": len(found), "weight": weight})
    # 结构密度 = 加权结构数/百词（本库典型 5-12），句长因子 = 平均句长超 9 词部分
    dens100 = sum(s["weight"] * s["count"] * 100 / n_words for s in structures)
    avg_sent, max_sent = _sentence_stats(text)
    sent_factor = max(0.0, min(25.0, (avg_sent - 9) * 4))
    grammar_score = min(100.0, dens100 * 5 + sent_factor)

    text_l = " " + text.lower() + " "
    topic_scores = []
    for key, label, tw, kws, desc in _TOPIC_CATS:
        hits = sum(text_l.count(" " + k + " ") for k in kws.split())
        if hits:
            topic_scores.append((hits, key, label, tw, desc,
                                 [k for k in kws.split()
                                  if " " + k + " " in text_l][:12]))
    topic_scores.sort(key=lambda x: -x[0])
    expository = bool(re.search(
        r"\b(?:because|however|although|therefore|as a result|in fact|"
        r"for example|such as|this means)\b", text, re.IGNORECASE))
    if topic_scores:
        t_hits, _, t_label, t_weight, t_desc, t_kws = topic_scores[0]
    else:
        past_dens = len(re.findall(r"\b\w+ed\b", text)) * 100 / n_words
        t_label, t_weight = ("小说叙事", 2) if past_dens >= 4 else ("一般说明文", 3)
        t_desc, t_kws, t_hits = "未命中题材关键词，按文体推断", [], 0
    # 题材难度 = 基础权重(1易-4难→0-70) + 关键词命中密度 + 议论文式句法加成
    topic_score = min(100.0, (t_weight - 1) / 3 * 70
                      + min(t_hits, 12) * 1.2 + (10 if expository else 0))

    overall = round(vocab_score * 0.4 + grammar_score * 0.45 + topic_score * 0.15, 1)
    level = 1 + (overall >= 30) + (overall >= 42) + (overall >= 55) + (overall >= 68)
    return {
        "score": overall,
        "level": level,
        "level_label": _LEVEL_LABELS[level - 1],
        "dims": {
            "vocab": {
                "score": round(vocab_score, 1),
                "avg_level": round(avg_lv, 2),
                "off_pct": round(off_pct, 1),
                "hard_pct": round(hard_pct, 1),
                "content": len(content), "matched": len(levels),
                "dist": dist,
                "offlist": sorted(offlist.items(), key=lambda x: -x[1])[:20],
                "hard_words": sorted(hard_words.items(), key=lambda x: -x[1])[:20],
            },
            "grammar": {
                "score": round(grammar_score, 1),
                "avg_sent": round(avg_sent, 1), "max_sent": max_sent,
                "structures": sorted(structures, key=lambda s: -s["weight"] * s["count"]),
            },
            "topic": {
                "score": round(topic_score, 1),
                "label": t_label, "weight": t_weight, "desc": t_desc,
                "keywords": t_kws, "expository": expository,
            },
        },
    }

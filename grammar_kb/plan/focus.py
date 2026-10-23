"""泛读会话词数、轨迹段与挂机会话剔除。"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
class FocusMixin:

    @staticmethod
    def _reading_session_valid(r) -> bool:
        """泛读会话有效性：剔除非真实阅读的信号（快速翻章/误触/闪进闪出）。

        判定（任一满足即有效）：
        - 停留 ≥60 秒（真读了）
        - 有交互：查词/播放/滚动/章导且停留 ≥10 秒
        （章切换后停留 <5 秒的翻章浏览——时长极短且无任何交互——无效。）
        """
        dur = r["active_sec"] or 0
        if dur >= 60:
            return True
        inter = ((r["lookups"] or 0) + (r["plays"] or 0)
                 + (r["scroll_count"] or 0) + (r["chapter_navs"] or 0))
        return dur >= 10 and inter > 0


    def _reading_day_detail(self, rows: list) -> list[dict]:
        """泛读会话 → 逐书精确实录（章区间 + 词数 + 分钟）。

        词数口径（按用户公式）：每本书取当日 span——
          结束位置所在章的结束位置 − 起始位置所在章的起始位置，
          逐章累计词数（边界章按重叠比例），剔除非必读章
          （book_reading_config.chapters 为空=全书口径）。
        percent 为 0-100 全书口径（epubjs locations/像素百分比），
          词位置 = percent/100 × 全书总词数。
        返回 [{book, from, to, min, words}]（from/to 为计入的章 idx；
          缺章节数据的书 from/to=None、words=0，只报分钟）。
        """
        conn = self._lib_connect()
        lib: dict[int, dict] = {}
        if conn is not None:
            try:
                with contextlib.closing(conn):
                    ids = {r["book_id"] for r in rows if r["book_id"] is not None}
                    lib = self._book_meta(conn, ids)
            except sqlite3.Error:
                pass

        by_book: dict[int, list] = {}
        for r in rows:
            if (r["book_id"] is not None and self._reading_session_valid(r)
                    and not self._zombie_reading(r)):
                by_book.setdefault(r["book_id"], []).append(r)
        # 无效会话也计入分钟口径的「在场时长」提示？不——无效即无效，
        # 分钟与词数都只认有效会话（否则出现 0 分钟 N 词的矛盾展示）。

        out = []
        for bid, srows in by_book.items():
            entry = {
                "book": (lib.get(bid, {}).get("title")
                         or (srows[0]["book_title"] or "") or f"#{bid}"),
                "from": None, "to": None,
                "min": round(sum(r["active_sec"] or 0 for r in srows) / 60),
                "words": 0,
            }
            info = lib.get(bid)
            if not info:
                out.append(entry)
                continue
            total = sum(w for _, w in info["chs"])
            if total <= 0:
                out.append(entry)
                continue

            # ---- 段级词数（v3 位置轨迹优先；无轨迹的存量会话退回 span 口径+限速封顶）----
            # 有效段集合：[(s_pos, e_pos)] 词位置区间（已剔跳章与短停留段）
            segs: list[tuple[float, float]] = []
            for r in srows:
                segs.extend(self._row_segs(r, total))
            # 去重合并（段可能跨会话重叠），再逐章计词。
            # 章区间不再输出（from/to 恒 None）：线性词数映射的落点章
            # 与真实 CFI 翻页位置误差可达 ±10+ 章（各章篇幅不均），
            # 展示出来误导；学习日志只保留时长 + 词数。
            words, _counted = self._segs_words(segs, info["chs"], info["required"])
            entry["words"] = words
            out.append(entry)
        return out


    @staticmethod
    def _zombie_reading(r) -> bool:
        """泛读僵尸会话：泛读馆会话「时长够长被判有效」但零阅读证据——
        无位置轨迹、起止零位移（0%→0%，阅读器恢复失败落书首后挂着不动）。
        学习日志行/时长聚合/泛读分钟都剔（focus_words 的「无有效阅读」
        flag 同口径；非泛读模块恒 False）。"""
        if (r["module"] or "library") != "library" or r["book_id"] is None:
            return False
        # 注意：percent_track 存 JSON 字符串，空轨迹 '[]' 也是 truthy——
        # 必须解析后看长度
        try:
            if json.loads(r["percent_track"] or "[]"):
                return False
        except (ValueError, TypeError):
            pass
        inter = ((r["lookups"] or 0) + (r["plays"] or 0)
                 + (r["scroll_count"] or 0) + (r["chapter_navs"] or 0))
        if inter:
            return False
        return (r["percent_end"] or 0) <= (r["percent_start"] or 0)


    @staticmethod
    def _display_chapter_nos(chs: list, lo_idx: int, hi_idx: int) -> tuple[int, int]:
        """raw 章 idx → 展示章号（结构化重编号，与 focus_words 同口径）。

        epub 的 raw idx 含封面/目录/版权等前置页（Percy 第 6 章=idx10），
        对外章号一律「首个 ≥200 词内容章起算第 1 章」。"""
        first_content = 0
        for idx, wc in chs:
            if (wc or 0) >= 200:
                first_content = idx
                break
        return lo_idx - first_content + 1, hi_idx - first_content + 1


    @staticmethod
    def _book_meta(lib, ids) -> dict[int, dict]:
        """书目元数据批量取：章词数 + 必读章配置 + 章标题 + 书名。
        缺章节数据的书不出现（调用方按缺元数据兜底）。"""
        meta: dict[int, dict] = {}
        for bid in ids:
            chs = lib.execute(
                "SELECT idx, word_count, title FROM chapters"
                " WHERE book_id = ? ORDER BY idx", (bid,)).fetchall()
            if not chs:
                continue
            cfg = lib.execute(
                "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                (bid,)).fetchone()
            try:
                req = set(json.loads(cfg[0])) if cfg and cfg[0] else None
            except (ValueError, TypeError):
                req = None
            meta[bid] = {"chs": [(c[0], c[1] or 0) for c in chs],
                         "required": req,
                         "titles": {c[0]: (c[2] or "") for c in chs}}
        # 书名（书已删时缺行，调用方用会话里的 book_title 兜底）
        for bid in list(meta):
            r = lib.execute("SELECT title FROM books WHERE id = ?", (bid,)).fetchone()
            if r:
                meta[bid]["title"] = r[0]
        return meta


    def _row_segs(self, r, total: int) -> list[tuple[float, float]]:
        """单会话 → 有效词位置区段：位置轨迹优先（剔跳章+限速封顶），
        无轨迹的存量会话退回 span 口径 + 会话级封顶（400 词/分 × active 分钟）。"""
        track = self._session_track(r)
        if track:
            return self._valid_track_segments(track, total, r["active_sec"] or 0)
        s = r["percent_start"] or 0
        e = r["percent_end"] or 0
        if e <= s:
            return []
        cap = max(0.0, (r["active_sec"] or 0)) / 60 * self.READ_WPM_CAP
        span_words = (e - s) / 100 * total
        ratio = min(1.0, cap / span_words) if span_words > 0 else 1.0
        return [(s / 100 * total, s / 100 * total + span_words * ratio)]


    @staticmethod
    def _segs_words(segs: list, chs: list, required) -> tuple[int, list[int]]:
        """词位置区段 →（排序去重合并后）与必读章的重叠词数 + 计入章 idx。"""
        merged: list[list[float]] = []
        for s_pos, e_pos in sorted(segs):
            if e_pos <= s_pos:
                continue
            if merged and s_pos <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e_pos)
            else:
                merged.append([s_pos, e_pos])
        words = 0
        counted: list[int] = []
        for s_pos, e_pos in merged:
            cum = 0
            for idx, wc in chs:
                c0, c1 = cum, cum + wc
                cum = c1
                if required is not None and idx not in required:
                    continue  # 非必读章剔除
                ov = min(e_pos, c1) - max(s_pos, c0)
                if ov > 0:
                    words += int(round(ov))
                    counted.append(idx)
        return words, counted


    def focus_words(self, ids: list) -> dict[int, dict]:
        """学习日志逐会话泛读词数 + 连续性/异常标记（教师端行内展示）。

        词数口径（防重复记录）：每本书维护「已读水位」=该生此前**带位置
        轨迹**的有效会话之有效区段最远终点（_valid_track_segments 口径，
        已剔跳章；无轨迹存量会话不推进水位——其限速折算终点只是记分
        口径不是真实读到的证据，会把大片区域误标「已读过」）。本次会话
        有效区段中，水位以下计入 overlap（重合，展示不计入学习词数），
        水位以上才是本次新学词数 words。回读整段在水位下 → words=0
        只显示重合；跳读空档不在会话区段内自然不计。
        - link：与同书上一会话（按开始时间）的衔接关系——
          next=顺延（|开头−上会话结尾|≤2%）、first=该书首个会话、
          back=回读（开头落后）、skip=跳读（开头超前，中间有断档）。
          开头取「恢复瞬态净化」后的稳定点（开局 3s 内 >4%/步的跳变
          是阅读器自愈落位，不是阅读）。
        - flags 异常标记：异常快=整段位移超 400 词/分物理读速（≥100 词才判，
          避免短会话小位移误报；含恢复瞬态的会话不判）；进度反向=结束位置
          低于开头 >1%；无有效阅读=闪进闪出/纯翻章会话（词数为 0）。
        位置口径（概念严格三分）：全书 % 仅是词位换算的内部中间量（s_pct/
        e_pct，前端仅兜底显示）；对外统计一律「当前阅读章节的章内 %」——
        chapters=[{ch 书内章号, s_in/e_in 章内起止%, words/overlap 该章
        新学/重合词数}]，新章开启=第N章 0%、续读=上次退出的章内位置。
        章号纯结构判定（首个 ≥200 词内容章起算第 1 章）；必读配置只定义
        计词范围（非必读章不列行不计词），不参与编号。
        返回 {会话id: {words, overlap, s_pct, e_pct, chapters[], link,
        link_note, flags[]}}。
        """
        out: dict[int, dict] = {}
        if not ids:
            return out
        rows: list = []
        hist: dict[tuple, list] = {}
        try:
            with self._connect() as conn:
                ph = ",".join("?" * len(ids))
                rows = conn.execute(
                    "SELECT id, user, started_at, book_id, active_sec,"
                    " percent_start, percent_end, lookups, plays,"
                    " scroll_count, chapter_navs, percent_track, created_at"
                    f" FROM focus_sessions WHERE id IN ({ph})"
                    " AND module = 'library'",
                    list(ids)).fetchall()
                # 每本书全历史时间线（水位/衔接基线要跨会话累计，批内 5-6 条
                # 不够）；只取批内涉及的书，通常 1-2 本。
                for r in rows:
                    if r["book_id"] is None:
                        continue
                    key = (r["user"], r["book_id"])
                    if key not in hist:
                        hist[key] = conn.execute(
                            "SELECT id, started_at, percent_start,"
                            " percent_end, active_sec, lookups, plays,"
                            " scroll_count, chapter_navs, percent_track,"
                            " created_at"
                            " FROM focus_sessions"
                            " WHERE user = ? AND module = 'library'"
                            " AND book_id = ?"
                            " ORDER BY started_at, id", key).fetchall()
        except sqlite3.Error:
            return out
        rows = [r for r in rows if r["book_id"] is not None]
        meta: dict[int, dict] = {}
        lib = self._lib_connect()
        if lib is not None:
            try:
                with contextlib.closing(lib):
                    meta = self._book_meta(lib, {r["book_id"] for r in rows})
            except sqlite3.Error:
                meta = {}
        # 时间线预演：逐会话两个基线（进入该会话前的状态）——
        #   prev_end=上一会话结束位置（衔接展示）；watermark=有效区段最远
        #   终点（词位置）。水位只由**带位置轨迹且口径未失真**的会话推进：
        #   无轨迹存量会话的限速折算终点只是记分口径、不是真实读到的证据；
        #   10-02~10-03 的「百分比口径失真」会话（前端分母 bug 期，详见
        #   _track_distorted）的 % 是章内值被当全书值，词位整体虚高。
        base: dict[int, dict] = {}
        for (user, bid), hs in hist.items():
            info = meta.get(bid)
            total = sum(w for _, w in info["chs"]) if info else 0
            prev_end = None
            watermark = 0.0
            for h in hs:
                base[h["id"]] = {"prev_end": prev_end, "watermark": watermark}
                if info and total > 0 and self._reading_session_valid(h):
                    tr = self._session_track(h)
                    if tr and not self._track_distorted(tr, h):
                        segs = self._valid_track_segments(
                            tr, total, h["active_sec"] or 0)
                        eff_end = max((e for _, e in segs), default=0.0)
                        watermark = max(watermark, eff_end)
                if h["percent_end"] is not None:
                    prev_end = h["percent_end"]
        for r in rows:
            info = meta.get(r["book_id"])
            total = sum(w for _, w in info["chs"]) if info else 0
            # 章号统一口径：优先取章节标题的字面数字（书自己印刷的
            # 「5 I Play Pinochle…」→ 第5章，与孩子看到的目录一致）；
            # 标题无数字（封面/目录/标题改造过的书）回落结构号——首个
            # 词数 ≥200 的内容章起算第 1 章。必读配置只管计词范围，
            # 绝不参与章号编号（改配置不漂移编号）。
            title_num: dict[int, int] = {}
            if info:
                for idx, t in (info.get("titles") or {}).items():
                    m = re.match(r"\s*(\d+)", str(t or ""))
                    if m:
                        title_num[idx] = int(m.group(1))
            first_content = 0
            if info:
                for idx, wc in info["chs"]:
                    if wc >= 200:
                        first_content = idx
                        break

            def disp_ch(idx: int) -> int:
                return title_num.get(idx) or max(1, idx - first_content + 1)
            valid = self._reading_session_valid(r)
            track = self._session_track(r)
            # 恢复瞬态净化：开局 3 秒内出现的大幅正向跳变（>4%/步）是
            # 阅读器「渲染开头→自愈滚到上次结尾」的落位动作（曾表现为
            # 「每次都从 5% 起读/回读 N%」假异常）。以瞬态后的稳定点为
            # 真实起点；无瞬态则用 percent_start。
            s0 = r["percent_start"] or 0
            e0 = r["percent_end"] or 0
            transient = False
            if track:
                prev = track[0]["p"]
                for pt in track[1:]:
                    if pt["t"] > 3:
                        break
                    if pt["p"] - prev > 4:
                        transient = True
                        s0 = max(s0, pt["p"])
                    prev = pt["p"]
            b = base.get(r["id"]) or {"prev_end": None, "watermark": 0.0}
            # lo/hi 必须先初始化：无效会话（闪进闪出/挂机）走不到下面分支，
            # 但 ch_rows 段引用它们——曾致 ids 只含这类会话时 500
            lo: list = []
            hi: list = []
            if valid and info and total > 0:
                segs = self._row_segs(r, total)
                wm = b["watermark"]
                for s_pos, e_pos in segs:
                    if e_pos <= wm:
                        lo.append((s_pos, e_pos))
                    elif s_pos >= wm:
                        hi.append((s_pos, e_pos))
                    else:
                        lo.append((s_pos, wm))
                        hi.append((wm, e_pos))
                overlap = self._segs_words(lo, info["chs"], info["required"])[0]
                words = self._segs_words(hi, info["chs"], info["required"])[0]
            else:
                words = overlap = 0
            flags: list[str] = []
            if track and self._track_distorted(track, r):
                flags.append("百分比口径失真（旧版分母bug，词数为封顶估算）")
            if not valid:
                # 纯翻章/闪进闪出：词数 0 已说明问题，不再叠加速度标记
                # （这类会话 active_sec 极小，raw 速度会是荒谬大数）。
                flags.append("无有效阅读")
            elif not words and not overlap and not track and e0 <= s0:
                # 「有效」但零产出：泛读僵尸会话（_zombie_reading 同口径）
                flags.append("无有效阅读")
            elif info and total > 0 and not transient:
                # 有效会话的「异常快」按原始位移速度判（词数口径已按
                # 400 词/分限速折算，被折算过的会话正是要暴露的对象）。
                # 含恢复瞬态的会话不判——瞬态位移是程序落位不是阅读。
                span_w = max(0.0, (e0 - s0) / 100 * total)
                mins = (r["active_sec"] or 0) / 60
                if (mins > 0 and span_w >= 100
                        and span_w / mins > self.READ_WPM_CAP):
                    flags.append(f"异常快 {round(span_w / mins)} 词/分")
            if e0 < s0 - 1:
                flags.append(f"进度反向 {round(s0 - e0)}%")
            link, note = "first", ""
            pe = b["prev_end"]
            if pe is not None:
                gap = s0 - pe
                if gap > 2:
                    link, note = "skip", f"跳读 {round(gap)}%"
                elif gap < -2:
                    link, note = "back", f"回读 {round(-gap)}%"
                else:
                    link, note = "next", ""

            # ---- 分章统计（统计口径=章内百分比）----
            # 全书 % 只是内部词位换算的中间量，对外一律按「当前阅读章节
            # 的章内 %」呈现：新章开启=第N章 0%，续读=上次退出的章内位置。
            # 逐有效章一行：章内起止% + 该章新学/重合词数（非必读章不列
            # 行——计词范围由配置定义，与章号编号相互独立）。前置页
            # （封面/目录）位置钳到第 1 章 0% 起。零位移（打开即退）也
            # 列位置行——教师能看到"停在第N章 X%"，词数自然为 0。
            ch_rows: list[dict] = []
            if info and total > 0 and e0 >= s0:
                first_pos = sum(w for i, w in info["chs"] if i < first_content)
                s_pos = max(s0 / 100 * total, first_pos)
                e_pos = max(e0 / 100 * total, s_pos)
                cum = 0
                for idx, wc in info["chs"]:
                    c1 = cum + wc
                    is_req = info["required"] is None or idx in info["required"]
                    if is_req and wc > 0 and c1 > s_pos and cum < e_pos:
                        a, b = max(cum, s_pos), min(c1, e_pos)
                        ch_rows.append({
                            "ch": disp_ch(idx),
                            "s_in": round((a - cum) / wc * 100),
                            "e_in": round((b - cum) / wc * 100),
                            "words": self._span_words(hi, cum, c1),
                            "overlap": self._span_words(lo, cum, c1),
                        })
                    cum = c1
            out[r["id"]] = {"words": words, "overlap": overlap,
                            "s_pct": round(s0), "e_pct": round(e0),
                            "chapters": ch_rows,
                            "link": link, "link_note": note, "flags": flags}
        return out


    @staticmethod
    def _span_words(segs: list, a: float, b: float) -> int:
        """词位区段集合与区间 [a,b) 的重叠词数（纯几何求和，不过滤章）。"""
        return int(round(sum(max(0.0, min(e, b) - max(s, a))
                             for s, e in segs)))


    # 阅读限速（词/分钟）：会话级封顶用（跳章剔除见 _valid_track_segments）
    READ_WPM_CAP = 400.0
    # 百分比口径失真期：前端精确百分比曾用滚动流高度当全书分母
    # （scrolled 大书懒渲染滚动流≈单章高，章内 % 被记成全书 %）。
    # 窗口收窄到「词位锚插值」实际部署时刻（2026-10-03T08:33Z，
    # add197b）——此后会话（含整数粒度的 944/945）数据已可信，
    # 不再标失真；窗口内带轨迹会话标失真：词数照算（限速封顶仍有
    # 意义）但不推进去重水位、不判衔接/异常。
    DISTORTED_UNTIL = "2026-10-03T08:33"

    @staticmethod
    def _track_distorted(track: list, r) -> bool:
        """会话是否落在百分比口径失真期（created_at 早于词位锚部署）。"""
        if not track:
            return False
        created = (r["created_at"] if "created_at" in r.keys()
                   else r["started_at"]) or ""
        return bool(created) and created < "2026-10-03T08:33"


    @staticmethod
    def _session_track(r) -> list[dict]:
        """会话行的 percent_track JSON → [{t, p}]（缺/坏返回 []）。
        兼容历史毫秒时间戳（会话上限 4h；末点 t>90000 必是 ms，整体换算秒）。"""
        raw = None
        try:
            raw = r["percent_track"]
        except (IndexError, KeyError):
            raw = None
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        out = []
        if isinstance(data, list):
            for x in data:
                try:
                    out.append({"t": float(x["t"]), "p": float(x["p"])})
                except (KeyError, TypeError, ValueError):
                    continue
        if out and out[-1]["t"] > 90000:
            out = [{"t": pt["t"] / 1000, "p": pt["p"]} for pt in out]
        return out


    def _valid_track_segments(self, track: list[dict], total_words: int,
                              active_sec: int = 0) -> list[tuple[float, float]]:
        """位置轨迹 → 有效阅读跨度（词位置区间，每会话一段）。双层判定：

        ①跳章剔除（位置级）：单步位移 >4%，或 <30 秒内 >2% —— 视为章导航/
          快滚位移，不计并触发 30s 冷却；回退不计。epubjs 位置为 1% 整数粒度
          （约 930 词/格），真实阅读的瞬时速度差异无法在粒度内分辨，故不做
          逐点限速（会误杀正常快读）。
        ②会话级封顶：有效跨度词数 ≤ 400 词/分 × active_sec——物理读速上限，
          连续快滚（每步都不触发跳章判定的匀速刷）在这里被整体压回。
        返回 [(起点词位, 终点词位)]；无有效增量返回 []。
        """
        if len(track) < 2:
            return []
        lo_p = hi_p = None
        cool_until = -1.0
        last_change_t = track[0]["t"]
        prev_p = track[0]["p"]
        for pt in track[1:]:
            if pt["t"] < last_change_t:  # 时钟回拨：重置锚点
                last_change_t = pt["t"]
                prev_p = pt["p"]
                continue
            if pt["p"] == prev_p:
                continue
            dpct = pt["p"] - prev_p
            if dpct > 0:
                dt = pt["t"] - last_change_t
                is_jump = dpct > 4 or (dt < 30 and dpct > 2)
                if not is_jump and pt["t"] >= cool_until:
                    lo_p = prev_p if lo_p is None else min(lo_p, prev_p)
                    hi_p = pt["p"] if hi_p is None else max(hi_p, pt["p"])
                elif is_jump:
                    cool_until = pt["t"] + 30
            last_change_t = pt["t"]
            prev_p = pt["p"]
        if lo_p is None or hi_p is None or hi_p <= lo_p:
            return []
        span = (hi_p - lo_p) / 100 * total_words
        cap = max(0, active_sec or 0) / 60 * self.READ_WPM_CAP
        if cap > 0:
            span = min(span, cap)
        s_pos = lo_p / 100 * total_words
        return [(s_pos, s_pos + span)]

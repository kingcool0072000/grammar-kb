"""泛读位置轨迹 → 词位区段（跳章剔除、读速封顶、失真窗口）。"""
from __future__ import annotations

import json


class FocusTrackMixin:
    """轨迹解析与词数几何。类属性供 PlanStore 上的 self.READ_WPM_CAP 查找。"""

    # 阅读限速（词/分钟）：会话级封顶用（跳章剔除见 _valid_track_segments）
    READ_WPM_CAP = 400.0
    # 百分比口径失真期：前端精确百分比曾用滚动流高度当全书分母
    # （scrolled 大书懒渲染滚动流≈单章高，章内 % 被记成全书 %）。
    # 窗口收窄到「词位锚插值」实际部署时刻（2026-10-03T08:33Z，
    # add197b）——此后会话（含整数粒度的 944/945）数据已可信，
    # 不再标失真；窗口内带轨迹会话标失真：词数照算（限速封顶仍有
    # 意义）但不推进去重水位、不判衔接/异常。
    DISTORTED_UNTIL = "2026-10-03T08:33"

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

    @staticmethod
    def _span_words(segs: list, a: float, b: float) -> int:
        """词位区段集合与区间 [a,b) 的重叠词数（纯几何求和，不过滤章）。"""
        return int(round(sum(max(0.0, min(e, b) - max(s, a))
                             for s, e in segs)))

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

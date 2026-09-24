// 专注力插件 · 阅读行为采集器（纯逻辑，无 UI）。
// 采集：切走（visibilitychange）、滚动（viewerEl scroll，500ms 节流）、
// 互动（查词/发音/预习/翻章/排版）、鼠标轨迹（主文档 readerRoot + epub iframe 双路）。
// 上报：60s 心跳 flush；destroy / beforeunload 强制 flush（卸载走 keepalive fetch 直发）。
// 原始事件只在内存，flush 时压缩成 mouse_metrics + polyline 才外传。

import { api, getAuth } from '../../api.js'

const SCROLL_THROTTLE_MS = 500 // 滚动计数节流
const HEARTBEAT_MS = 60000 // 心跳上报间隔
const IDLE_SPLIT_MS = 5 * 60 * 1000 // 超时不动即拆分会话（v2 需求①）
const FAST_WINDOW_MS = 10000 // 快滚判定窗口
const FAST_PERCENT_GAIN = 8 // 窗口内推进阈值（percent 为 0-100）
const FAST_COOLDOWN_MS = 30000 // 快滚触发后冷却
const POLYLINE_MIN_STEP_PX = 4 // polyline 相邻点最小位移（小于则丢弃）
const POLYLINE_MAX_POINTS = 3000 // polyline 点数上限（超出按 stride 抽稀）
const POLYLINE_MAX_CHARS = 200000 // polyline JSON 字符上限（超出对半抽稀）
const JITTER_SEG_PX = 6 // jitter 分段最小位移
const JITTER_COS_THRESHOLD = Math.cos((60 * Math.PI) / 180) // 相邻段夹角 >60° 记改向
const ACTIVE_BUCKET_MS = 30000 // active_ratio 的 30s 桶
const IDLE_GAP_MS = 5000 // idle_gaps 的空档阈值
const IDLE_GAPS_MAX = 20

/** 会话内固定 id：优先 crypto.randomUUID，降级 Math.random 拼接 */
function makeSessionId() {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  } catch {
    /* ignore */
  }
  return 'fs-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 8)
}

/** ISO 字符串截到秒（"2026-09-13T08:30:00Z"） */
function isoSec(ts) {
  return new Date(ts).toISOString().replace(/\.\d{3}Z$/, 'Z')
}

function dist(ax, ay, bx, by) {
  return Math.hypot(bx - ax, by - ay)
}

// ---------------------------------------------------------------------------
// 纯函数（导出供单元测试）：输入 [{x,y,t}]（t 为相对会话开始的 ms）
// ---------------------------------------------------------------------------

/**
 * 鼠标指标：总位移 / 活跃均速 / p95 速度 / 活跃桶占比 / 空闲空档 / 乱晃指数。
 * v3：可选 wheelSamples [{t 秒, dy}]（触控板上划/下滑 1s 桶）——纯触控板
 * 阅读（不动鼠标只滚动）时 active_ratio 以 wheel 桶补亮，避免被误判低活跃。
 */
export function computeMetrics(points, wheelSamples, wheelStats) {
  const n = points ? points.length : 0
  const wheel = (wheelSamples || []).filter((w) => w && Number.isFinite(w.t))
  if (!n && !wheel.length) {
    return { distance_px: 0, avg_speed: 0, p95_speed: 0, active_ratio: 0, idle_gaps: [], jitter: 0, points: 0, wheel: { up: 0, down: 0, buckets: 0 } }
  }
  if (!n) {
    // 纯 wheel 会话：距离/速度类全 0，活跃度只看 wheel 桶
    const t0 = wheel[0].t
    const span = Math.max(0, wheel[wheel.length - 1].t - t0)
    const totalBuckets = Math.max(1, Math.ceil((span + 1) / (ACTIVE_BUCKET_MS / 1000)))
    const up = wheel.filter((w) => w.dy < 0).length
    const down = wheel.filter((w) => w.dy > 0).length
    return {
      distance_px: 0, avg_speed: 0, p95_speed: 0,
      active_ratio: Math.round(Math.min(1, wheel.length / totalBuckets) * 1000) / 1000,
      idle_gaps: [], jitter: 0, points: 0,
      wheel: wheelStatOrSamples(wheelStats, wheel),
    }
  }
  let distance = 0 // 总位移
  let activeDist = 0 // 有移动段的位移
  let activeMs = 0 // 有移动段的时长
  const speeds = [] // 各段速度 px/s
  for (let i = 1; i < n; i++) {
    const a = points[i - 1]
    const b = points[i]
    const d = dist(a.x, a.y, b.x, b.y)
    const dt = Math.max(0, b.t - a.t)
    distance += d
    if (dt > 0 && d > 0.5) {
      speeds.push(d / (dt / 1000))
      activeDist += d
      activeMs += dt
    }
  }

  // p95 速度（排序取分位）
  let p95 = 0
  if (speeds.length) {
    const s = [...speeds].sort((x, y) => x - y)
    p95 = s[Math.min(s.length - 1, Math.floor(s.length * 0.95))]
  }

  // active_ratio：联合时间跨度（鼠标点 × wheel 桶，ms）切 30s 桶，
  // 有鼠标点【或 wheel 桶】占比——纯触控板时段既进分子也进分母
  const t0 = Math.min(points[0].t, wheel.length ? wheel[0].t * 1000 : Infinity)
  const t1 = Math.max(points[n - 1].t, wheel.length ? wheel[wheel.length - 1].t * 1000 : -Infinity)
  const span = Math.max(0, t1 - t0)
  const totalBuckets = Math.max(1, Math.ceil((span + 1) / ACTIVE_BUCKET_MS))
  const activeBuckets = new Set()
  for (const p of points) {
    activeBuckets.add(Math.floor((p.t - t0) / ACTIVE_BUCKET_MS))
  }
  for (const w of wheel) {
    activeBuckets.add(Math.floor((w.t * 1000 - t0) / ACTIVE_BUCKET_MS))
  }
  const activeRatio = Math.min(1, activeBuckets.size / totalBuckets)

  // idle_gaps：相邻点间隔 >5s 的空档 [起,止]（秒），截前 20 个
  const idleGaps = []
  for (let i = 1; i < n && idleGaps.length < IDLE_GAPS_MAX; i++) {
    const dt = points[i].t - points[i - 1].t
    if (dt > IDLE_GAP_MS) {
      idleGaps.push([Math.round(points[i - 1].t / 1000), Math.round(points[i].t / 1000)])
    }
  }

  // jitter：按 ≥6px 位移切段成方向向量，相邻段夹角 >60° 记一次改向；改向数/段数（0-1）
  const segs = []
  let ax = points[0].x
  let ay = points[0].y
  for (let i = 1; i < n; i++) {
    if (dist(ax, ay, points[i].x, points[i].y) >= JITTER_SEG_PX) {
      segs.push([points[i].x - ax, points[i].y - ay])
      ax = points[i].x
      ay = points[i].y
    }
  }
  let turns = 0
  for (let i = 1; i < segs.length; i++) {
    const [x1, y1] = segs[i - 1]
    const [x2, y2] = segs[i]
    const m1 = Math.hypot(x1, y1)
    const m2 = Math.hypot(x2, y2)
    if (m1 > 0 && m2 > 0) {
      const cos = (x1 * x2 + y1 * y2) / (m1 * m2)
      if (cos < JITTER_COS_THRESHOLD) turns += 1 // 夹角 >60° → cos < cos60°
    }
  }
  const jitter = segs.length >= 2 ? Math.min(1, Math.max(0, turns / segs.length)) : 0

  return {
    distance_px: Math.round(distance),
    avg_speed: activeMs > 0 ? Math.round((activeDist / (activeMs / 1000)) * 100) / 100 : 0,
    p95_speed: Math.round(p95 * 100) / 100,
    active_ratio: Math.round(activeRatio * 1000) / 1000,
    idle_gaps: idleGaps,
    jitter: Math.round(jitter * 1000) / 1000,
    points: n,
    // v3：触控板上划/下滑汇总（事件级计数优先，缺失时按桶推断）
    wheel: wheelStatOrSamples(wheelStats, wheel),
  }
}

/** wheel 汇总：有事件级计数（wheelStats）用它；否则按 1s 桶方向推断。 */
function wheelStatOrSamples(stats, wheel) {
  if (stats && (stats.up || stats.down || stats.dist)) {
    return { up: stats.up, down: stats.down, buckets: wheel.length }
  }
  return {
    up: wheel.filter((w) => w.dy < 0).length,
    down: wheel.filter((w) => w.dy > 0).length,
    buckets: wheel.length,
  }
}

/** 轨迹压缩成 polyline [[x,y,t],...]（t 取整秒）：<4px 合并、3000 点上限、JSON 200k 字符上限 */
export function compressMouse(points) {
  const src = points || []
  if (!src.length) return []
  // 最小位移增量合并
  let kept = [src[0]]
  for (let i = 1; i < src.length; i++) {
    const last = kept[kept.length - 1]
    if (dist(last.x, last.y, src[i].x, src[i].y) >= POLYLINE_MIN_STEP_PX) kept.push(src[i])
  }
  let out = kept.map((p) => [Math.round(p.x), Math.round(p.y), Math.round(p.t / 1000)])
  // 点数上限：按 stride 抽稀（保末点，末点信息最新）
  if (out.length > POLYLINE_MAX_POINTS) {
    const stride = Math.ceil(out.length / POLYLINE_MAX_POINTS)
    const last = out[out.length - 1]
    out = out.filter((_, i) => i % stride === 0)
    if (out[out.length - 1] !== last) out.push(last)
  }
  // JSON 字符上限：对半抽稀直至达标
  let json = JSON.stringify(out)
  while (json.length > POLYLINE_MAX_CHARS && out.length > 2) {
    const last = out[out.length - 1]
    out = out.filter((_, i) => i % 2 === 0)
    if (out[out.length - 1] !== last) out.push(last)
    const next = JSON.stringify(out)
    if (next.length >= json.length) break // 防御：长度不再下降时退出
    json = next
  }
  return out
}

// ---------------------------------------------------------------------------
// 采集器本体
// ---------------------------------------------------------------------------

/**
 * 创建专注力采集器。
 * @param {object} opts
 * @param {HTMLElement} opts.readerRoot 阅读器根容器（.lib-reader），鼠标轨迹主路
 * @param {HTMLElement} opts.viewerEl   #epub-viewer，滚动事件源
 * @param {number} opts.bookId
 * @param {string} opts.bookTitle
 * @param {boolean} opts.enabled false：不挂任何监听、flush 直接 return（教师端/开关关闭）
 * @returns {{ hooks, flush, destroy, disable, enable }}
 */
export function createFocusTracker({ readerRoot, viewerEl, bookId, bookTitle = '', enabled = true,
                                       module = 'library', initialRangeLabel = '', idleMs = IDLE_SPLIT_MS }) {
  // 会话可变状态（超时不活跃拆分时整体重置）
  let startedAt = Date.now()
  let sessionId = makeSessionId()
  // 创建时 enabled=false：tracker 是空壳（教师不追踪；开关关闭时由 reader 调 disable）
  let created = !!enabled
  let collecting = created
  let alive = true
  let stopped = false
  let intervalId = null
  let lastActiveAt = Date.now() // idle 拆分判定的活动时钟

  let st = null
  const newSt = () => ({
    awayCount: 0,
    awayMs: 0,
    longestAwayMs: 0,
    awayStart: 0, // hidden 起始的 Date.now()，0 表示当前可见
    scrollCount: 0,
    chapterNavs: 0,
    lookups: 0,
    plays: 0,
    prepOpens: 0,
    prepStarts: 0,
    percentStart: null,
    percentEnd: null,
    fastScrollFlags: 0,
  })
  st = newSt()
  // 内容漏斗（v2）：词 → 次数；selected 为「选了但没查没听」的词
  let lookupMap = new Map()
  let speakMap = new Map()
  let selectedMap = new Map()
  let activity = [] // {t 秒, type, w?} 内容互动时间线
  let curChapterLabel = ''
  let mouse = [] // {x,y,t}，t 相对会话开始 ms，不去抖不采样
  // 触控板 wheel 轨迹（v3）：上划/下滑计数 + 时序采样（1s 桶去抖）。
  // deltaY<0＝手指上划（看下面内容），>0＝手指下滑（回看上面内容）；
  // Mac 触控板自然滚动方向与此一致。持续小幅 wheel 是「在读」的重要信号。
  let wheelStats = { up: 0, down: 0, dist: 0 }
  let wheelSamples = [] // {t, dy} 1s 桶聚合，供曲线与 active_ratio
  let lastWheelBucket = -1
  let lastWheelAt = 0
  const scrollSamples = [] // {t, percent} 快滚判定滑动窗口
  let lastEngageAt = -Infinity // 最近一次查词/预习互动（快滚抑制）
  let lastFastAt = -Infinity // 最近一次快滚触发（冷却）
  let lastScrollAt = 0
  const boundIframeDocs = new WeakSet() // 幂等：已注入 mousemove 的 iframe document

  const relNow = () => Date.now() - startedAt
  const touchActive = () => { lastActiveAt = Date.now() }

  const wordsToArray = (m) => [...m.entries()].map(([w, n]) => [w, n]).sort((a, b) => b[1] - a[1])
  const pushActivity = (type, w) => {
    if (activity.length >= 500) return
    const rec = { t: Math.floor(relNow() / 1000), type }
    if (w) rec.w = String(w).slice(0, 60)
    activity.push(rec)
  }

  /**
   * 会话拆分（①：超 5 分钟不活跃）：结算当前段（ended_at 取最后活动时刻，
   * idle 时长不计入）并静默开启新会话。心跳/滚动/鼠标路径都会检查。
   */
  function checkIdleSplit() {
    if (!collecting || stopped) return
    if (document.visibilityState === 'hidden') return // 切走由 away 段管，回来 touchActive
    if (Date.now() - lastActiveAt <= idleMs) return
    // 结算旧段：截到最后活动时刻
    const endTs = lastActiveAt
    if (endTs > startedAt) {
      try { api.focusSubmit(buildRec(endTs)).catch(() => {}) } catch { /* ignore */ }
    }
    // 重置为新会话
    sessionId = makeSessionId()
    startedAt = Date.now()
    lastActiveAt = startedAt
    st = newSt()
    lookupMap = new Map(); speakMap = new Map(); selectedMap = new Map()
    activity = []
    mouse = []
    wheelStats = { up: 0, down: 0, dist: 0 }
    wheelSamples = []
    lastWheelBucket = -1
    lastWheelAt = 0
    scrollSamples.length = 0
    lastEngageAt = -Infinity; lastFastAt = -Infinity
  }

  function pushMouse(x, y) {
    mouse.push({ x: Math.round(x), y: Math.round(y), t: relNow() })
  }

  // ---- 切走（事件级，不轮询）----
  function onVisibilityChange() {
    if (!collecting) return
    if (document.visibilityState === 'hidden') {
      st.awayStart = Date.now()
    } else {
      touchActive() // 回到可见即活动（idle 时钟重置，切走期间不计 idle）
      if (st.awayStart) {
        const dur = Date.now() - st.awayStart
        st.awayMs += dur
        if (dur > st.longestAwayMs) st.longestAwayMs = dur
        st.awayCount += 1
        st.awayStart = 0
      }
    }
  }

  // ---- 滚动（500ms 节流）+ 快滚检测 ----
  function checkFastScroll() {
    const t = relNow()
    while (scrollSamples.length && t - scrollSamples[0].t > FAST_WINDOW_MS) scrollSamples.shift()
    if (!scrollSamples.length) return
    const first = scrollSamples[0]
    if (st.percentEnd == null || first.percent == null) return
    if (st.percentEnd - first.percent > FAST_PERCENT_GAIN) {
      const engaged = t - lastEngageAt <= FAST_WINDOW_MS // 窗口内有查词/预习互动 → 不算快滚
      if (!engaged && t - lastFastAt > FAST_COOLDOWN_MS) {
        st.fastScrollFlags += 1
        lastFastAt = t
        scrollSamples.length = 0 // 触发后清窗口，同一段连续快滚不重复计数
      }
    }
  }

  function onScroll() {
    if (!collecting) return
    checkIdleSplit() // 滚动是活动：先看有没有该结算的 idle 段
    const now = Date.now()
    if (now - lastScrollAt < SCROLL_THROTTLE_MS) return
    lastScrollAt = now
    touchActive()
    st.scrollCount += 1
    scrollSamples.push({ t: relNow(), percent: st.percentEnd })
    checkFastScroll()
  }

  // ---- 触控板 wheel（上划/下滑）轨迹 ----
  function onWheel(e) {
    if (!collecting) return
    const dy = Number(e.deltaY) || 0
    if (!dy) return
    checkIdleSplit()
    touchActive()
    const now = Date.now()
    if (now - lastWheelAt < 100) return // 100ms 去抖（触控板惯性事件极密）
    lastWheelAt = now
    if (dy < 0) wheelStats.up += 1
    else wheelStats.down += 1
    wheelStats.dist += Math.min(500, Math.abs(dy))
    // 1s 桶聚合（带方向净量），供 active_ratio 与曲线
    const bucket = Math.floor(relNow() / 1000)
    if (bucket !== lastWheelBucket) {
      lastWheelBucket = bucket
      wheelSamples.push({ t: bucket, dy: 0 })
      if (wheelSamples.length > 3000) wheelSamples.shift()
    }
    wheelSamples[wheelSamples.length - 1].dy += dy
  }

  // ---- 主文档鼠标轨迹 ----
  function onRootMouseMove(e) {
    if (!collecting) return
    checkIdleSplit()
    touchActive()
    try {
      const r = readerRoot.getBoundingClientRect()
      pushMouse(e.clientX - r.left, e.clientY - r.top)
    } catch {
      /* ignore */
    }
  }

  // ---- 卸载兜底：keepalive 直发（api.focusSubmit 无 keepalive，卸载场景自己 fetch）----
  function onBeforeUnload() {
    if (!alive || !created || stopped) return
    try {
      const headers = { 'Content-Type': 'application/json' }
      const a = getAuth()
      if (a && a.token) headers.Authorization = `Bearer ${a.token}`
      fetch('/api/focus/sessions', {
        method: 'POST',
        headers,
        body: JSON.stringify(buildRec(Date.now())),
        keepalive: true,
      }).catch(() => {})
    } catch {
      /* ignore */
    }
  }

  function attach() {
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('beforeunload', onBeforeUnload)
    if (viewerEl) viewerEl.addEventListener('scroll', onScroll, { passive: true })
    if (readerRoot) readerRoot.addEventListener('mousemove', onRootMouseMove, { passive: true })
    if (readerRoot) readerRoot.addEventListener('wheel', onWheel, { passive: true })
    intervalId = setInterval(() => {
      if (alive && !stopped) {
        checkIdleSplit()
        flush(false)
      }
    }, HEARTBEAT_MS)
  }

  function detach() {
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
    if (viewerEl) viewerEl.removeEventListener('scroll', onScroll)
    if (readerRoot) readerRoot.removeEventListener('mousemove', onRootMouseMove)
    if (readerRoot) readerRoot.removeEventListener('wheel', onWheel)
    if (intervalId) {
      clearInterval(intervalId)
      intervalId = null
    }
  }

  // ---- rec 构造（契约字段，勿改名）----
  function buildRec(ts) {
    const totalMs = Math.max(0, ts - startedAt)
    const awayMs = st.awayMs + (st.awayStart ? ts - st.awayStart : 0)
    const activeMs = Math.max(0, totalMs - awayMs)
    return {
      session_id: sessionId,
      book_id: bookId,
      book_title: bookTitle || '',
      started_at: isoSec(startedAt),
      ended_at: isoSec(ts),
      total_sec: Math.round(totalMs / 1000),
      active_sec: Math.round(activeMs / 1000),
      away_count: st.awayCount + (st.awayStart ? 1 : 0),
      away_sec: Math.round(awayMs / 1000),
      longest_away_sec: Math.round(st.longestAwayMs / 1000),
      scroll_count: st.scrollCount,
      chapter_navs: st.chapterNavs,
      lookups: st.lookups,
      plays: st.plays,
      prep_opens: st.prepOpens,
      prep_starts: st.prepStarts,
      percent_start: st.percentStart == null ? 0 : Math.round(st.percentStart * 100) / 100,
      percent_end: st.percentEnd == null ? 0 : Math.round(st.percentEnd * 100) / 100,
      fast_scroll_flags: st.fastScrollFlags,
      mouse_metrics: computeMetrics(mouse, wheelSamples, wheelStats),
      // 后端契约：polyline 为字符串（max_length 200k）；空轨迹给空串
      polyline: mouse.length ? JSON.stringify(compressMouse(mouse)) : '',
      // v2：内容监控 + 学习范围
      module,
      range_start: st.percentStart == null ? null : Math.round(st.percentStart * 100) / 100,
      range_end: st.percentEnd == null ? null : Math.round(st.percentEnd * 100) / 100,
      range_label: (curChapterLabel || initialRangeLabel || '').slice(0, 120),
      lookup_words: wordsToArray(lookupMap),
      speak_words: wordsToArray(speakMap),
      selected_words: wordsToArray(selectedMap),
      activity,
    }
  }

  /**
   * 上报当前会话（心跳也走这里）。静默失败。
   * created=false 时直接 return（教师/开关关闭，双保险之一）。
   */
  function flush() {
    if (!alive || !created || stopped) return
    try {
      api.focusSubmit(buildRec(Date.now())).catch(() => {})
    } catch {
      /* ignore */
    }
  }

  /** 教师关闭专注采集：停止采集、补发一次最终 rec、清空内存 */
  function disable() {
    if (!alive || !created || stopped) return
    stopped = true
    try {
      api.focusSubmit(buildRec(Date.now())).catch(() => {})
    } catch {
      /* ignore */
    }
    collecting = false
    detach()
    mouse.length = 0
    wheelSamples.length = 0
    scrollSamples.length = 0
  }

  /** 重新开启（disable 后调用）：重挂监听、重新计数从当下起 */
  function enable() {
    if (!alive || !created || !stopped) return
    stopped = false
    collecting = true
    lastEngageAt = -Infinity
    lastFastAt = -Infinity
    attach()
  }

  /** 移除全部监听并终止（reader cleanup 调用；未 disable 过则补发最终 rec） */
  function destroy() {
    if (!alive) return
    if (created && !stopped) {
      try {
        api.focusSubmit(buildRec(Date.now())).catch(() => {})
      } catch {
        /* ignore */
      }
    }
    alive = false
    collecting = false
    detach()
    mouse.length = 0
    wheelSamples.length = 0
    scrollSamples.length = 0
  }

  if (created) attach()

  return {
    hooks: {
      /** 划词选中（lookup 层工具条弹出即报；查/听之后会从中移除——漏斗上层） */
      onSelect(word) {
        if (!collecting) return
        touchActive()
        const w = String(word || '').trim()
        if (!w) return
        selectedMap.set(w, (selectedMap.get(w) || 0) + 1)
        pushActivity('select', w)
      },
      /** 查词（lookup 层 showDict 漏斗调用，带词） */
      onLookup(word) {
        if (!collecting) return
        touchActive()
        st.lookups += 1
        lastEngageAt = relNow()
        const w = String(word || '').trim()
        if (!w) return
        lookupMap.set(w, (lookupMap.get(w) || 0) + 1)
        selectedMap.delete(w) // 已进入下层，不算「选了没查」
        pushActivity('lookup', w)
      },
      /** 发音（工具条/查词浮层发音钮，带词） */
      onSpeak(word) {
        if (!collecting) return
        touchActive()
        st.plays += 1
        const w = String(word || '').trim()
        if (!w) return
        speakMap.set(w, (speakMap.get(w) || 0) + 1)
        selectedMap.delete(w)
        pushActivity('speak', w)
      },
      /** 录音开始/结束（阅读练习精读场景） */
      onRecord() {
        if (!collecting) return
        touchActive()
        pushActivity('record')
      },
      /** 打开预习抽屉 */
      onPrepOpen() {
        if (!collecting) return
        touchActive()
        st.prepOpens += 1
        lastEngageAt = relNow()
        pushActivity('prep')
      },
      /** 预习开始 */
      onPrepStart() {
        if (!collecting) return
        touchActive()
        st.prepStarts += 1
        lastEngageAt = relNow()
      },
      /** 章级导航 */
      onChapterNav() {
        if (!collecting) return
        touchActive()
        st.chapterNavs += 1
        pushActivity('chapter')
      },
      /** 排版调整（后端契约暂无对应字段，占位保接线完整） */
      onThemeAdjust() {
        if (!collecting) return
      },
      /** 阅读进度（handleReloc 调用，percent 0-100；label 为章标题/范围标签） */
      onReloc(percent, label) {
        if (!collecting) return
        const p = Number(percent)
        if (Number.isFinite(p)) {
          if (st.percentStart == null) st.percentStart = p
          st.percentEnd = p
        }
        if (label) curChapterLabel = String(label).slice(0, 120)
      },
      /**
       * iframe 正文鼠标轨迹（reader 的 onRendered 通道调用，幂等防重复挂）。
       * rendition destroy 时 iframe 连同监听一起销毁，无需手动清理。
       */
      attachIframeDoc(doc) {
        if (!collecting || !doc || boundIframeDocs.has(doc)) return
        try {
          const win = doc.defaultView
          const iframe = win && win.frameElement
          if (!iframe) return
          boundIframeDocs.add(doc)
          doc.addEventListener('wheel', (e) => {
            if (!collecting) return
            // iframe wheel 先冒泡前的上下文：交给统一处理器（方向/去抖/采样）
            onWheel(e)
          }, { passive: true })
          doc.addEventListener('mousemove', (e) => {
            if (!collecting) return
            checkIdleSplit()
            touchActive()
            try {
              const rootRect = readerRoot.getBoundingClientRect()
              const ifRect = iframe.getBoundingClientRect()
              pushMouse(e.clientX + ifRect.left - rootRect.left, e.clientY + ifRect.top - rootRect.top)
            } catch {
              /* ignore */
            }
          })
        } catch {
          /* ignore */
        }
      },
    },
    flush,
    destroy,
    disable,
    enable,
  }
}

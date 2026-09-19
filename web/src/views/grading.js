import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 批改中心（首页）：学生提交的作业按三大板块归类——
//   FCE 听说读写（朗读录音批改 / 作文批改 / 练习明细）
//   哈1 语法（哈一作业成绩 / 背单词记录）
//   专注力 · 泛读阅读（泛读馆阅读器行为采集，点会话行看详情弹窗）
// 每个分区：待办数字 + 最近动态 + 一键跳到对应工具。
export async function mountGrading(el) {
  el.innerHTML = '<div class="view-head"><h1>批改中心</h1><p>加载中…</p></div>'
  let recs, fceSubs, recite, exams, focus
  try {
    ;[recs, fceSubs, recite, exams, focus] = await Promise.all([
      api.readingRecordings({ limit: 200 }),
      api.fceSubmissions({ limit: 200 }),
      api.reciteSessions({ limit: 100 }),
      api.examsList(),
      // 专注力后端并行开发中，失败不拖垮整页
      api.focusSessions({ limit: 50 }).catch(() => []),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }

  // ---- 待办汇总 ----
  const pendRec = recs.filter((r) => r.status === 'pending')
  const pendEssay = fceSubs.filter((s) => s.status === 'pending')
  const today = new Date().toISOString().slice(0, 10)
  const reciteToday = recite.filter((s) => (s.created_at || '').slice(0, 10) === today)
  const latestExam = exams[0]
  const totalPend = pendRec.length + pendEssay.length

  // ---- 专注力 · 近 7 天汇总（统计卡 + 板块汇总行共用）----
  const weekMs = 7 * 24 * 3600 * 1000
  const nowMs = Date.now()
  focus = Array.isArray(focus) ? focus : []
  const focusWeek = focus.filter(
    (s) => s && new Date(s.started_at || s.created_at || 0).getTime() >= nowMs - weekMs,
  )
  const weekScores = focusWeek.filter((s) => typeof s.score === 'number')
  const focusWeekAvg = weekScores.length
    ? Math.round(weekScores.reduce((a, s) => a + s.score, 0) / weekScores.length)
    : null
  const focusWeekMin = Math.round(focusWeek.reduce((a, s) => a + (s.active_sec || 0), 0) / 60)
  // 展示用：按开始时间倒序
  const focusRecent = [...focus]
    .filter((s) => s && s.id !== undefined)
    .sort((a, b) => String(b.started_at || b.created_at || '').localeCompare(String(a.started_at || a.created_at || '')))

  el.innerHTML = `
    <div class="view-head">
      <h1>批改中心</h1>
      <p>学生作业的待批与动态汇总。${totalPend ? `<b class="gd-pend-badge">${totalPend} 项待批</b>` : '当前没有待批项 ✅'}</p>
    </div>

    <div class="grading-stats">
      <div class="grading-stat ${pendRec.length ? 'warn' : ''}" data-go="recording" title="FCE 口语朗读录音批改">
        <b>${pendRec.length}</b><span>FCE 朗读录音待批</span>
      </div>
      <div class="grading-stat ${pendEssay.length ? 'warn' : ''}" data-go="essay" title="FCE 写作批改">
        <b>${pendEssay.length}</b><span>FCE 作文待批</span>
      </div>
      <div class="grading-stat" data-go="haya-exam" title="哈一作业成绩管理">
        <b>${latestExam ? latestExam.score : '—'}</b><span>最近哈一成绩${latestExam ? `（第${latestExam.lecture}讲）` : ''}</span>
      </div>
      <div class="grading-stat" data-go="recite" title="背单词练习记录">
        <b>${reciteToday.length}</b><span>今日背单词（组）</span>
      </div>
      <div class="grading-stat gd-focus-stat" title="泛读馆阅读专注评分 · 近 7 天会话平均">
        <b>${focusWeekAvg ?? '—'}</b><span>本周专注均分${focusWeek.length ? `（${focusWeek.length} 次）` : ''}</span>
      </div>
    </div>

    <div class="gd-board">
      <!-- ================= FCE 听说读写 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎧 FCE 听说读写</h2>
          <nav>
            <button class="reading-btn small primary" data-go="recording">🎙 朗读录音${pendRec.length ? `（${pendRec.length}）` : ''}</button>
            <button class="reading-btn small primary" data-go="essay">✍️ 作文${pendEssay.length ? `（${pendEssay.length}）` : ''}</button>
            <button class="reading-btn small" data-go="fce-subs">📋 练习明细</button>
          </nav>
        </header>
        <div class="gd-subgroup">
          <h3>🎙 朗读录音${pendRec.length ? ` · 待批 ${pendRec.length}` : ''}</h3>
          ${recs.slice(0, 5).map((r) => recRow(r)).join('') || '<p class="reading-hint">暂无录音提交</p>'}
        </div>
        <div class="gd-subgroup">
          <h3>✍️ 作文与练习</h3>
          ${fceSubs.slice(0, 5).map((s) => fceRow(s)).join('') || '<p class="reading-hint">暂无 FCE 练习</p>'}
        </div>
      </section>

      <!-- ================= 哈1 语法 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📚 哈1 语法</h2>
          <nav>
            <button class="reading-btn small primary" data-go="haya-exam">📊 哈一作业成绩</button>
            <button class="reading-btn small" data-go="recite">📖 背单词记录</button>
          </nav>
        </header>
        <div class="gd-subgroup">
          <h3>📊 哈一作业成绩 · 最近</h3>
          ${exams.slice(0, 5).map((e) => `
            <div class="fce-his-row">
              <span class="fce-his-what">第 ${e.lecture} 讲</span>
              <b class="fce-his-score ${e.score >= 90 ? 'ok' : e.score >= 70 ? '' : 'bad'}">${e.score}</b>
              <span class="fce-his-date">${e.date}${e.wrong && e.wrong.length ? ` · 错 ${e.wrong.length} 题` : ''}</span>
            </div>`).join('') || '<p class="reading-hint">暂无成绩</p>'}
        </div>
        <div class="gd-subgroup">
          <h3>📖 背单词 · 最近练习</h3>
          ${recite.slice(0, 5).map((s) => `
            <div class="fce-his-row">
              <span class="fce-his-what">${escapeHtml(s.user)} · ${scopeCn(s.scope)}${s.mode === 'flip' ? '（自评）' : ''}</span>
              <b class="fce-his-score ${s.acc >= 80 ? 'ok' : ''}">${s.acc}%</b>
              <span class="fce-his-date">${s.total} 词 · 错 ${s.wrong} · ${fmtDur(s.duration_sec || 0)} · ${(s.created_at || '').slice(5, 10)}</span>
            </div>`).join('') || '<p class="reading-hint">暂无背单词记录</p>'}
          ${recite.length ? `<p class="reading-hint">易错词（最近）：${topWrongWords(recite)}</p>` : ''}
        </div>
      </section>

      <!-- ================= 专注力 · 泛读阅读 ================= -->
      <section class="gd-section gd-focus-section">
        <header class="gd-section-head">
          <h2>👁 专注力 · 泛读阅读</h2>
          <nav><span class="gd-focus-head-hint">点会话查看专注详情</span></nav>
        </header>
        <div class="gd-subgroup">
          <h3>📖 阅读会话 · 最近</h3>
          ${focusRecent.length
            ? focusRecent.slice(0, 8).map((s) => focusRow(s)).join('')
            : '<p class="reading-hint">暂无阅读专注数据——学生在泛读馆读书后自动生成</p>'}
          ${focusRecent.length
            ? `<p class="reading-hint">近 7 天 ${focusWeek.length} 次会话 · 平均 ${focusWeekAvg ?? '—'} 分 · 累计专注 ${focusWeekMin} 分钟</p>`
            : ''}
        </div>
      </section>
    </div>
  `

  el.querySelectorAll('[data-go]').forEach((b) => {
    b.addEventListener('click', () => go(b.dataset.go))
  })

  // 专注会话行 → 详情弹窗
  el.querySelectorAll('[data-focus-id]').forEach((row) => {
    row.addEventListener('click', () => openFocusDetail(Number(row.dataset.focusId)))
  })
}

// 专注会话行（fce-his-row 风格，可点开详情）
function focusRow(s) {
  const score = typeof s.score === 'number' ? Math.round(s.score) : null
  const scoreCls = score === null ? 'pend' : score >= 80 ? 'ok' : score >= 60 ? '' : 'bad'
  const mod = s.module === 'reading' ? '精读' : '泛读'
  const modCls = s.module === 'reading' ? 'gd-focus-mod reading' : 'gd-focus-mod'
  const chips = [
    `<i class="${modCls}">${mod}</i>`,
    `<i class="gd-focus-chip">查词 ${s.lookups || 0}</i>`,
    `<i class="gd-focus-chip">发音 ${s.plays || 0}</i>`,
    `<i class="gd-focus-chip">离开 ${s.away_count || 0} 次</i>`,
    ...(s.fast_scroll_flags ? [`<i class="gd-focus-chip">快滚 ${s.fast_scroll_flags}</i>`] : []),
  ].join('')
  const rangeTip = s.range_label ? ` · ${escapeHtml(s.range_label)}` : ''
  return `
    <div class="fce-his-row gd-focus-row" data-focus-id="${s.id}" title="点开专注详情${rangeTip}">
      <span class="fce-his-what">${escapeHtml(s.book_title || `#${s.book_id}`)}</span>
      <b class="fce-his-score ${scoreCls}">${score === null ? '—' : score}</b>
      <span class="fce-his-date">${fmtDur(s.total_sec || 0)} · ${fmtCnTime(s.started_at || s.created_at)}</span>
      <span class="gd-focus-chips">${chips}</span>
    </div>`
}

/** 学习范围卡：模块徽标 + 范围标签 + 本段进度条 */
function focusRangeCard(s) {
  const mod = s.module === 'reading' ? '精读' : '泛读'
  const modCls = s.module === 'reading' ? 'gd-focus-mod reading' : 'gd-focus-mod'
  const label = s.range_label || s.book_title || ''
  const rs = Number.isFinite(Number(s.range_start)) ? Number(s.range_start) : 0
  const re = Number.isFinite(Number(s.range_end)) ? Number(s.range_end) : 0
  const left = Math.min(100, Math.max(0, rs))
  const width = Math.max(0, Math.min(100 - left, re - left))
  return `
  <div class="gd-focus-range">
    <div class="gd-focus-range-head">
      <i class="${modCls}">${mod}</i>
      <span class="gd-focus-range-label">${escapeHtml(label)}</span>
      <span class="gd-focus-range-pct">${left.toFixed(0)}% → ${re.toFixed(0)}%</span>
    </div>
    <div class="gd-focus-range-track"><i style="left:${left}%;width:${width}%"></i></div>
  </div>`
}

/** 词汇互动卡：漏斗统计 + 三组词 chips（v2 数据缺省时回落计数字段） */
function focusWordsCard(s) {
  const lw = Array.isArray(s.lookup_words) ? s.lookup_words : []
  const sw = Array.isArray(s.speak_words) ? s.speak_words : []
  const selw = Array.isArray(s.selected_words) ? s.selected_words : []
  const hasDetail = lw.length || sw.length || selw.length
  const nLook = hasDetail ? new Set(lw.map((x) => x.w)).size : (s.lookups || 0)
  const nSpeak = hasDetail ? new Set(sw.map((x) => x.w)).size : (s.plays || 0)
  const nSel = hasDetail ? new Set(selw.map((x) => x.w)).size : 0
  if (!nLook && !nSpeak && !nSel) {
    return `<div class="gd-focus-words"><div class="gd-focus-funnel">本会话无词汇互动</div></div>`
  }
  const group = (title, arr) => {
    if (!arr.length) return ''
    const sup = (n) => (n > 1 ? `<sup>${n > 99 ? '99+' : n}</sup>` : '')
    const shown = arr.slice(0, 12).map((x) => `<i class="gd-focus-word">${escapeHtml(String(x.w))}${sup(x.n)}</i>`).join('')
    const more = arr.length > 12 ? `<em>+${arr.length - 12}</em>` : ''
    return `<div class="gd-focus-word-group"><span class="gd-focus-word-label">${title}</span><span class="gd-focus-word-list">${shown}${more}</span></div>`
  }
  return `
  <div class="gd-focus-words">
    <div class="gd-focus-funnel">选中 <b>${nSel + nLook}</b> → 查词 <b>${nLook}</b> → 发音 <b>${nSpeak}</b>${hasDetail ? '' : ' <span class="gd-focus-funnel-old">（旧版会话：按次计）</span>'}</div>
    ${hasDetail ? group('查词', lw) + group('发音', sw) + group('选中未查', selw) : ''}
  </div>`
}

// ---------- 专注会话详情弹窗 ----------
async function openFocusDetail(id) {
  let s
  try {
    s = await api.focusSession(id)
  } catch (err) {
    alert(`加载专注详情失败：${err.message}`)
    return
  }
  if (!s || typeof s !== 'object') {
    alert('该专注会话不存在或已删除')
    return
  }
  closeFocusDetail() // 防重复叠加

  const score = typeof s.score === 'number' ? Math.round(s.score) : null
  const scoreCls = score === null ? '' : score >= 80 ? 'ok' : score >= 60 ? '' : 'bad'
  const sd = s.score_detail || {}
  const pct = (v) => {
    const n = Number(v)
    return Number.isFinite(n) ? Math.round(Math.min(1, Math.max(0, n)) * 100) : 0
  }
  // 三项正向（绿色，0-1 归一）+ 两项惩罚（橙色，按各自上限归一：离开≤12、乱晃≤5，
  // 显示绝对扣分值——满条=扣满上限，避免「惩罚 3 分显示 100%」的语义误导）
  const bars = [
    ['有效时长占比', pct(sd.effective_ratio), false, null],
    ['内容互动', pct(sd.engagement), false, null],
    ['阅读进度', pct(sd.progress), false, null],
    ['离开惩罚', pct((Number(sd.away_penalty) || 0) / 12), true, `-${(Number(sd.away_penalty) || 0).toFixed(1)}`],
    ['乱晃惩罚', pct((Number(sd.jitter_penalty) || 0) / 5), true, `-${(Number(sd.jitter_penalty) || 0).toFixed(1)}`],
    ['发呆惩罚', pct((Number(sd.idle_penalty) || 0) / 8), true, `-${(Number(sd.idle_penalty) || 0).toFixed(1)}`],
  ]
    .map(([name, p, neg, valText]) => `
      <div class="gd-focus-bar">
        <span class="gd-focus-bar-label">${name}</span>
        <span class="gd-focus-bar-track"><i class="${neg ? 'neg' : 'pos'}" style="width:${p}%"></i></span>
        <span class="gd-focus-bar-val">${valText || p + '%'}</span>
      </div>`)
    .join('')

  const startCn = fmtCnTime(s.started_at || s.created_at)
  const endCn = fmtCnTime(s.ended_at, false)
  const activePct = s.total_sec ? Math.round(((s.active_sec || 0) / s.total_sec) * 100) : 0
  const awayMin = ((s.away_sec || 0) / 60).toFixed(1).replace(/\.0$/, '')

  const mm = s.mouse_metrics || {}
  const fmtPx = (v) => {
    const n = Number(v)
    if (!Number.isFinite(n)) return '—'
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k px` : `${Math.round(n)} px`
  }
  const fmtSpeed = (v) => {
    const n = Number(v)
    return Number.isFinite(n) ? `${fmtPx(n)}/s` : '—'
  }
  const jit = Number(mm.jitter)
  const jitterOk = Number.isFinite(jit)
  const jitterHigh = jitterOk && jit > 0.6
  const metricCards = [
    ['总位移', fmtPx(mm.distance_px), false],
    ['平均速度', fmtSpeed(mm.avg_speed), false],
    ['P95 速度', fmtSpeed(mm.p95_speed), false],
    [
      '活跃占比',
      Number.isFinite(Number(mm.active_ratio)) ? `${Math.round(mm.active_ratio * 100)}%` : '—',
      false,
    ],
    ['乱晃指数', jitterOk ? (jitterHigh ? '偏高' : '正常') : '—', jitterHigh, jitterOk ? jit.toFixed(2) : ''],
    ['静止空档', `${(Array.isArray(mm.idle_gaps) ? mm.idle_gaps : []).length} 段`, false],
  ]
    .map(
      ([name, val, warn, sub]) => `
      <div class="gd-focus-mm ${warn ? 'warn' : ''}">
        <span>${name}</span><b>${val}${sub ? ` <i>${sub}</i>` : ''}</b>
      </div>`,
    )
    .join('')

  const overlay = document.createElement('div')
  overlay.className = 'gd-focus-detail'
  overlay.innerHTML = `
    <div class="gd-focus-detail-mask" data-close></div>
    <div class="gd-focus-detail-card">
      <div class="gd-focus-detail-head">
        <div class="gd-focus-detail-title">
          <b>${escapeHtml(s.book_title || `#${s.book_id}`)}</b>
          <span>${escapeHtml(s.user || '')} · ${startCn}${endCn ? ` → ${endCn}` : ''}</span>
        </div>
        <button class="reading-btn small" data-close>关闭</button>
      </div>
      <div class="gd-focus-detail-body">
        <p class="gd-focus-dur">总时长 <b>${fmtDur(s.total_sec || 0)}</b> · 有效阅读 <b>${fmtDur(s.active_sec || 0)}</b>（${activePct}%） · 滚动 ${s.scroll_count || 0} 次 · 章节跳转 ${s.chapter_navs || 0} 次</p>
        ${focusRangeCard(s)}
        ${focusWordsCard(s)}
        <div class="gd-focus-score-wrap">
          <div class="gd-focus-score-bars">${bars}</div>
          <div class="gd-focus-score-total">
            <b class="${scoreCls}">${score === null ? '—' : score}</b>
            <span>专注评分</span>
          </div>
        </div>
        <div class="gd-focus-chart-block">
          <div class="gd-focus-chart-title">鼠标活动强度 <span>（10 秒聚合 · 对数归一）</span></div>
          <canvas class="gd-focus-canvas"></canvas>
          <p class="gd-focus-chart-note">离开 ${s.away_count || 0} 次共 ${awayMin} 分钟 · 最长单次离开 ${fmtDur(s.longest_away_sec || 0)}</p>
        </div>
        <div class="gd-focus-mms">${metricCards}</div>
        <div class="gd-focus-trail-block">
          <div class="gd-focus-chart-title">鼠标轨迹缩略图 <span>（{PTS} 个点 · <i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#22a06b;vertical-align:-1px"></i> 起点 <i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#b42318;vertical-align:-1px"></i> 终点）</span></div>
          <canvas class="gd-focus-trail" width="200" height="150"></canvas>
        </div>
      </div>
    </div>`
  document.body.append(overlay)

  // Esc 关闭（仅本弹窗存活期间监听）
  const onKey = (e) => {
    if (e.key === 'Escape') closeFocusDetail()
  }
  document.addEventListener('keydown', onKey)
  overlay._onKey = onKey
  overlay.querySelectorAll('[data-close]').forEach((x) =>
    x.addEventListener('click', closeFocusDetail),
  )

  drawFocusIntensity(overlay.querySelector('.gd-focus-canvas'), s)
  const polyPts = Array.isArray(s.polyline) ? s.polyline.length : 0
  const tEl = overlay.querySelector('.gd-focus-trail-block .gd-focus-chart-title span')
  if (tEl) tEl.innerHTML = tEl.innerHTML.replace('{PTS}', String(polyPts))
  drawFocusTrail(overlay.querySelector('.gd-focus-trail'), s)
}

function closeFocusDetail() {
  const overlay = document.querySelector('.gd-focus-detail')
  if (!overlay) return
  if (overlay._onKey) document.removeEventListener('keydown', overlay._onKey)
  overlay.remove()
}

// 时间线：把 polyline 的 t 聚合到 10s 桶（桶内移动距离→强度，log 归一），面积图填充
/** ISO 时间（UTC 带 Z / 本地串）→ 北京时间「MM-DD HH:mm」。
 *  采集端 isoSec() 生成 UTC 字符串，直接 slice 会早 8 小时——必须经 Date 转换。
 *  无 Z 后缀的裸串（历史数据/本地时钟）按已是中国时间直解。 */
function fmtCnTime(iso, withDate = true) {
  if (!iso) return '—'
  const raw = String(iso)
  const d = new Date(/[Z+]/.test(raw.slice(-6)) ? raw : raw + '+08:00')
  if (isNaN(d.getTime())) return raw.slice(0, 16).replace('T', ' ')
  const p = (n) => String(n).padStart(2, '0')
  const hm = `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  return withDate ? hm : hm.slice(6)
}

/** 秒 → m:ss / mm:ss 刻度文字 */
function fmtMin(sec) {
  const m = Math.floor(sec / 60)
  const r = Math.round(sec % 60)
  return m === 0 ? `${r}s` : `${m}:${String(r).padStart(2, '0')}`
}

function drawFocusIntensity(canvas, session) {
  if (!canvas) return
  const poly = Array.isArray(session.polyline) ? session.polyline : []
  const total = Math.max(1, Math.floor(session.total_sec || 0))
  const dpr = window.devicePixelRatio || 1
  const w = Math.max(200, canvas.clientWidth || 560)
  const h = 120
  canvas.width = Math.round(w * dpr)
  canvas.height = Math.round(h * dpr)
  canvas.style.height = `${h}px`
  const ctx = canvas.getContext('2d')
  ctx.scale(dpr, dpr)
  const padB = 12
  const plotH = h - padB - 8
  // 背景网格：每 5 分钟一条 + 刻度标签（60s 线太密读成纹理——走查 M1）
  ctx.strokeStyle = 'rgba(138,109,59,.16)'
  ctx.lineWidth = 1
  ctx.fillStyle = 'rgba(110,103,84,.65)'
  ctx.font = '10px sans-serif'
  ctx.textAlign = 'center'
  const gridStep = total <= 90 ? 30 : total <= 600 ? 60 : 300 // 短会话 30s / 中 1min / 长 5min
  for (let t = gridStep; t < total; t += gridStep) {
    const x = Math.round((t / total) * (w - 16) + 8) + 0.5
    ctx.beginPath()
    ctx.moveTo(x, 6)
    ctx.lineTo(x, h - padB)
    ctx.stroke()
    ctx.fillText(fmtMin(t), x, h - 1.5)
  }
  // 基线
  ctx.strokeStyle = 'rgba(138,109,59,.4)'
  ctx.beginPath()
  ctx.moveTo(8, h - padB + 0.5)
  ctx.lineTo(w - 8, h - padB + 0.5)
  ctx.stroke()

  if (poly.length < 2) {
    ctx.fillStyle = 'rgba(110,103,84,.75)'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('本会话没有鼠标轨迹数据', w / 2, h / 2)
    return
  }
  const bucketSec = 10
  const nBuckets = Math.max(1, Math.ceil(total / bucketSec))
  const dist = new Float64Array(nBuckets)
  for (let i = 1; i < poly.length; i++) {
    const a = poly[i - 1]
    const b = poly[i]
    if (!Array.isArray(a) || !Array.isArray(b)) continue
    const t = Number(b[2])
    if (!Number.isFinite(t) || t < 0) continue
    const bi = Math.floor(t / bucketSec)
    if (bi >= nBuckets) continue
    const dx = Number(b[0]) - Number(a[0])
    const dy = Number(b[1]) - Number(a[1])
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) continue
    dist[bi] += Math.hypot(dx, dy)
  }
  // log 归一：安静阅读时段桶内位移小，剧烈晃动大，log 压数量级后线性映射
  const vals = new Float64Array(nBuckets)
  let vmax = 0
  for (let i = 0; i < nBuckets; i++) {
    const v = dist[i] > 0 ? Math.log10(1 + dist[i]) : 0
    vals[i] = v
    if (v > vmax) vmax = v
  }
  const x0 = 8
  const x1 = w - 8
  const bw = (x1 - x0) / nBuckets
  const yOf = (v) => h - padB - (vmax > 0 ? (v / vmax) * plotH : 0)
  // 数据桶过少（<3）时画离散柱而非插值面积——避免"先升后降"的假象（走查 M3）
  const filledBuckets = dist.reduce((n, v) => n + (v > 0 ? 1 : 0), 0)
  if (filledBuckets < 3) {
    const barW = Math.max(4, bw * 0.5)
    ctx.fillStyle = 'rgba(214,81,36,.35)'
    for (let i = 0; i < nBuckets; i++) {
      if (dist[i] <= 0) continue
      const cx = x0 + bw * (i + 0.5)
      const yTop = yOf(vals[i])
      ctx.fillRect(cx - barW / 2, yTop, barW, h - padB - yTop)
    }
  } else {
    ctx.beginPath()
    ctx.moveTo(x0, h - padB)
    for (let i = 0; i < nBuckets; i++) {
      const cx = x0 + bw * (i + 0.5)
      ctx.lineTo(cx, yOf(vals[i]))
    }
    ctx.lineTo(x1, h - padB)
    ctx.closePath()
    ctx.fillStyle = 'rgba(214,81,36,.28)'
    ctx.fill()
  }
  // 顶部描线
  ctx.beginPath()
  for (let i = 0; i < nBuckets; i++) {
    const cx = x0 + bw * (i + 0.5)
    if (i === 0) ctx.moveTo(cx, yOf(vals[i]))
    else ctx.lineTo(cx, yOf(vals[i]))
  }
  ctx.strokeStyle = 'rgba(214,81,36,.85)'
  ctx.lineWidth = 1.5
  ctx.stroke()
  // 时长标注
  ctx.fillStyle = 'rgba(110,103,84,.8)'
  ctx.font = '10.5px sans-serif'
  ctx.textAlign = 'left'
  ctx.fillText('0:00', x0, h - 1.5)
  ctx.textAlign = 'right'
  ctx.fillText(fmtDur(total), x1, h - 1.5)

  // v2：内容互动事件标记（activity 时间线）——查词 ● / 发音 ▲ 顶部一排，同点错开
  const acts = Array.isArray(session.activity) ? session.activity : []
  const marks = acts.filter((a) => a && (a.type === 'lookup' || a.type === 'speak'))
  if (marks.length) {
    let laneToggle = 0
    for (const a of marks) {
      const t = Math.max(0, Math.min(total, Number(a.t) || 0))
      const x = x0 + (t / total) * (x1 - x0)
      const y = 10 + (laneToggle++ % 3) * 7
      ctx.beginPath()
      if (a.type === 'lookup') {
        ctx.fillStyle = '#d65124'
        ctx.arc(x, y, 3, 0, Math.PI * 2)
        ctx.fill()
      } else {
        ctx.fillStyle = '#1d8578'
        ctx.moveTo(x, y - 4); ctx.lineTo(x - 4, y + 3); ctx.lineTo(x + 4, y + 3)
        ctx.closePath(); ctx.fill()
      }
    }
    // 微图例（右上角）
    ctx.font = '10px sans-serif'
    ctx.textAlign = 'right'
    ctx.fillStyle = '#d65124'
    ctx.beginPath(); ctx.arc(x1 - 74, 10, 3, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = 'rgba(110,103,84,.85)'
    ctx.fillText('查词', x1 - 66, 13)
    ctx.fillStyle = '#1d8578'
    ctx.beginPath(); ctx.moveTo(x1 - 32, 6); ctx.lineTo(x1 - 36, 13); ctx.lineTo(x1 - 28, 13); ctx.closePath(); ctx.fill()
    ctx.fillStyle = 'rgba(110,103,84,.85)'
    ctx.fillText('发音', x1 - 24, 13)
  }
}

// 轨迹缩略图：前 2000 点归一化画到 200×150
function drawFocusTrail(canvas, session) {
  if (!canvas) return
  const poly = Array.isArray(session.polyline) ? session.polyline : []
  const pts = []
  for (const p of poly) {
    if (pts.length >= 2000) break
    if (!Array.isArray(p)) continue
    const x = Number(p[0])
    const y = Number(p[1])
    if (Number.isFinite(x) && Number.isFinite(y)) pts.push([x, y])
  }
  const dpr = window.devicePixelRatio || 1
  // 跟随 CSS 尺寸（宽满容器 ≤560、高 240——走查 M4 邮票图放大）
  const rect = canvas.getBoundingClientRect()
  const w = Math.max(200, Math.round(rect.width || 200))
  const h = Math.max(120, Math.round(rect.height || 150))
  canvas.width = Math.round(w * dpr)
  canvas.height = Math.round(h * dpr)
  canvas.style.width = `${w}px`
  canvas.style.height = `${h}px`
  const ctx = canvas.getContext('2d')
  ctx.scale(dpr, dpr)
  if (pts.length < 2) {
    ctx.fillStyle = 'rgba(110,103,84,.75)'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('无轨迹', w / 2, h / 2)
    return
  }
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const [x, y] of pts) {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const pad = 8
  const sx = (w - pad * 2) / (maxX - minX || 1)
  const sy = (h - pad * 2) / (maxY - minY || 1)
  // 等比缩放：取较小者，避免轨迹被拉伸
  const sEq = Math.min(sx, sy)
  const cx0 = (minX + maxX) / 2
  const cy0 = (minY + maxY) / 2
  const gx = (x) => w / 2 + (x - cx0) * sEq
  const gy = (y) => h / 2 - (y - cy0) * sEq
  ctx.strokeStyle = 'rgba(214,81,36,.45)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(gx(pts[0][0]), gy(pts[0][1]))
  for (let i = 1; i < pts.length; i++) ctx.lineTo(gx(pts[i][0]), gy(pts[i][1]))
  ctx.stroke()
  // 起终点小标记
  ctx.fillStyle = 'rgba(34,160,107,.9)'
  ctx.beginPath()
  ctx.arc(gx(pts[0][0]), gy(pts[0][1]), 2.5, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = 'rgba(180,35,24,.9)'
  ctx.beginPath()
  const last = pts[pts.length - 1]
  ctx.arc(gx(last[0]), gy(last[1]), 2.5, 0, Math.PI * 2)
  ctx.fill()
}

function recRow(r) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
      ${r.status === 'graded'
        ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
        : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

function fceRow(s) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(s.user)} · T${s.test_id} P${s.part}</span>
      ${s.status === 'auto'
        ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b>`
        : s.status === 'graded'
          ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b>`
          : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${s.duration_sec ? fmtDur(s.duration_sec) + ' · ' : ''}${(s.created_at || '').slice(0, 10)}</span>
    </div>`
}

// 跳转路由（sessionStorage 联动目标页自动打开对应面板）
export function go(target) {
  if (target === 'recording') {
    sessionStorage.setItem('gkb-open-review', '1')
    location.hash = '#/readingAdmin'
  } else if (target === 'essay') {
    sessionStorage.setItem('gkb-open-essay', '1')
    location.hash = '#/fcePapers'
  } else if (target === 'fce-subs') {
    location.hash = '#/fcePapers'
  } else if (target === 'haya-exam') {
    location.hash = '#/exams'
  }
}

function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

function scopeCn(scope) {
  return { all: '全部词表', verb: '只动词', special: '特殊拼写' }[scope] || scope || '全部词表'
}

function topWrongWords(sessions) {
  const freq = new Map()
  for (const s of sessions.slice(0, 20)) {
    for (const w of s.wrong_words || []) freq.set(w, (freq.get(w) || 0) + 1)
  }
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([w, n]) => `${escapeHtml(w)}×${n}`)
    .join('、') || '（暂无）'
}

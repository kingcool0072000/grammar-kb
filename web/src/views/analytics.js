import { escapeHtml, escAttr } from '../render.js'
import { api } from '../api.js'

// 教师版 · 学情分析：实时渲染（替代旧静态快照 public/analytics-report.html，已删除）。
// 数据源：GET /analytics/summary?user=<user>&month=<YYYY-MM>（鉴权/401/解包走 api 层）
// 契约字段缺失/null 一律显示 '—'，不报错。

// ---- 纸质报告风样式（搬运自旧 analytics-report.html 的 <style>，规则加 .ana-report
// 作用域前缀防止 table/th/footer 等裸选择器污染宿主页面；报告内部视觉不变）----
const ANA_CSS = `
.ana-report { --paper:#f7f1e3; --card:#fffdf8; --ink:#2f2a20; --ink-soft:#6b6353; --line:#e5dcc8;
  --brand:#8a6d1f; --brand-ink:#6b5410; }
.ana-report, .ana-report * { box-sizing:border-box; margin:0; padding:0; }
.ana-report { background:var(--paper); color:var(--ink); font-family:-apple-system,"PingFang SC","Hiragino Sans GB",sans-serif;
  line-height:1.7; padding:28px 16px 60px; }
.ana-report.embedded { padding:12px 0 24px; border-radius:14px; }
.ana-report .wrap { max-width:900px; margin:0 auto; }
.ana-report header.hero { background:linear-gradient(135deg,#8a6d1f,#b08d2e); color:#fff; border-radius:18px;
  padding:30px 28px; margin-bottom:22px; }
.ana-report header.hero h1 { font-size:26px; margin-bottom:6px; }
.ana-report header.hero p { opacity:.92; font-size:14px; }
.ana-report .hero-row { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; flex-wrap:wrap; }
.ana-report .ana-month { display:flex; align-items:center; gap:8px; font-size:13px; opacity:.95; white-space:nowrap; }
.ana-report .ana-month input { background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.5);
  color:#fff; border-radius:9px; padding:5px 9px; font-size:13.5px; font-family:inherit; color-scheme:dark; }
.ana-report .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:26px; }
.ana-report .stat { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; text-align:center; }
.ana-report .stat b { display:block; font-size:26px; color:var(--brand-ink); font-variant-numeric:tabular-nums; }
.ana-report .stat span { font-size:12.5px; color:var(--ink-soft); }
.ana-report .stat .sub { display:block; margin-top:4px; font-size:11.5px; line-height:1.55; }
.ana-report .stat .unit { font-style:normal; font-size:12px; color:var(--ink-soft); margin-left:3px; font-weight:400; }
.ana-report .stat.stat-wide { grid-column:1 / -1; text-align:left; }
.ana-report .delta-up { color:#2e7d4f; font-weight:700; }
.ana-report .delta-down { color:#b42318; font-weight:700; }
.ana-report .delta-flat { color:var(--ink-soft); }
.ana-report section { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:20px 22px; margin-bottom:22px; }
.ana-report section h2 { font-size:18px; color:var(--brand-ink); margin-bottom:4px; }
.ana-report section .sub { font-size:13px; color:var(--ink-soft); margin-bottom:14px; }
.ana-report table { width:100%; border-collapse:collapse; font-size:13.5px; }
.ana-report th { text-align:left; font-size:12px; color:var(--ink-soft); border-bottom:2px solid var(--line); padding:6px 8px; }
.ana-report td { border-bottom:1px solid var(--line); padding:7px 8px; vertical-align:middle; }
.ana-report tr:last-child td { border-bottom:0; }
.ana-report .bar { background:#efe8d6; border-radius:5px; height:9px; width:90px; overflow:hidden; display:inline-block; vertical-align:middle; }
.ana-report .bar i { display:block; height:100%; border-radius:5px; }
.ana-report .barcell { white-space:nowrap; } .ana-report .barcell span { margin-left:8px; font-weight:700; font-variant-numeric:tabular-nums; }
.ana-report .score { font-weight:800; color:#2e7d4f; } .ana-report .score.warn { color:#c77928; } .ana-report .score.bad { color:#b42318; }
.ana-report .lecs { font-size:12px; color:var(--ink-soft); }
.ana-report .callout { background:#fdf6e4; border:1px solid #ead9ad; border-radius:12px; padding:12px 16px; font-size:13.5px; margin-top:14px; }
.ana-report .callout b { color:var(--brand-ink); }
.ana-report .callout.yellow { background:#fdf6e4; border-color:#ead9ad; }
.ana-report .callout.yellow b { color:#8a6d1f; }
.ana-report .callout.red { background:#fdecea; border-color:#efc3bb; }
.ana-report .callout.red b { color:#b42318; }
.ana-report .chips { display:flex; flex-wrap:wrap; gap:8px; }
.ana-report .chip { background:var(--paper); border:1px solid var(--line); border-radius:999px; padding:4px 14px; font-size:14px; }
.ana-report .chip b { margin-left:6px; color:#b42318; font-variant-numeric:tabular-nums; font-weight:700; }
.ana-report .chip.day { color:var(--brand-ink); font-size:12.5px; padding:2px 10px; }
.ana-report ul.ana-daily { list-style:none; font-size:14px; margin-top:12px; }
.ana-report ul.ana-daily li { padding:7px 0; border-bottom:1px dashed var(--line); display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.ana-report ul.ana-daily li:last-child { border-bottom:0; }
.ana-report .gap-dates { color:#b42318; font-weight:700; }
.ana-report .topic-list { list-style:none; margin-top:8px; display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:4px 22px; font-size:13.5px; }
.ana-report .topic-list li { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 0; border-bottom:1px dashed var(--line); }
.ana-report .topic-list .t-title { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ana-report .topic-list .t-prog { white-space:nowrap; }
.ana-report .topic-list .t-prog b { margin-left:8px; color:var(--brand-ink); font-variant-numeric:tabular-nums; }
.ana-report ul.insights { padding-left:20px; font-size:14px; } .ana-report ul.insights li { margin-bottom:8px; }
.ana-report .muted { color:var(--ink-soft); font-size:13.5px; }
.ana-report footer { text-align:center; font-size:12px; color:var(--ink-soft); margin-top:8px; }
@media print { .ana-report { padding:10px; } .ana-report section { break-inside:avoid; } }
`

// 样式只注入一次（重复挂载不堆 <style> 标签）
function ensureStyles() {
  if (!document.getElementById('ana-report-style')) {
    const tag = document.createElement('style')
    tag.id = 'ana-report-style'
    tag.textContent = ANA_CSS
    document.head.appendChild(tag)
  }
}

// ---- 兜底工具：字段缺失/null 一律 '—' ----
const esc = escapeHtml
const DASH = '—'

function fmt(v) {
  return v === null || v === undefined || v === '' ? DASH : String(v)
}

function fmtNum(v) {
  if (v === null || v === undefined || v === '') return DASH
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('en-US') : DASH
}

// value 相比 prev 的升降标记：上升绿 ▲ / 下降红 ▼（goodWhenUp=false 时反转，
// 用于「积压」这类越低越好的指标）
function deltaHtml(curr, prev, goodWhenUp = true) {
  if (curr === null || curr === undefined || prev === null || prev === undefined) return ''
  const c = Number(curr)
  const p = Number(prev)
  if (!Number.isFinite(c) || !Number.isFinite(p)) return ''
  const d = c - p
  if (d === 0) return '<span class="delta-flat">与上月持平</span>'
  const up = d > 0
  const cls = up === goodWhenUp ? 'delta-up' : 'delta-down'
  const mag = Number.isInteger(Math.abs(d)) ? String(Math.abs(d)) : Math.abs(d).toFixed(1)
  return `<span class="${cls}">${up ? '▲' : '▼'} ${up ? '+' : '-'}${mag}</span>`
}

// 成绩分档：≥75 绿 / 70–74 橙 / <70 红（沿用 .score / .score.warn / .score.bad）
function scoreCls(v) {
  const n = Number(v)
  if (v === null || v === undefined || v === '' || !Number.isFinite(n)) return ''
  if (n >= 75) return 'score'
  if (n >= 70) return 'score warn'
  return 'score bad'
}

function bandColor(v) {
  const n = Number(v)
  if (n >= 75) return '#2e7d4f'
  if (n >= 70) return '#c77928'
  return '#b42318'
}

const KIND_LABELS = { hw: '作业卷', exam: '考试', vocab: '词汇卷', vocab_paper: '词汇卷', fce: 'FCE' }
const kindLabel = (k) => (k === null || k === undefined || k === '' ? DASH : KIND_LABELS[k] || String(k))

const FLAG_LABELS = {
  backlog: '复习积压',
  gap: '学习断档',
  gaps: '学习断档',
  score: '成绩波动',
  recite: '背词中断',
  intensive: '精读下滑',
  fce: 'FCE 正确率',
}

function fmtTime(t) {
  if (t === null || t === undefined || t === '') return DASH
  return esc(String(t).replace('T', ' ').slice(0, 19))
}

function currentMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function barHtml(pct, color) {
  const w = Math.max(0, Math.min(100, Number(pct) || 0))
  return `<span class="bar"><i style="width:${w}%;background:${color}"></i></span>`
}

const parts = (arr) => arr.filter(Boolean).join(' · ')

// ---- 渲染各区块 ----

function heroHtml({ name, month, generatedAt }) {
  return `<header class="hero"><div class="hero-row">
    <div>
      <h1>📊 ${esc(name)} 学情分析</h1>
      <p>${esc(fmt(month))} · 生成于 ${fmtTime(generatedAt)}</p>
    </div>
    <label class="ana-month">月份
      <input type="month" value="${escAttr(fmt(month))}" aria-label="切换月份">
    </label>
  </div></header>`
}

function flagsHtml(flags) {
  const list = Array.isArray(flags) ? flags : []
  return list.map((f) => {
    const o = f && typeof f === 'object' ? f : {}
    const lv = o.level === 'red' ? 'red' : o.level === 'yellow' ? 'yellow' : ''
    const label = FLAG_LABELS[o.key] || (o.key ? String(o.key) : '提示')
    const msg = o.message === null || o.message === undefined || o.message === '' ? DASH : esc(String(o.message))
    return `<div class="callout ${lv}"><b>${esc(label)}：</b>${msg}</div>`
  }).join('')
}

function statCard({ label, valueHtml, subs = [] }) {
  const subHtml = subs.filter(Boolean)
    .map((s) => `<span class="sub">${s}</span>`)
    .join('')
  return `<div class="stat"><b>${valueHtml}</b><span>${esc(label)}</span>${subHtml}</div>`
}

function cardsHtml(cards) {
  const c = cards && typeof cards === 'object' ? cards : {}
  const g = (k) => (c[k] && typeof c[k] === 'object' ? c[k] : {})
  const out = []

  // 作业卷均分（当月无登记时 count=0、value 无意义：值显 '—' 且不显升降）
  const sc = g('score')
  const scHasData = Number(sc.count) > 0
  out.push(statCard({
    label: sc.label || '作业卷均分',
    valueHtml: scHasData ? esc(fmtNum(sc.value)) : '—',
    subs: [parts([
      sc.count !== null && sc.count !== undefined ? `${esc(fmt(sc.count))} 次` : '',
      scHasData ? deltaHtml(sc.value, sc.prev_value, true) : '',
      sc.prev_count !== null && sc.prev_count !== undefined ? `上月 ${esc(fmt(sc.prev_count))} 次` : '',
    ])],
  }))

  // 背词学习日
  const rd = g('recite_days')
  out.push(statCard({
    label: rd.label || '背词学习日',
    valueHtml: esc(fmtNum(rd.value)),
    subs: [parts([
      deltaHtml(rd.value, rd.prev_value, true),
      rd.prev_value !== null && rd.prev_value !== undefined ? `上月 ${esc(fmtNum(rd.prev_value))}` : '',
    ])],
  }))

  // 背词正确率%
  const ra = g('recite_accuracy')
  out.push(statCard({
    label: ra.label || '背词正确率%',
    valueHtml: esc(fmtNum(ra.value)),
    subs: [],
  }))

  // 累计掌握词
  const ma = g('mastered')
  const dm = ma.delta_month
  out.push(statCard({
    label: ma.label || '累计掌握词',
    valueHtml: esc(fmtNum(ma.value)),
    subs: [dm !== null && dm !== undefined
      ? `<span class="${Number(dm) >= 0 ? 'delta-up' : 'delta-down'}">本月 ${Number(dm) >= 0 ? '+' : '-'}${esc(fmtNum(Math.abs(Number(dm))))}</span>`
      : ''],
  }))

  // 复习积压（越多越糟，若有 prev 则降绿升红）
  const bl = g('backlog')
  const stages = Array.isArray(bl.stages) ? bl.stages : []
  out.push(statCard({
    label: bl.label || '复习积压',
    valueHtml: esc(fmtNum(bl.value)),
    subs: [parts([
      bl.in_cycle !== null && bl.in_cycle !== undefined ? `周期内 ${esc(fmtNum(bl.in_cycle))}` : '',
      stages.length ? stages.map((s) => `S${esc(fmt(s && s.stage))} ${esc(fmt(s && s.count))}`).join(' / ') : '',
      deltaHtml(bl.value, bl.prev_value, false),
    ])],
  }))

  // 泛读
  const ex = g('extensive')
  out.push(statCard({
    label: ex.label || '泛读',
    valueHtml: `${esc(fmtNum(ex.minutes))}<i class="unit">分钟</i>`,
    subs: [
      parts([
        ex.sessions !== null && ex.sessions !== undefined ? `${esc(fmt(ex.sessions))} 段` : '',
        ex.month_words !== null && ex.month_words !== undefined ? `${esc(fmtNum(ex.month_words))} 词` : '',
        ex.invalid_pct !== null && ex.invalid_pct !== undefined ? `无效 ${esc(fmt(ex.invalid_pct))}%` : '',
      ]),
      parts([
        ex.book ? esc(String(ex.book)) : '',
        ex.progress_pct !== null && ex.progress_pct !== undefined ? `${esc(fmt(ex.progress_pct))}%` : '',
      ]),
    ],
  }))

  // 精读录音
  const it = g('intensive')
  out.push(statCard({
    label: it.label || '精读录音',
    valueHtml: `${esc(fmtNum(it.recordings))}<i class="unit">条</i>`,
    subs: [parts([
      it.avg_score !== null && it.avg_score !== undefined ? `均分 ${esc(fmt(it.avg_score))}` : '',
      it.prev_recordings !== null && it.prev_recordings !== undefined
        ? `${deltaHtml(it.recordings, it.prev_recordings, true)}（上月 ${esc(fmtNum(it.prev_recordings))}）`
        : '',
    ])],
  }))

  // 视频复述（后端字段名与精读同为 recordings；retells 为契约旧名，兼容读取）
  const vd = g('video')
  const vdCount = vd.recordings !== null && vd.recordings !== undefined ? vd.recordings : vd.retells
  out.push(statCard({
    label: vd.label || '视频复述',
    valueHtml: `${esc(fmtNum(vdCount))}<i class="unit">条</i>`,
    subs: [vd.avg_score !== null && vd.avg_score !== undefined ? `均分 ${esc(fmt(vd.avg_score))}` : ''],
  }))

  // FCE 客观题
  const fc = g('fce')
  out.push(statCard({
    label: fc.label || 'FCE 客观题',
    valueHtml: `${esc(fmt(fc.correct))}/${esc(fmt(fc.total))}`,
    subs: [parts([
      fc.submissions !== null && fc.submissions !== undefined ? `提交 ${esc(fmt(fc.submissions))} 次` : '',
      fc.parts_done !== null || fc.parts_total !== undefined
        ? `Part ${esc(fmt(fc.parts_done))}/${esc(fmt(fc.parts_total))}`
        : '',
    ])],
  }))

  // 专题完成（全宽小列表）
  const tp = g('topics')
  const items = Array.isArray(tp.items) ? tp.items : []
  const doneCount = items.filter((x) => {
    const o = x && typeof x === 'object' ? x : {}
    const total = Number(o.nodes_total)
    const dn = Number(o.nodes_done)
    return Number.isFinite(total) && total > 0 && Number.isFinite(dn) && dn >= total
  }).length
  out.push(`<div class="stat stat-wide">
    <b>${items.length ? `${esc(fmt(doneCount))}/${esc(fmt(items.length))}` : DASH}</b>
    <span>${esc(tp.label || '专题完成')}</span>
    <span class="sub">${items.length
      ? `<ul class="topic-list">${items.map(topicRow).join('')}</ul>`
      : '暂无专题数据'}</span>
  </div>`)

  return out.join('')
}

function topicRow(item) {
  const o = item && typeof item === 'object' ? item : {}
  const total = Number(o.nodes_total)
  const dn = Number(o.nodes_done)
  const has = Number.isFinite(total) && total > 0 && Number.isFinite(dn)
  const pct = has ? (dn / total) * 100 : 0
  const color = has && dn >= total ? '#2e7d4f' : '#c77928'
  return `<li>
    <span class="t-title">${esc(fmt(o.title))}</span>
    <span class="t-prog">${barHtml(pct, color)}<b>${esc(fmt(o.nodes_done))}/${esc(fmt(o.nodes_total))}</b></span>
  </li>`
}

function scoresHtml(scores) {
  const rows = Array.isArray(scores) ? scores : []
  if (!rows.length) return '<p class="muted">本月暂无成绩记录。</p>'
  return `<table>
    <tr><th>日期</th><th>讲次</th><th>卷名</th><th>类型</th><th>得分</th><th>错题</th></tr>
    ${rows.map(rowScore).join('')}
  </table>`
}

function rowScore(s) {
  const o = s && typeof s === 'object' ? s : {}
  const n = Number(o.score)
  const has = o.score !== null && o.score !== undefined && o.score !== '' && Number.isFinite(n)
  const scoreCell = has
    ? `${barHtml(n, bandColor(n))}<span class="${scoreCls(n)}">${esc(fmt(o.score))}</span>`
    : `<span class="muted">${DASH}</span>`
  return `<tr>
    <td><b>${esc(fmt(o.date))}</b></td>
    <td>${esc(fmt(o.lecture))}</td>
    <td>${esc(fmt(o.title))}</td>
    <td>${esc(kindLabel(o.kind))}</td>
    <td class="barcell">${scoreCell}</td>
    <td>${esc(fmt(o.wrong_count))}</td>
  </tr>`
}

function wrongWordsHtml(words) {
  const list = (Array.isArray(words) ? words : []).slice(0, 20)
  if (!list.length) return '<p class="muted">本月暂无易错词记录。</p>'
  return `<div class="chips">${list.map((w) => {
    const o = w && typeof w === 'object' ? w : {}
    return `<span class="chip">${esc(fmt(o.word))}<b>×${esc(fmt(o.count))}</b></span>`
  }).join('')}</div>`
}

function dailyHtml(daily) {
  const d = daily && typeof daily === 'object' ? daily : {}
  const days = Array.isArray(d.recite_days) ? d.recite_days : []
  const gaps = Array.isArray(d.gaps) ? d.gaps : []
  const dayBlock = days.length
    ? `<div class="chips">${days.map((x) => `<span class="chip day">${esc(fmt(x))}</span>`).join('')}</div>`
    : '<p class="muted">本月暂无背词学习日记录。</p>'
  const gapRows = gaps.length
    ? gaps.map((gp) => {
        const o = gp && typeof gp === 'object' ? gp : {}
        return `<li>
          <span class="gap-dates">${esc(fmt(o.from))} → ${esc(fmt(o.to))}</span>
          <span>断档 <b>${esc(fmt(o.days))}</b> 天</span>
        </li>`
      }).join('')
    : '<li><span class="delta-up">本月无断档，保持节奏。</span></li>'
  return `
    <p class="sub">背词学习日共 ${esc(fmt(days.length))} 天；断档指连续多日无学习的区间。</p>
    ${dayBlock}
    <ul class="ana-daily">${gapRows}</ul>`
}

function reportBody(d) {
  const data = d && typeof d === 'object' ? d : {}
  return [
    flagsHtml(data.flags),
    `<div class="cards">${cardsHtml(data.cards)}</div>`,
    `<section>
      <h2>本月成绩</h2>
      <p class="sub">得分标色：≥75 达标 / 70–74 临界 / &lt;70 需关注。</p>
      ${scoresHtml(data.scores)}
    </section>`,
    `<section>
      <h2>易错词 Top</h2>
      <p class="sub">本月错误次数最多的词（最多展示 20 个）。</p>
      ${wrongWordsHtml(data.wrong_words)}
    </section>`,
    `<section>
      <h2>断档与学习日</h2>
      ${dailyHtml(data.daily)}
    </section>`,
    `<footer>和爸学 · 学情分析 · 实时渲染${data.generated_at ? `（数据生成于 ${fmtTime(data.generated_at)}）` : ''}</footer>`,
  ].join('')
}

function loadingBody() {
  return '<p class="muted">学情数据加载中…</p>'
}

function errorBody(e) {
  const msg = e && e.message ? e.message : String(e)
  return `<p style="color:#b42318">学情分析加载失败：${esc(msg)}</p>
    <p class="muted" style="margin-top:6px">请确认后端服务可用（端点 /api/analytics/summary），或稍后重试。</p>`
}

// ---- 入口：保持 mountAnalytics(el, { bare, user }) 签名 ----
export async function mountAnalytics(el, { bare = false, user = '' } = {}) {
  let month = currentMonth()
  let seq = 0 // 月份快速切换时丢弃过期响应

  function paint(bodyHtml, resolvedName, generatedAt = '') {
    ensureStyles()
    el.innerHTML = `
      ${bare ? '' : `<div class="view-head"><h1>学情分析</h1><p>实时学情数据 · 可切换月份查看。</p></div>`}
      <div class="ana-report${bare ? ' embedded' : ''}"><div class="wrap">
        ${heroHtml({ name: resolvedName, month, generatedAt })}
        ${bodyHtml}
      </div></div>`
    const inp = el.querySelector('.ana-month input')
    if (inp) {
      inp.addEventListener('change', () => {
        if (!inp.value) return
        month = inp.value
        load()
      })
    }
  }

  async function load() {
    const my = ++seq
    paint(loadingBody(), user || '学生')
    let data
    try {
      data = await api.analyticsSummary(user, month)
    } catch (e) {
      if (my !== seq) return
      paint(errorBody(e), user || '学生')
      return
    }
    if (my !== seq) return
    const d = data && typeof data === 'object' ? data : {}
    paint(reportBody(d), d.user || user || '学生', d.generated_at || '')
  }

  await load()
}

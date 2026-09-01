import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 学情分析：每周总结 / 月度总结——四类作业数据聚合。
//   周视图：最近 8 周各类作业量与成绩趋势 + 本周明细
//   月视图：最近 6 个月成绩趋势 + 本月明细与统计
export async function mountAnalytics(el) {
  el.innerHTML = '<div class="view-head"><h1>学情分析</h1><p>加载中…</p></div>'
  let recs, fceSubs, recite, exams
  try {
    ;[recs, fceSubs, recite, exams] = await Promise.all([
      api.readingRecordings({ limit: 500 }),
      api.fceSubmissions({ limit: 500 }),
      api.reciteSessions({ limit: 500 }),
      api.examsList(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }

  const state = { period: 'week' } // week | month
  const render = () => {
    const agg = state.period === 'week'
      ? aggregate(recs, fceSubs, recite, exams, 8, 'week')
      : aggregate(recs, fceSubs, recite, exams, 6, 'month')
    el.innerHTML = `
      <div class="view-head">
        <h1>学情分析</h1>
        <p>四类作业的周期汇总：哈一作业成绩 · FCE 练习 · 阅读朗读 · 背单词。</p>
      </div>
      <div class="ana-tabs">
        <button class="reading-btn ${state.period === 'week' ? 'primary' : ''}" data-period="week">📅 每周总结</button>
        <button class="reading-btn ${state.period === 'month' ? 'primary' : ''}" data-period="month">🗓 月度总结</button>
        <span class="reading-pick-info">当前周期：${agg.curLabel}</span>
      </div>

      <div class="grading-stats">
        <div class="grading-stat"><b>${agg.cur.exams.length}</b><span>${agg.curLabel}哈一作业（次）</span></div>
        <div class="grading-stat"><b>${agg.cur.fce.length}</b><span>FCE 练习（次）</span></div>
        <div class="grading-stat"><b>${agg.cur.rec.length}</b><span>阅读朗读（次）</span></div>
        <div class="grading-stat"><b>${agg.cur.recite.length}</b><span>背单词（组）</span></div>
      </div>

      <section class="gd-section">
        <header class="gd-section-head"><h2>📈 成绩趋势</h2></header>
        <div class="ana-chart">
          ${agg.buckets.map((b) => {
            const bars = [
              { label: '哈一', v: b.examAvg, max: 100, unit: '分', color: '#8a6d3b' },
              { label: 'FCE', v: b.fceAvg, max: 100, unit: '%', color: '#6b8f71' },
              { label: '朗读', v: b.recAvg, max: 10, unit: '分', color: '#5b7fa6' },
              { label: '单词', v: b.reciteAvg, max: 100, unit: '%', color: '#a67c52' },
            ]
            return `
            <div class="ana-col" title="${b.label}">
              <div class="ana-bars">
                ${bars.map((x) => `
                  <div class="ana-bar-track">
                    <i class="ana-bar" style="height:${x.v == null ? 0 : Math.max(4, Math.round((x.v / x.max) * 100))}%; background:${x.v == null ? 'transparent' : x.color}"
                       title="${x.label} ${x.v == null ? '无数据' : x.v + x.unit}"></i>
                  </div>`).join('')}
              </div>
              <span class="ana-col-label">${b.label}</span>
            </div>`
          }).join('')}
          <div class="ana-legend">
            <span><i style="background:#8a6d3b"></i>哈一（百分制）</span>
            <span><i style="background:#6b8f71"></i>FCE 正确率</span>
            <span><i style="background:#5b7fa6"></i>朗读（10 分制）</span>
            <span><i style="background:#a67c52"></i>单词正确率</span>
          </div>
        </div>
      </section>

      <div class="gd-board">
        <section class="gd-section">
          <header class="gd-section-head"><h2>📚 ${agg.curLabel} · 哈一 & 单词</h2></header>
          <div class="gd-subgroup">
            <h3>哈一作业成绩</h3>
            ${agg.cur.exams.slice(0, 6).map((e) => `
              <div class="fce-his-row">
                <span class="fce-his-what">第 ${e.lecture} 讲</span>
                <b class="fce-his-score ${e.score >= 90 ? 'ok' : e.score >= 70 ? '' : 'bad'}">${e.score}</b>
                <span class="fce-his-date">${e.date}${e.wrong && e.wrong.length ? ` · 错 ${e.wrong.length}` : ''}</span>
              </div>`).join('') || '<p class="reading-hint">本周期无记录</p>'}
            ${agg.cur.exams.length ? `<p class="reading-hint">平均 ${agg.cur.examAvg} 分 · 最高 ${Math.max(...agg.cur.exams.map((e) => e.score))} · 最低 ${Math.min(...agg.cur.exams.map((e) => e.score))}</p>` : ''}
          </div>
          <div class="gd-subgroup">
            <h3>背单词</h3>
            ${agg.cur.recite.slice(0, 6).map((s) => `
              <div class="fce-his-row">
                <span class="fce-his-what">${scopeCn(s.scope)}${s.mode === 'flip' ? '（自评）' : ''}</span>
                <b class="fce-his-score ${s.acc >= 80 ? 'ok' : ''}">${s.acc}%</b>
                <span class="fce-his-date">${s.total} 词 · 错 ${s.wrong} · ${(s.created_at || '').slice(5, 10)}</span>
              </div>`).join('') || '<p class="reading-hint">本周期无记录</p>'}
            ${agg.cur.recite.length ? `<p class="reading-hint">平均正确率 ${agg.cur.reciteAvg}% · 共 ${agg.cur.recite.reduce((s, x) => s + x.total, 0)} 词次</p>` : ''}
          </div>
        </section>

        <section class="gd-section">
          <header class="gd-section-head"><h2>🎧 ${agg.curLabel} · FCE & 朗读</h2></header>
          <div class="gd-subgroup">
            <h3>FCE 练习</h3>
            ${agg.cur.fce.slice(0, 6).map((s) => `
              <div class="fce-his-row">
                <span class="fce-his-what">T${s.test_id} P${s.part}${s.paper === 'Writing' ? ' 写作' : ''}</span>
                ${s.status === 'auto'
                  ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b>`
                  : s.status === 'graded'
                    ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b>`
                    : '<b class="fce-his-score pend">待批改</b>'}
                <span class="fce-his-date">${(s.created_at || '').slice(5, 10)}</span>
              </div>`).join('') || '<p class="reading-hint">本周期无记录</p>'}
            ${agg.cur.fce.length ? `<p class="reading-hint">客观题平均正确率 ${agg.cur.fceAvg}%（${agg.cur.fce.filter((s) => s.status === 'auto').length} 次自动批改）</p>` : ''}
          </div>
          <div class="gd-subgroup">
            <h3>阅读朗读</h3>
            ${agg.cur.rec.slice(0, 6).map((r) => `
              <div class="fce-his-row">
                <span class="fce-his-what">${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
                ${r.status === 'graded'
                  ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
                  : '<b class="fce-his-score pend">待批改</b>'}
                <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(5, 10)}</span>
              </div>`).join('') || '<p class="reading-hint">本周期无记录</p>'}
            ${agg.cur.rec.length ? (agg.cur.recAvg != null ? `<p class="reading-hint">平均 ${agg.cur.recAvg}/10 分</p>` : '<p class="reading-hint">尚无已批改录音</p>') : ''}
          </div>
        </section>
      </div>
    `
    el.querySelectorAll('[data-period]').forEach((b) =>
      b.addEventListener('click', () => {
        state.period = b.dataset.period
        render()
      }),
    )
  }
  render()
}

// ---- 周期聚合 ----
function aggregate(recs, fceSubs, recite, exams, n, unit) {
  const fmt = unit === 'week' ? weekLabel : (d) => d.toISOString().slice(0, 7).replace('-', '/')
  const now = new Date()
  const buckets = []
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now)
    if (unit === 'week') d.setDate(d.getDate() - i * 7)
    else d.setMonth(d.getMonth() - i)
    buckets.push({ key: fmt(d), label: fmt(d), examAvg: null, fceAvg: null, recAvg: null, reciteAvg: null })
  }
  const keyOf = (t) => {
    const d = new Date(t)
    if (isNaN(d)) return null
    if (unit === 'week') {
      // 归到所在周的周一标签（与 bucket key 对齐：从本周往前推）
      const cur = new Date(d)
      const day = (cur.getDay() + 6) % 7 // 周一=0
      cur.setDate(cur.getDate() - day)
      return weekLabel(cur)
    }
    return d.toISOString().slice(0, 7).replace('-', '/')
  }
  const inBucket = {}
  buckets.forEach((b, i) => (inBucket[b.key] = i))

  const put = (arr, field, val, compute) => {
    for (const item of arr) {
      const k = keyOf(item.created_at || item.date)
      const idx = inBucket[k]
      if (idx == null) continue
      const b = buckets[idx]
      ;(b[field] = b[field] || []).push(item)
    }
    buckets.forEach((b) => {
      const list = b[field] || []
      b[field] = list
      if (list.length) b[compute.field] = compute.value(list)
    })
  }
  const avg = (xs) => Math.round(xs.reduce((s, x) => s + x, 0) / xs.length)

  put(exams, 'exams', 'score', { field: 'examAvg', value: (l) => avg(l.map((e) => e.score)) })
  put(fceSubs, 'fce', null, {
    field: 'fceAvg',
    value: (l) => {
      const auto = l.filter((s) => s.status === 'auto')
      return auto.length ? avg(auto.map((s) => Math.round((s.auto_score / (s.total || 1)) * 100))) : null
    },
  })
  put(recs, 'rec', null, {
    field: 'recAvg',
    value: (l) => {
      const g = l.filter((r) => r.status === 'graded')
      return g.length ? avg(g.map((r) => r.teacher_score)) : null
    },
  })
  put(recite, 'recite', null, { field: 'reciteAvg', value: (l) => avg(l.map((s) => s.acc)) })

  const curKey = buckets[buckets.length - 1].key
  const cur = {
    exams: buckets[buckets.length - 1].exams || [],
    fce: buckets[buckets.length - 1].fce || [],
    rec: buckets[buckets.length - 1].rec || [],
    recite: buckets[buckets.length - 1].recite || [],
    get examAvg() { return buckets[buckets.length - 1].examAvg },
    get fceAvg() { return buckets[buckets.length - 1].fceAvg },
    get recAvg() { return buckets[buckets.length - 1].recAvg },
    get reciteAvg() { return buckets[buckets.length - 1].reciteAvg },
  }
  return { buckets, cur, curLabel: curKey + (unit === 'week' ? ' 周' : ' 月') }
}

function weekLabel(d) {
  const day = (d.getDay() + 6) % 7 // 周一=0
  const monday = new Date(d)
  monday.setDate(d.getDate() - day)
  const mm = String(monday.getMonth() + 1).padStart(2, '0')
  const dd = String(monday.getDate()).padStart(2, '0')
  return `${mm}.${dd}`
}

function scopeCn(scope) {
  return { all: '全部词表', verb: '只动词', special: '特殊拼写' }[scope] || scope || '全部词表'
}

function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

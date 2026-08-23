import { escapeHtml } from '../render.js'
import { api } from '../api.js'

// 作业成绩记录：每讲一份作业卷（35 题，1~5 每题 2 分、6~35 每题 3 分，满分 100）。
// 孩子每卷会做多次：全部记录存 localStorage，错题按「讲次+题号」汇总方便回查。
const STORE_KEY = 'gkb-exam-records-v1'   // 旧 localStorage 记录，首启迁移到后端
const MIGRATED_KEY = 'gkb-exam-migrated-v1'
const TOTAL = 35

// 题号 → 分值（1~5 题 2 分，6~35 题 3 分）
const scoreOf = (q) => (q <= 5 ? 2 : 3)
const fullScore = Array.from({ length: TOTAL }, (_, i) => scoreOf(i + 1)).reduce((a, b) => a + b, 0)

// 记录持久化于后端 exam.db；首次启动把 localStorage 旧记录迁移上去
async function migrateOnce() {
  if (localStorage.getItem(MIGRATED_KEY)) return
  localStorage.setItem(MIGRATED_KEY, '1')
  try {
    const remote = await api.examsList()
    if (remote.length) return
    let old = []
    try {
      old = JSON.parse(localStorage.getItem(STORE_KEY) || '[]')
    } catch { /* 忽略坏数据 */ }
    for (const r of old) {
      if (r && r.lecture && r.date != null) {
        await api.examsAdd({ lecture: r.lecture, date: r.date, score: r.score || 0, wrong: r.wrong || [] })
      }
    }
    if (old.length) console.info(`已迁移 ${old.length} 条本地成绩到后端`)
  } catch { /* 后端不可用时下次再迁移 */ }
}

export function mountExams(el, { lectures }) {
  const lecTitle = (n) => {
    const l = lectures.find((x) => x.number === n)
    return l ? l.title : ''
  }

  el.innerHTML = `
    <div class="view-head">
      <h1>作业成绩</h1>
      <p>每讲一份作业卷（35 题 · 满分 ${fullScore}：1~5 题每题 ${scoreOf(1)} 分，6~35 题每题 ${scoreOf(6)} 分）。点题号记对错，分数自动算；多次作答全部保留。</p>
    </div>

    <section class="exam-sec">
      <h3>📝 记一次</h3>
      <div class="exam-form">
        <div class="exam-form-row">
          <label>讲次
            <select id="ex-lecture">
              ${lectures
                .slice()
                .sort((a, b) => a.number - b.number)
                .map((l) => `<option value="${l.number}">第${l.number}讲 ${escapeHtml(l.title)}</option>`)
                .join('')}
            </select>
          </label>
          <label>日期 <input type="date" id="ex-date" /></label>
          <div class="ex-live" id="ex-live"></div>
        </div>
        <div class="ex-tip">点击题号切换对错：<span class="q-btn right">对</span> / <span class="q-btn wrong">错</span></div>
        <div class="q-grid" id="ex-grid"></div>
        <div class="exam-actions">
          <button class="btn-primary" id="ex-save">保存这次成绩</button>
          <button class="btn-ghost" id="ex-clear">清空重选</button>
        </div>
      </div>
    </section>

    <section class="exam-sec">
      <h3>❌ 错题本 <span class="sec-sub">按错误次数排序，重复错的优先攻克</span></h3>
      <div id="ex-wrong"></div>
    </section>

    <section class="exam-sec">
      <h3>📚 全部成绩 <span class="sec-sub">按讲次查看每次作答</span></h3>
      <div class="exam-toolbar">
        <button class="btn-ghost" id="ex-export">导出记录（JSON）</button>
      </div>
      <div id="ex-history"></div>
    </section>
  `

  // ---------- 记一次 ----------
  const $lec = el.querySelector('#ex-lecture')
  const $date = el.querySelector('#ex-date')
  const $grid = el.querySelector('#ex-grid')
  const $live = el.querySelector('#ex-live')
  $date.value = new Date().toISOString().slice(0, 10)

  // 35 题按钮
  $grid.innerHTML = Array.from({ length: TOTAL }, (_, i) => i + 1)
    .map((q) => `<button class="q-btn" data-q="${q}">${q}</button>`)
    .join('')
  let wrong = new Set()
  const paintQ = () =>
    $grid.querySelectorAll('.q-btn').forEach((b) => {
      b.classList.toggle('wrong', wrong.has(Number(b.dataset.q)))
    })
  const paintLive = () => {
    const lost = [...wrong].reduce((s, q) => s + scoreOf(q), 0)
    const part1 = [...wrong].filter((q) => q <= 5).length
    const part2 = wrong.size - part1
    $live.innerHTML =
      `得分 <b>${fullScore - lost}</b> / ${fullScore}` +
      ` <span class="ex-detail">（错 ${wrong.size} 题：1~5 题 ${part1} 个×2 分 + 6~35 题 ${part2} 个×3 分 = 扣 ${lost} 分）</span>`
  }
  $grid.addEventListener('click', (e) => {
    const b = e.target.closest('.q-btn')
    if (!b) return
    const q = Number(b.dataset.q)
    wrong.has(q) ? wrong.delete(q) : wrong.add(q)
    paintQ()
    paintLive()
  })
  el.querySelector('#ex-clear').addEventListener('click', () => {
    wrong.clear()
    paintQ()
    paintLive()
  })
  el.querySelector('#ex-save').addEventListener('click', async () => {
    const lost = [...wrong].reduce((s, q) => s + scoreOf(q), 0)
    const btn = el.querySelector('#ex-save')
    btn.disabled = true
    try {
      await api.examsAdd({
        lecture: Number($lec.value),
        date: $date.value || new Date().toISOString().slice(0, 10),
        score: fullScore - lost,
        wrong: [...wrong].sort((a, b) => a - b),
      })
      wrong.clear()
      paintQ()
      paintLive()
      await refresh()
    } catch (e) {
      alert(`保存失败：${e.message}（请确认后端服务在运行）`)
    } finally {
      btn.disabled = false
    }
  })
  paintLive()

  // ---------- 错题本 ----------
  async function renderWrong() {
    const list = await fetchRecords(el)
    // (讲次, 题号) → 错误次数
    const agg = new Map()
    for (const r of list) {
      for (const q of r.wrong) {
        const k = `${r.lecture}-${q}`
        agg.set(k, (agg.get(k) || 0) + 1)
      }
    }
    const rows = [...agg.entries()]
      .map(([k, n]) => {
        const [lec, q] = k.split('-').map(Number)
        return { lec, q, n }
      })
      .sort((a, b) => b.n - a.n || a.lec - b.lec || a.q - b.q)
    const $w = el.querySelector('#ex-wrong')
    if (!rows.length) {
      $w.innerHTML = '<div class="empty">还没有错题记录，保持下去！</div>'
      return
    }
    $w.innerHTML =
      `<div class="wrong-stats">共 ${rows.length} 道错题，其中错 2 次以上 ${rows.filter((r) => r.n >= 2).length} 道</div>` +
      `<div class="wrong-grid">` +
      rows
        .map(
          (r) =>
            `<div class="wrong-item ${r.n >= 2 ? 'hot' : ''}">` +
            `<span class="wrong-n">×${r.n}</span>` +
            `<span class="wrong-lq">第${r.lec}讲 · 第${r.q}题</span>` +
            `<span class="wrong-t">${escapeHtml(lecTitle(r.lec))}</span>` +
            `</div>`,
        )
        .join('') +
      `</div>`
  }

  // ---------- 全部成绩 ----------
  async function renderHistory() {
    const list = (await fetchRecords(el)).slice().sort((a, b) => a.lecture - b.lecture || (a.date < b.date ? 1 : -1))
    const $h = el.querySelector('#ex-history')
    if (!list.length) {
      $h.innerHTML = '<div class="empty">还没有作答记录，先在上方记一次。</div>'
      return
    }
    const byLec = new Map()
    for (const r of list) {
      ;(byLec.get(r.lecture) || byLec.set(r.lecture, []).get(r.lecture)).push(r)
    }
    $h.innerHTML = [...byLec.entries()]
      .map(([lec, recs]) => {
        const best = Math.max(...recs.map((r) => r.score))
        const last = recs[0]
        return `
        <div class="his-group">
          <div class="his-head">
            <b>第${lec}讲</b> ${escapeHtml(lecTitle(lec))}
            <span class="his-meta">做了 ${recs.length} 次 · 最高 ${best} 分 · 最近 ${last.score} 分</span>
          </div>
          ${recs
            .map(
              (r) => `
            <div class="his-row" data-id="${r.id}">
              <span class="his-date">${escapeHtml(r.date)}</span>
              <span class="his-score ${r.score >= 90 ? 'good' : r.score >= 60 ? 'mid' : 'bad'}">${r.score} 分</span>
              <span class="his-wrong">${
                r.wrong.length ? r.wrong.map((q) => `<span class="wrong-chip">第${q}题</span>`).join('') : '<span class="all-right">全对 🎉</span>'
              }</span>
              <button class="his-del" title="删除这条记录">×</button>
            </div>`,
            )
            .join('')}
        </div>`
      })
      .join('')
  }

  // 删除单条
  el.querySelector('#ex-history').addEventListener('click', async (e) => {
    const del = e.target.closest('.his-del')
    if (!del) return
    const id = del.closest('.his-row').dataset.id
    try {
      await api.examsDelete(id)
      await refresh()
    } catch (err) {
      alert(`删除失败：${err.message}`)
    }
  })

  // 导出
  el.querySelector('#ex-export').addEventListener('click', async () => {
    const blob = new Blob([JSON.stringify(await fetchRecords(el), null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `作业成绩-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  })

  // 统一刷新：迁移旧数据 → 拉后端记录 → 重渲染
  async function refresh() {
    await migrateOnce()
    await Promise.all([renderWrong(), renderHistory()])
  }
  refresh()
}

// 拉记录（带错误提示）；挂载时 el 已在 DOM
async function fetchRecords(el) {
  try {
    return await api.examsList()
  } catch (e) {
    const tip = el.querySelector('.view-head p')
    if (tip && !tip.dataset.err) {
      tip.dataset.err = '1'
      tip.innerHTML += `<br><span style="color:#b91c1c">⚠ 成绩服务连接失败（${escapeHtml(e.message)}），检查后端是否在运行。</span>`
    }
    return []
  }
}

import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { getAuth } from '../api.js'

// 学生端 · 今日任务（内循环核心）+ 周清单。
// 任务由教师周计划（外循环）自动派生：微练习/错词/讲次/FCE/泛读。
// 完成态双路：自动检测（做题/背词/阅读即亮）+ 手动打卡（plan_day_checks）。
const TAB_KEY = 'gkb-today-tab-v1'

export async function mountToday(el) {
  const auth = getAuth()
  const user = auth ? auth.user : 'malin'
  const saved = (() => { try { return localStorage.getItem(TAB_KEY) } catch { return 'today' } })()
  const state = { tab: saved === 'week' ? 'week' : 'today', data: null, week: null }

  render()

  function render() {
    try { localStorage.setItem(TAB_KEY, state.tab) } catch { /* 忽略 */ }
    el.innerHTML = `
      <div class="view-head">
        <h1>今日任务</h1>
        <p>老师排的计划会自动变成每天的小任务，做完一样勾一样。</p>
      </div>
      <div class="ana-tabs today-subtabs">
        <button class="reading-btn ${state.tab === 'today' ? 'primary' : ''}" data-ttab="today">📌 今日清单</button>
        <button class="reading-btn ${state.tab === 'week' ? 'primary' : ''}" data-ttab="week">📅 本周任务</button>
      </div>
      <div id="today-body"><p class="muted">加载中…</p></div>
    `
    el.querySelectorAll('[data-ttab]').forEach((b) =>
      b.addEventListener('click', () => { state.tab = b.dataset.ttab; render() }))
    const body = el.querySelector('#today-body')
    if (state.tab === 'week') mountWeek(body)
    else mountDay(body)
  }

  // ---- 今日清单 ----
  async function mountDay(body) {
    let data
    try {
      data = await api.planToday()
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    state.data = data
    const tasks = data.tasks || []
    if (!tasks.length) {
      body.innerHTML = `
        <section class="td-card td-empty">
          <div class="td-empty-emoji">🎈</div>
          <h3>今天没有任务</h3>
          <p>老师这周还没排计划。可以去<a href="#/recite" class="td-link">背单词</a>、<a href="#/library" class="td-link">泛读馆</a>读会儿书。</p>
        </section>`
      return
    }
    const done = data.done || 0
    const total = data.total || tasks.length
    const pct = total ? Math.round((done / total) * 100) : 0
    body.innerHTML = `
      <section class="td-card">
        <header class="td-head">
          <h3>${dayLabel(data.date)}</h3>
          <div class="td-progress">
            <div class="td-bar"><i style="width:${pct}%"></i></div>
            <b>${done}/${total}</b>
          </div>
        </header>
        <div class="td-list">
          ${tasks.map((t) => taskRow(t)).join('')}
        </div>
      </section>`
    body.querySelectorAll('[data-check]').forEach((b) =>
      b.addEventListener('click', () => toggleCheck(b.dataset.check, b.dataset.on !== '1')))
  }

  function taskRow(t) {
    const icon = { micro_drill: '✏️', wrong_words: '🔤', lecture: '📚', fce: '🎧', reading: '📖' }[t.type] || '•'
    const canManual = !t.auto_done
    return `<div class="td-row ${t.done ? 'done' : ''}">
      <button class="td-check ${t.done ? 'on' : ''} ${t.auto_done ? 'auto' : ''}"
              data-check="${escapeHtml(t.key)}" data-on="${t.done && !t.auto_done ? 1 : 0}"
              ${canManual ? '' : 'disabled'} title="${t.auto_done ? '检测到今天已完成，自动打勾' : '点一下打卡'}">
        ${t.auto_done ? '⚡' : (t.done ? '✓' : '')}
      </button>
      <div class="td-main">
        <b>${icon} ${escapeHtml(t.text)}</b>
        ${t.detail ? `<i>${escapeHtml(t.detail)}</i>` : ''}
      </div>
      ${t.auto_done ? '<span class="td-auto-tag">自动</span>' : ''}
    </div>`
  }

  async function toggleCheck(taskKey, on) {
    try {
      await api.planDayCheck({
        user, day: state.data.date, task_key: taskKey, on,
      })
      const fresh = await api.planToday()
      state.data = fresh
      const list = el.querySelector('.td-list')
      if (list) list.innerHTML = (fresh.tasks || []).map((t) => taskRow(t)).join('')
      rebindChecks()
      const done = fresh.done || 0, total = fresh.total || 1
      const bar = el.querySelector('.td-bar i')
      if (bar) bar.style.width = `${total ? Math.round((done / total) * 100) : 0}%`
      const cnt = el.querySelector('.td-progress b')
      if (cnt) cnt.textContent = `${done}/${total}`
    } catch (e) { alert('打卡失败：' + e.message) }
  }

  function rebindChecks() {
    el.querySelectorAll('[data-check]').forEach((b) =>
      b.addEventListener('click', () => toggleCheck(b.dataset.check, b.dataset.on !== '1')))
  }

  // ---- 本周任务 ----
  async function mountWeek(body) {
    let wk
    try {
      wk = await api.planWeekTodo()
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    state.week = wk
    const g = wk.goals || {}
    const goalChips = []
    if (g.focus_kps?.length) goalChips.push(`🎯 攻坚：${g.focus_kps.map((l) => '第' + l + '讲').join('、')}`)
    if (g.lectures?.length) goalChips.push(`📚 ${[...g.lectures].sort((a, b) => a - b).join('/')}讲`)
    if (g.vocab_goal != null) goalChips.push(`🔤 累计${g.vocab_goal}词`)
    if (g.fce?.length) goalChips.push(`🎧 ${g.fce.join('+')}`)
    if (g.reading) for (const [k, v] of Object.entries(g.reading)) goalChips.push(`📖 ${k} ${v}%`)

    const wkDone = (wk.days || []).reduce((a, d) => a + (d.done || 0), 0)
    const wkTotal = (wk.days || []).filter((d) => !d.future).reduce((a, d) => a + (d.total || 0), 0)
    const todayIso = new Date().toISOString().slice(0, 10)

    body.innerHTML = `
      <section class="td-card">
        <header class="td-head">
          <h3>本周 · ${weekLabel(wk.week_start)}</h3>
          <div class="td-progress"><b>已完成 ${wkDone} 项</b></div>
        </header>
        ${goalChips.length
          ? `<div class="td-goals">${goalChips.map((c) => `<i>${escapeHtml(c)}</i>`).join('')}</div>`
          : '<p class="muted" style="margin:4px 0 10px">老师这周还没排计划。</p>'}
        <div class="td-week">
          ${(wk.days || []).map((d) => {
            const isToday = d.date === todayIso
            const dd = new Date(d.date + 'T00:00:00')
            const name = ['日', '一', '二', '三', '四', '五', '六'][dd.getDay()]
            const inner = d.future
              ? `<b>${Number(d.date.slice(8))}</b><i>周${name}</i><em>待来</em>`
              : `<b>${Number(d.date.slice(8))}</b><i>周${name}</i><em>${d.total ? `${d.done}/${d.total}` : '—'}</em>`
            return `<div class="td-day ${isToday ? 'today' : ''} ${d.future ? 'future' : ''} ${!d.future && d.total && d.done >= d.total ? 'fulled' : ''}">${inner}</div>`
          }).join('')}
        </div>
        ${wkTotal ? `<p class="td-week-sum">本周到现在共 ${wkTotal} 项任务，已完成 <b>${wkDone}</b> 项${wkDone >= wkTotal && wkTotal ? ' 🎉' : ''}</p>` : ''}
      </section>`
  }
}

function dayLabel(iso) {
  const d = new Date(iso + 'T00:00:00')
  const name = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 · 周${name}${iso === new Date().toISOString().slice(0, 10) ? ' · 今天' : ''}`
}

function weekLabel(ws) {
  const [y, m, d] = ws.split('-').map(Number)
  const end = new Date(y, m - 1, d + 6)
  return `${m}.${d}–${end.getMonth() + 1}.${end.getDate()}`
}

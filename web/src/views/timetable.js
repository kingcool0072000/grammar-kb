import { api } from '../api.js'
import { escapeHtml, todayIso } from '../render.js'

// 学生版 · 课程表：教师计划看板-周计划的只读镜像（/plan/my-week，
// week_view 同源数据）。只看自己 + 本周；展示周拆解汇总 + 周一至周日
// 逐日任务（完成态自动勾，来自学习行为的只读同步）。
const WEEK_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const TYPE_ICON = {
  lecture: '📚', fce: '🎧', paper: '📝', hw_paper: '🗒', article: '📄',
  reading: '📖', vocab: '🔤', speak: '🎤',
}

export async function mountTimetable(el) {
  el.innerHTML = '<div class="view-head"><h1>课程表</h1><p>加载中…</p></div>'
  let d
  try {
    d = await api.planMyWeek()
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }

  const tasks = d.tasks || {}
  const days = d.days || []
  const gp = d.goal_progress || {}
  const dims = gp.dims || []
  const today = todayIso()

  // 周拆解摘要（与教师看板同口径）。泛读按书显示章区间
  // （reading_items 由后端带 title/chapter_range——「读完第6–7章」
  // 比笼统的「1 本」信息量大；旧 pct 格式无区间则退回 N 本）
  const summary = []
  if (tasks.lectures?.length) summary.push(`📚 语法 ${tasks.lectures.length} 讲`)
  if (tasks.hw_papers?.length) summary.push(`🗒 作业卷 ${tasks.hw_papers.length} 卷`)
  if (tasks.vocab_goal) summary.push(`🔤 词汇目标 ${tasks.vocab_goal} 词`)
  if (tasks.fce?.length) summary.push(`🎧 FCE真题 ${tasks.fce.length} 项`)
  if (tasks.articles?.length) summary.push(`📄 精读 ${tasks.articles.length} 篇`)
  const rItems = d.breakdown?.reading_items || []
  if (rItems.length) {
    for (const r of rItems) {
      summary.push(r.chapter_range
        ? `📖 ${r.title || '泛读'} 读完${r.chapter_range}`
        : `📖 泛读 ${r.title || ''} ${r.goal_pct != null ? `到${r.goal_pct}%` : ''}`.trim())
    }
  } else {
    const readingBooks = Object.keys(tasks.reading || {}).length
    if (readingBooks) summary.push(`📖 泛读 ${readingBooks} 本`)
  }
  if (tasks.vocab_papers?.length) summary.push(`📝 试卷 ${tasks.vocab_papers.length} 份`)

  const dimRow = (x) => `
    <div class="tt-dim ${x.done >= x.total ? 'done' : ''}">
      <span class="tt-dim-icon">${x.icon}</span>
      <span class="tt-dim-name">${escapeHtml(x.name)}</span>
      <div class="tt-dim-bar"><i style="width:${Math.min(100, (x.done / (x.total || 1)) * 100)}%"></i></div>
      <b class="tt-dim-num">${round1(x.done)}/${x.total}</b>
    </div>`

  const dayCell = (day, i) => {
    const isToday = day.date === today
    // 只展示已完成的学习（课程表=学习记录视角）：完成计划任务 +
    // 计划外朗读 + 泛读/背单词实录统计（与教师计划看板日卡同口径）
    const doneTasks = (day.tasks || []).filter((t) => t.done)
    const items = doneTasks.map((t) => `
      <div class="tt-task ok">
        <span class="tt-task-ic">${TYPE_ICON[t.type] || '•'}</span>
        <span class="tt-task-text">${escapeHtml(t.text || t.key)}</span>
        <span class="tt-task-check">✓</span>
      </div>`).join('')
    const acts = day.acts || {}
    const extraRows = (acts.extra_speaks || []).map((x) => `
      <div class="tt-task ok">
        <span class="tt-task-ic">${TYPE_ICON.speak}</span>
        <span class="tt-task-text">${escapeHtml(x.text || '计划外朗读')}</span>
        <span class="tt-task-check">✓</span>
      </div>`).join('')
    const statRows = []
    for (const r of acts.readings || []) {
      const ch = r.from != null ? ` 第${r.from}${r.to !== r.from ? `–${r.to}` : ''}章` : ''
      statRows.push(`📖 泛读 ${String(r.book).slice(0, 14)}${ch} ${r.min} 分钟${r.words ? `（${r.words} 词）` : ''}`)
    }
    if (acts.vocab_n) statRows.push(`🔤 背单词 ${acts.vocab_n} 个${acts.vocab_min ? `（${acts.vocab_min} 分钟）` : ''}`)
    const statHtml = statRows.length
      ? `<div class="tt-acts">${statRows.map((s) => `<span>${escapeHtml(s)}</span>`).join('')}</div>` : ''
    const live = items || extraRows || statRows.length
    return `
      <section class="tt-day ${isToday ? 'today' : ''} ${day.future ? 'future' : ''}">
        <header>
          <b>${WEEK_CN[i]}</b>
          <span class="tt-day-date">${day.date.slice(5)}</span>
          ${isToday ? '<i class="tt-today-tag">今天</i>' : ''}
          <span class="tt-day-count">${day.future ? '' : `${day.done}/${day.total}`}</span>
        </header>
        ${live ? items + extraRows + statHtml : `<p class="tt-day-empty">${day.future ? '未开始' : '还没有完成的学习'}</p>`}
      </section>`
  }

  el.innerHTML = `
    <div class="view-head">
      <h1>课程表</h1>
      <p>本周（${d.week_start || ''} 起）已完成的学习记录 · 每天做了什么一目了然</p>
    </div>
    ${summary.length ? `<div class="tt-summary">${summary.map((s) => `<span>${s}</span>`).join('')}</div>` : ''}
    ${dims.length ? `
      <section class="tt-goalcard card">
        <h3>🎯 本周目标进度</h3>
        ${dims.map(dimRow).join('')}
      </section>` : ''}
    <div class="tt-grid">
      ${days.map(dayCell).join('')}
    </div>
    ${(d.notes || '').trim() ? `<section class="tt-notes card"><h3>📌 老师的话</h3><p>${escapeHtml(d.notes).replace(/\n/g, '<br>')}</p></section>` : ''}
  `
}

function round1(n) {
  return Math.round(Number(n || 0) * 10) / 10
}

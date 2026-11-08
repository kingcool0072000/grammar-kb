import { api } from '../api.js'
import { escapeHtml, escAttr, todayIso, studyGoalMin, taskWhy, taskWhyShort } from '../render.js'
import { openBoostRetell, parseBoostRef } from './boostRetell.js'

// 学生版 · 课程表：教师计划看板-周计划的只读镜像（/plan/my-week，
// week_view 同源数据）。只看自己 + 本周；展示周拆解汇总 + 周一至周日
// 逐日任务（完成态自动勾，来自学习行为的只读同步）。
const WEEK_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const TYPE_ICON = {
  lecture: '📚', fce: '🎧', paper: '📝', hw_paper: '🗒', article: '📄',
  reading: '📖', vocab: '🔤', speak: '🎤', wrong_words: '🔤',
  micro_drill: '✏️', video: '🎬', history: '•', plan: '•',
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
  // 今日闭环（v3）：后端 today.loop 聚合了背词/微练习/泛读/提升档当日状态。
  // 契约挂在 today 下；顶层 loop 也兜一手（并行后端落点漂移时不白屏）。
  // 旧后端两个字段都没有时整卡不渲染，不影响页面其它部分。
  const loop = (d.today && d.today.loop) || d.loop || null
  const boostRows = ((d.days || []).find((day) => day && day.date === today)?.tasks || [])
    .filter((t) => t && t.boost)

  // 周拆解摘要（与教师看板同口径）。泛读按书显示章区间
  // （reading_items 由后端带 title/chapter_range——「读完第6–7章」
  // 比笼统的「1 本」信息量大；旧 pct 格式无区间则退回 N 本）
  const summary = []
  if (tasks.lectures?.length) summary.push(`📚 语法 ${tasks.lectures.length} 讲`)
  if (tasks.hw_papers?.length) summary.push(`🗒 作业卷 ${tasks.hw_papers.length} 卷`)
  if (tasks.vocab_new != null && tasks.vocab_lv != null) {
    summary.push(`🔤 本周新掌握 L${tasks.vocab_lv} 词 ${tasks.vocab_new} 个`)
  } else if (tasks.vocab_goal) summary.push(`🔤 词汇目标 ${tasks.vocab_goal} 词`)
  if (tasks.fce?.length) summary.push(`🎧 FCE真题 ${tasks.fce.length} 项`)
  // 精读：显示文章名（比「N 篇」信息量大；完成态在维度条里看）
  const artTitles = (d.article_titles || []).filter((a) => a.title)
  if (artTitles.length) {
    const names = artTitles.map((a) => a.title.slice(0, 16)).join('、')
    summary.push(`📄 精读 ${names}`)
  } else if (tasks.articles?.length) {
    summary.push(`📄 精读 ${tasks.articles.length} 篇`)
  }
  const vidTitles = (d.video_titles || []).filter((v) => v.title)
  if (vidTitles.length) {
    summary.push(`🎬 视频 ${vidTitles.map((v) => escapeHtml(v.title.slice(0, 16))).join('、')}`)
  } else if (tasks.videos?.length) {
    summary.push(`🎬 视频 ${tasks.videos.length} 个`)
  }
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

  const dimRow = (x) => {
    const raw = x.total ? Math.round(x.done / x.total * 100) : null
    const width = raw == null ? 0 : Math.max(0, Math.min(100, raw))
    return `
    <div class="tt-dim ${x.done >= x.total && x.total ? 'done' : ''}">
      <span class="tt-dim-icon">${x.icon}</span>
      <span class="tt-dim-name">${escapeHtml(x.name)}</span>
      <div class="tt-dim-bar"><i style="width:${width}%"></i></div>
      <b class="tt-dim-num">${raw == null ? '—' : `${raw}%`}<small>${round1(x.done)}/${x.total}</small></b>
    </div>`
  }

  const dayCell = (day, i) => {
    const isToday = day.date === today
    // 任务行：精读篇目可点——直达阅读练习（列表里本周任务置顶标 📌）
    const ttTask = (t, ok) => {
      const why = taskWhy(t.why)
      const whyShort = day.future ? '' : taskWhyShort(t.why)
      const tip = [t.detail, why].filter(Boolean).join(' · ')
        || (t.type === 'article' ? '去阅读练习读全文 + 提交朗读' : '')
      // 提升档行（task_key 前缀 boost:）：未配置=灰字；其余点直达
      // （口诀卡→专题页 / 句型复述→复述面板）
      if (t.boost) {
        const b = t.boost
        const inner = `
          <span class="tt-task-ic">⭐</span>
          <span class="tt-task-text">${escapeHtml(t.text || t.label || t.key)}</span>
          ${ok ? '<span class="tt-task-check">✓</span>' : ''}`
        if (!b.configured)
          return `<div class="tt-task boost unconfig" title="本周未配置提升任务">${inner}</div>`
        return `<button type="button" class="tt-task link boost ${ok ? 'ok' : 'plan'}"
          data-boost-ref="${escAttr(b.item_ref || '')}" data-boost-kind="${escAttr(b.kind || '')}"
          data-boost-done="${ok ? '1' : '0'}" title="${escAttr(b.kind === 'retell' ? '打开句型复述' : '去专题页背口诀卡')}">${inner}</button>`
      }
      const inner = `
        <span class="tt-task-ic">${TYPE_ICON[t.type] || '•'}</span>
        <span class="tt-task-text">${escapeHtml(t.text || t.key)}</span>
        ${whyShort ? `<small class="tt-why">${escapeHtml(whyShort)}</small>` : ''}
        ${ok ? '<span class="tt-task-check">✓</span>' : ''}`
      if (t.type === 'article')
        return `<a class="tt-task link ${ok ? 'ok' : 'plan'}" href="#/reading"
          title="${escapeHtml(tip)}">${inner}</a>`
      if (t.type === 'video' && t.video_id)
        return `<a class="tt-task link ${ok ? 'ok' : 'plan'}" href="#/videoStudy?vid=${encodeURIComponent(t.video_id)}"
          title="${escapeHtml(tip || '看视频并复述')}">${inner}</a>`
      return `<div class="tt-task ${ok ? 'ok' : 'plan'}" title="${escapeHtml(tip)}">${inner}</div>`
    }
    // 已完成的学习（课程表=学习记录视角）：完成计划任务 +
    // 计划外朗读 + 泛读/背单词实录统计（与教师计划看板日卡同口径）
    const doneTasks = (day.tasks || []).filter((t) => t.done)
    const items = doneTasks.map((t) => ttTask(t, true)).join('')
    // 未完成项也列出，并带上原因（无记录 / 未达配额），避免 0% 被看成没打卡。
    const planItems = (day.tasks || []).filter((t) => !t.done).map((t) => ttTask(t, false)).join('')
    const acts = day.acts || {}
    const extraRows = (acts.extra_speaks || []).map((x) => `
      <div class="tt-task ok">
        <span class="tt-task-ic">${TYPE_ICON.speak}</span>
        <span class="tt-task-text">${escapeHtml(x.text || '计划外朗读')}</span>
        <span class="tt-task-check">✓</span>
      </div>`).join('')
    // 微练习完成打勾行（后端 acts.micro_done.done）
    const microRow = acts.micro_done?.done ? `
      <div class="tt-task ok">
        <span class="tt-task-ic">${TYPE_ICON.micro_drill}</span>
        <span class="tt-task-text">微练习</span>
        <span class="tt-task-check">✓</span>
      </div>` : ''
    const statRows = []
    for (const r of acts.readings || []) {
      const ch = r.from != null ? ` 第${r.from}${r.to !== r.from ? `–${r.to}` : ''}章` : ''
      statRows.push(`📖 泛读 ${String(r.book).slice(0, 14)}${ch} ${r.min} 分钟${r.words ? `（${r.words} 词）` : ''}`)
    }
    if (acts.vocab_n) statRows.push(`🔤 背单词 ${acts.vocab_n} 个${acts.vocab_min ? `（${acts.vocab_min} 分钟）` : ''}`)
    const statHtml = statRows.length
      ? `<div class="tt-acts">${statRows.map((s) => `<span>${escapeHtml(s)}</span>`).join('')}</div>` : ''
    // 每日有效学习时长 vs 目标（周中45/周末90）：与教师计划看板同口径
    const goalMin = studyGoalMin(day.date)
    const studyMin = acts.study_min || 0
    const minBadge = day.future ? '' :
      `<i class="tt-min ${studyMin >= goalMin ? 'ok' : ''}" title="每日目标 ${goalMin} 分钟（周中45/周末90）">${studyMin ? `${studyMin}′` : '0′'}/${goalMin}′${studyMin >= goalMin ? ' ✓' : ''}</i>`
    const live = items || planItems || extraRows || microRow || statRows.length
    return `
      <section class="tt-day ${isToday ? 'today' : ''} ${day.future ? 'future' : ''}">
        <header>
          <b>${WEEK_CN[i]}</b>
          <span class="tt-day-date">${day.date.slice(5)}</span>
          ${isToday ? '<i class="tt-today-tag">今天</i>' : ''}
          ${minBadge}
          <span class="tt-day-count" title="${day.future ? '还没到这一天' : '按学习信号计算，不看打卡'}">${dayRateLabel(day)}</span>
        </header>
        ${day.future
          ? (planItems || `<p class="tt-day-empty">无安排</p>`)
          : (live ? items + planItems + extraRows + microRow + statHtml
            : `<p class="tt-day-empty">当天没有计划项</p>`)}
      </section>`
  }

  el.innerHTML = `
    <div class="view-head">
      <h1>课程表</h1>
      <p>本周（${d.week_start || ''} 起）${studentWeekRate(d.completion)} · 按学习信号自动计算，不用打卡</p>
    </div>
    ${loop ? loopCardHtml(loop) : ''}
    <div id="tt-micro"></div>
    <div id="tt-loop"></div>
    ${summary.length ? `<div class="tt-summary">${summary.map((s) => `<span>${s}</span>`).join('')}</div>` : ''}
    ${dims.length ? `
      <section class="tt-goalcard card">
        <h3>🎯 本周目标进度</h3>
        ${gp.percent == null ? '' : `<p class="tt-goal-sum">综合 ${gp.percent}% · 以各模块为准</p>`}
        ${dims.map(dimRow).join('')}
      </section>` : ''}
    <div class="tt-grid">
      ${days.map(dayCell).join('')}
    </div>
    ${(d.notes || '').trim() ? `<section class="tt-notes card"><h3>📌 老师的话</h3><p>${escapeHtml(d.notes).replace(/\n/g, '<br>')}</p></section>` : ''}
  `
  if (loop) bindLoopCard(el, loop, boostRows)
  bindBoostRows(el, boostRows)
  renderReviewStrip(el.querySelector('#tt-loop'))
  renderMicroCard(el.querySelector('#tt-micro'), d.micro_enabled)
}

// ---------- 今日闭环卡（v3 日闭环：到期背词/微练习/泛读/提升档） ----------
// 数据来自 /plan/my-week 的 today.loop；字段缺失按 falsy 兜底，不白屏。
function loopCardHtml(loop) {
  const recite = loop.recite || {}
  const micro = loop.micro || {}
  const reading = loop.reading || {}
  const boost = loop.boost || null  // null=今天不是提升日，整格不渲染

  const cells = []
  cells.push(recite.clear
    ? { name: '🔁 到期背词', done: true }
    : {
        name: '🔁 到期背词',
        state: `剩 ${Number(recite.due) || 0} · 逾期 ${Number(recite.overdue) || 0}`,
        go: 'recite', goText: '去复习',
      })
  cells.push(micro.done
    ? { name: '📝 微练习', done: true }
    : {
        name: '📝 微练习',
        state: `${Number(micro.answered) || 0}/${Number(micro.total) || 3}`,
        go: 'micro', goText: '去做题',
      })
  cells.push(reading.ok
    ? { name: '📖 泛读', done: true }
    : {
        name: '📖 泛读',
        state: (Number(reading.target_words) || 0) > 0
          ? `今日 ${Number(reading.today_words) || 0}/${Number(reading.target_words)} 词`
          : `今日 ${Number(reading.today_words) || 0} 词`,
        go: 'reading', goText: '去泛读',
      })
  if (boost) {
    cells.push(boost.configured === false
      ? { name: '⭐ 提升档', state: '本周未配置提升任务', na: true }
      : boost.done
        ? { name: '⭐ 提升档', done: true }
        : { name: '⭐ 提升档', state: boost.label || '今日提升任务', boost: true })
  }

  // 全绿判定：未配置提升任务（na）不算欠账，不挡闭环达成
  const allGreen = cells.every((c) => c.done || c.na)
  const streak = Number(loop.streak) || 0
  const nq = loop.next_queue || {}
  const nqBits = [
    (Number(nq.recite_words) || 0) ? `背词 ${Number(nq.recite_words)} 词` : '',
    (Number(nq.micro_questions) || 0) ? `练习 ${Number(nq.micro_questions)} 题` : '',
  ].filter(Boolean)

  return `
    <section class="tt-loopcard card">
      <h3>🔄 今日闭环</h3>
      <div class="tt-loop-grid" style="grid-template-columns:repeat(${cells.length},minmax(0,1fr))">
        ${cells.map((c) => `
          <div class="tt-loop-cell ${c.done ? 'done' : c.na ? 'na' : 'todo'}" ${c.boost ? 'data-boost-cell' : ''}>
            <span class="tt-loop-name">${c.name}</span>
            <b class="tt-loop-state" title="${escAttr(c.state || '')}">${c.done ? '✓' : escapeHtml(c.state || '')}</b>
            ${c.go ? `<button type="button" class="tt-loop-go" data-loop-go="${c.go}">${c.goText} →</button>` : ''}
            ${c.boost ? '<button type="button" class="tt-loop-go" data-boost-go>去完成 →</button>' : ''}
          </div>`).join('')}
      </div>
      ${allGreen ? `<p class="tt-loop-alldone">✅ 今日闭环达成${streak > 1 ? ` · 连续 ${streak} 天` : ''}</p>` : ''}
      ${nqBits.length ? `<p class="tt-loop-next">明日回炉：${nqBits.join(' · ')}</p>` : ''}
    </section>`
}

function bindLoopCard(el, loop, boostRows) {
  el.querySelectorAll('[data-loop-go]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const which = btn.dataset.loopGo
      if (which === 'recite') {
        const over = Number(loop.recite && loop.recite.overdue) || 0
        sessionStorage.setItem('gkb-recite-review', over > 0 ? 'overdue' : 'due')
        location.hash = '/recite'
      } else if (which === 'micro') {
        location.hash = '/micro'
      } else if (which === 'reading') {
        location.hash = '/library'
      }
    })
  })
  const boostGo = el.querySelector('[data-boost-go]')
  if (boostGo) {
    boostGo.addEventListener('click', () => {
      const actionable = boostRows.filter((t) => t.boost && t.boost.configured !== false)
      const row = actionable.find((t) => !t.done) || actionable[0]
      if (row) openBoostItem(el, boostRows, row.boost.item_ref, row.boost.kind)
    })
  }
}

// 今日任务里的提升档行（task_key 前缀 boost:）：口诀卡→专题页；句型复述→复述面板
function bindBoostRows(el, boostRows) {
  el.querySelectorAll('[data-boost-ref]').forEach((btn) => {
    btn.addEventListener('click', () =>
      openBoostItem(el, boostRows, btn.dataset.boostRef, btn.dataset.boostKind))
  })
}

function openBoostItem(el, boostRows, itemRef, kind) {
  const ref = parseBoostRef(itemRef, kind)
  if (!ref) return
  if (kind === 'retell') {
    if (ref.patternId == null) return
    const row = boostRows.find((t) => t.boost && t.boost.item_ref === itemRef)
    openBoostRetell({
      patternId: ref.patternId,
      label: (row && (row.text || row.label)) || '',
      onSubmitted: () => markBoostDone(el),
    })
    return
  }
  // memory：翻面自评在专题页完成，这里负责送到
  if (ref.topicId) location.hash = `/topics/${ref.topicId}`
}

// 句型复述提交成功后，把本页提升档的格子和今天的任务行就地翻成完成态
// （后端口径「当日有提交即完成」，刷新后与服务器状态一致）
function markBoostDone(el) {
  el.querySelectorAll('[data-boost-cell]').forEach((c) => {
    c.classList.remove('todo', 'na')
    c.classList.add('done')
    const state = c.querySelector('.tt-loop-state')
    if (state) state.textContent = '✓'
    const go = c.querySelector('[data-boost-go]')
    if (go) go.remove()
  })
  // 只翻今天格子里的行：未来提升日的行不受今天提交影响
  const todayCell = el.querySelector('.tt-day.today')
  if (!todayCell) return
  todayCell.querySelectorAll('[data-boost-ref]').forEach((btn) => {
    btn.dataset.boostDone = '1'
    btn.classList.remove('plan')
    btn.classList.add('ok')
    if (!btn.querySelector('.tt-task-check'))
      btn.insertAdjacentHTML('beforeend', '<span class="tt-task-check">✓</span>')
  })
}

// 微练习入口卡：未做 → 去做题；已做 → 点开回看。
// 本周计划未启用微练习（my-week 的 micro_enabled === false）时不渲染卡；
// undefined（旧后端）/true 照常显示。接口失败或今天没题也静默隐藏。
async function renderMicroCard(host, microEnabled) {
  if (!host) return
  if (microEnabled === false) { host.innerHTML = ''; return }
  let d
  try {
    d = await api.microToday()
  } catch {
    host.innerHTML = ''
    return
  }
  d = d || {}
  const qs = (d.questions || []).length
  if (!d.done && !qs) { host.innerHTML = ''; return }
  let inner
  if (d.done) {
    // correct_n 不在 today 返回里：拿 answered 幂等重放一次提交换回对错数
    let score = ''
    const known = Object.entries(d.answered || {})
      .map(([id, choice]) => ({ id: Number(id), choice }))
    if (known.length) {
      try {
        const res = await api.microSubmit(known)
        if (res && res.correct_n != null) {
          score = `（答对 ${res.correct_n}/${(res.results || []).length || known.length}）`
        }
      } catch { /* 拿不到对错就只报完成 */ }
    }
    inner = `<span class="tt-micro-main">✏️ 微练习已完成 ✓${score}</span><small>点开回看</small>`
  } else {
    inner = `<span class="tt-micro-main">✏️ 今日微练习 ${qs} 题</span><small>现在做 →</small>`
  }
  host.innerHTML = `<button type="button" class="tt-micro-card ${d.done ? 'done' : ''}">${inner}</button>`
  host.querySelector('.tt-micro-card').addEventListener('click', () => {
    location.hash = '/micro'
  })
}

async function renderReviewStrip(host) {
  if (!host) return
  let s
  try {
    s = await api.reviewSummary()
  } catch {
    host.innerHTML = ''
    return
  }
  const due = Number(s.due_today || 0)
  const over = Number(s.overdue || 0)
  const d7 = Number((s.conquered_n && s.conquered_n.d7) || 0)
  const go = (which) => {
    if (which === 'conquered') {
      location.hash = '/conquered'
      return
    }
    if ((which === 'overdue' ? over : due) <= 0) return
    sessionStorage.setItem('gkb-recite-review', which === 'overdue' ? 'overdue' : 'due')
    location.hash = '/recite'
  }
  host.innerHTML = `
    <div class="rc-loop tt-loop">
      <button type="button" class="rc-loop-card due" ${due ? '' : 'disabled'} data-go="due">
        <b>${due}</b><span>到期</span><small>去复习</small>
      </button>
      <button type="button" class="rc-loop-card over" ${over ? '' : 'disabled'} data-go="overdue">
        <b>${over}</b><span>逾期</span><small>去复习</small>
      </button>
      <button type="button" class="rc-loop-card cq" data-go="conquered">
        <b>${d7}</b><span>近7日攻克</span><small>打开词本</small>
      </button>
    </div>`
  host.querySelectorAll('[data-go]').forEach((btn) => {
    btn.addEventListener('click', () => go(btn.dataset.go))
  })
}

function round1(n) {
  return Math.round(Number(n || 0) * 10) / 10
}

function dayRateLabel(day) {
  if (day.future) return day.total ? `${day.total} 项` : '待来'
  if (!day.total) return '—'
  const pct = day.percent != null ? day.percent : Math.round(day.done / day.total * 100)
  return `${pct}%`
}

function studentWeekRate(comp) {
  if (!comp || !comp.total) return '本周已到的日子还没有计划项'
  return `完成率 ${comp.percent}%`
}

import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 计划表：周历视图。本周大卡（今日任务）+ 月历式周网格往下排，
// 学情分析并入为子 Tab（#/plan?tab=analytics / #/plan 切换）。
// 计划数据由 scripts/seed_plan_weeks.py 自动排程预填，教师可编辑微调。
// 词汇考试：登记成绩（≥80 解锁下一级）+ 打印考卷入口。
export async function mountPlan(el) {
  const subTab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab') || 'board'
  const state = {
    tab: subTab === 'students' ? 'students' : 'board',
    view: 'week',   // week | month | overview
    student: 'malin',
  }

  render()

  function render() {
    el.innerHTML = `
      <div class="ana-tabs plan-subtabs">
        <button class="reading-btn ${state.tab === 'board' ? 'primary' : ''}" data-ptab="board">📋 计划看板</button>
        <button class="reading-btn ${state.tab === 'students' ? 'primary' : ''}" data-ptab="students">🎒 学生管理</button>
      </div>
      <div id="plan-body"></div>
    `
    el.querySelectorAll('[data-ptab]').forEach((b) =>
      b.addEventListener('click', () => { state.tab = b.dataset.ptab; render() }))
    const body = el.querySelector('#plan-body')
    if (state.tab === 'students') {
      import('./students.js').then(({ mountStudents }) => {
        const host = document.createElement('div')
        body.replaceChildren(host)
        mountStudents(host, { bare: true })
      })
    } else {
      mountCalendar(body)
    }
  }

  async function mountCalendar(body) {
    body.innerHTML = '<p class="muted">加载中…</p>'
    let data
    try {
      data = await api.planWeeks({ user: state.student })
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    const { weeks = [], current_week: curWeek } = data
    const byStart = new Map(weeks.map((w) => [w.week_start, w]))
    const today = new Date().toISOString().slice(0, 10)
    // 周历视图状态：默认本周，可前后翻；月历当前月
    let viewWeek = curWeek
    let viewMonth = today.slice(0, 7)
    // 视图：week 周 | month 月 | overview 全览
    const view = { mode: 'week' }
    let students = ['malin']
    try {
      const ur = await api.users()
      const studs = (ur.users || []).filter((u) => u.role === 'student').map((u) => u.user)
      if (studs.length) students = studs
    } catch { /* 保持默认 */ }
    if (!students.includes(state.student)) state.student = students[0]
    // 学生管理页跳转携带的学生
    const fromStore = sessionStorage.getItem('gkb-plan-student')
    if (fromStore && students.includes(fromStore)) {
      state.student = fromStore
      sessionStorage.removeItem('gkb-plan-student')
    }
    let goals = null
    try { goals = (await api.planGoals({ user: state.student })).goals } catch { goals = null }

    let goalProg = null
    try { goalProg = await api.planGoalProgress({ user: state.student }) } catch { goalProg = null }
    // 目标卡模式：week 周（默认，随翻周联动）/ total 总
    let goalMode = 'week'
    let weekProg = null // 当前展示周的周目标进度（week-view 返回）

    body.innerHTML = `
      <div class="pw-stubar">
        <label class="pw-student">学生
          <select id="pw-user"></select>
        </label>
        <button class="chip" id="pw-cfg-goal">🎯 配置 ${escapeHtml(state.student)} 的总目标</button>
        <span class="pw-flex"></span>
        <div class="pw-modes">
          <button class="chip ${view.mode === 'week' ? 'primary' : ''}" data-vmode="week">📅 周历</button>
          <button class="chip ${view.mode === 'month' ? 'primary' : ''}" data-vmode="month">🗓 月历</button>
          <button class="chip ${view.mode === 'overview' ? 'primary' : ''}" data-vmode="overview">🗺 全览</button>
        </div>
      </div>
      ${'' /* 目标卡容器：周/总切换内容由 renderGoalCard 填充 */}
      <div id="goal-card"></div>
      <div id="plan-view"></div>
    `
    renderGoalCard()
    body.querySelector('#pw-cfg-goal').addEventListener('click', () => {
      sessionStorage.setItem('gkb-open-goal', state.student)
      const t = el.querySelector('[data-ptab="students"]')
      if (t) t.click()
    })
    const goCfg = body.querySelector('#gp-go-cfg')
    if (goCfg) goCfg.addEventListener('click', () => {
      sessionStorage.setItem('gkb-open-goal', state.student)
      const t = el.querySelector('[data-ptab="students"]')
      if (t) t.click()
    })
    body.querySelector('#pw-user').innerHTML = students.map((x) =>
      `<option value="${escapeHtml(x)}" ${state.student === x ? 'selected' : ''}>${escapeHtml(x)}</option>`).join('')
    const viewHost = body.querySelector('#plan-view')

    body.querySelectorAll('[data-vmode]').forEach((b) =>
      b.addEventListener('click', () => {
        view.mode = b.dataset.vmode
        body.querySelectorAll('[data-vmode]').forEach((x) =>
          x.classList.toggle('primary', x.dataset.vmode === view.mode))
        renderView()
      }))
    body.querySelector('#pw-user').addEventListener('change', (e) => {
      state.student = e.target.value
      // 学生切换 = 换目标/进度/编辑器数据源：整个看板重载
      // （goals·goalProg·编辑器数据都是按学生拉的，仅 renderView 不够）
      mountCalendar(body)
    })

    function renderView() {
      if (view.mode === 'month') renderMonth()
      else if (view.mode === 'overview') renderOverview()
      else renderWeeks()
    }
    renderView()




    // 统一目标卡：周目标/总目标切换（默认周），两种模式同构「目标+进度」展示
    function progressCardHtml() {
      const isWeek = goalMode === 'week'
      const gp = isWeek ? weekProg : goalProg
      const title = isWeek
        ? `🎯 本周目标 · ${weekProg?.week_label || ''}`
        : `🎯 ${escapeHtml(state.student)} 的总目标`
      const emptyTxt = isWeek ? '本周未排计划' : `${escapeHtml(state.student)} 的总目标未配置`
      const emptyBtn = isWeek
        ? '<button class="chip" data-edit-cur="1">去排本周计划</button>'
        : '<button class="chip" id="gp-go-cfg">去配置</button>'
      if (!gp || gp.percent == null || !(gp.dims || []).length) {
        return `<div class="gp-card gp-empty">
          <span>${title} · ${emptyTxt}</span>
          ${emptyBtn}
        </div>`
      }
      const bars = gp.dims.map((d) => {
        const pct = d.total ? Math.min(100, Math.round(d.done / d.total * 100)) : 0
        return `<div class="gp-dim">
          <span class="gp-dim-name">${d.icon} ${escapeHtml(d.name)}${d.extra ? `<i>${escapeHtml(d.extra)}</i>` : ''}</span>
          <div class="gp-dim-bar"><i style="width:${pct}%"></i></div>
          <b class="gp-dim-n">${d.done}/${d.total}</b>
        </div>`
      }).join('')
      return `<div class="gp-card">
        <div class="gp-head">
          <div class="gp-ring" style="--p:${gp.percent}">
            <b>${gp.percent}%</b><span>${isWeek ? '周进度' : '总进度'}</span>
          </div>
          <div class="gp-meta">
            <b>${title}</b>
            <span class="muted">${gp.done_items}/${gp.total_items} 项完成${!isWeek && gp.deadline ? ` · 截止 ${gp.deadline}` : ''}</span>
          </div>
          <div class="gp-mode-tabs">
            <button class="chip ${isWeek ? 'primary' : ''}" data-gmode="week">周目标</button>
            <button class="chip ${!isWeek ? 'primary' : ''}" data-gmode="total">总目标</button>
          </div>
        </div>
        <div class="gp-dims">${bars}</div>
      </div>`
    }

    function renderGoalCard() {
      const host = body.querySelector('#goal-card')
      if (!host) return
      host.innerHTML = progressCardHtml()
      host.querySelectorAll('[data-gmode]').forEach((b) =>
        b.addEventListener('click', () => {
          goalMode = b.dataset.gmode
          renderGoalCard()
        }))
      const cfg = host.querySelector('#gp-go-cfg')
      if (cfg) cfg.addEventListener('click', () => {
        sessionStorage.setItem('gkb-open-goal', state.student)
        const t = el.querySelector('[data-ptab="students"]')
        if (t) t.click()
      })
      const editCur = host.querySelector('[data-edit-cur]')
      if (editCur) editCur.addEventListener('click', () => openEditor(viewWeek))
    }

    // ---- 周历视图：← 本周 → 翻周，7 天格子逐日任务 ----
    async function renderWeeks() {
      const host = viewHost
      host.innerHTML = '<p class="muted">周历加载中…</p>'
      let wv
      try {
        wv = await api.planWeekView({ weekStart: viewWeek, user: state.student })
      } catch (e) {
        host.innerHTML = `<p style="color:#b42318">周历加载失败：${escapeHtml(e.message)}</p>`
        return
      }
      const t = wv.tasks || {}
      const isCur = wv.week_start === curWeek
      const seeded = [...byStart.keys()].sort()
      const earliest = seeded.length ? seeded[0] : iso(new Date(new Date(curWeek + 'T00:00:00').getTime() - 14 * 86400000))
      // 周目标进度进统一目标卡（翻周联动；目标卡默认显示周模式）
      weekProg = { ...(wv.goal_progress || null), week_label: weekLabel(wv.week_start) }
      renderGoalCard()

      const dayNames = ['一', '二', '三', '四', '五', '六', '日']
      host.innerHTML = `
        <section class="pw-view">
          <header class="pw-nav">
            <button class="chip" data-wk="prev" ${viewWeek <= earliest ? 'disabled' : ''}>← 上一周</button>
            <div class="pw-title">
              <h3>${isCur ? '本周' : '周'} · ${weekLabel(wv.week_start)}${wv.current_week ? '' : wv.week_start > curWeek ? '（未开始）' : '（已结束）'}</h3>
            </div>
            <button class="chip" data-wk="next" ${wv.week_start >= '2027-01-25' ? 'disabled' : ''}>下一周 →</button>
            <button class="chip" data-edit="${wv.week_start}">✏️ 编辑本周</button>
            ${isCur ? '' : '<button class="chip" data-wk="cur">回到本周</button>'}
          </header>
          <div class="pw-grid">
            ${(wv.days || []).map((d, i) => dayCell(d, i, dayNames)).join('')}
          </div>
        </section>`
      host.querySelectorAll('[data-wk]').forEach((b) =>
        b.addEventListener('click', () => {
          const k = b.dataset.wk
          if (k === 'cur') viewWeek = curWeek
          else if (k === 'prev') viewWeek = addDays(viewWeek, -7)
          else viewWeek = addDays(viewWeek, 7)
          renderWeeks()
        }))
      host.querySelectorAll('[data-edit]').forEach((b) =>
        b.addEventListener('click', () => openEditor(b.dataset.edit)))
    }

    function dayCell(d, i, dayNames) {
      const dd = Number(d.date.slice(8))
      const isToday = d.date === today
      const icon = { micro_drill: '✏️', wrong_words: '🔤', lecture: '📚', fce: '🎧', reading: '📖', speak: '🎤', article: '📄', paper: '📝' }
      // 单日卡片只显示当天完成的工作；未完成的计划不在卡上列出。
      // 计划外的工作（朗读/泛读/背单词量化实录）由 acts 补充显示。
      const rows = (d.tasks || []).filter((x) => x.done).map((x) => `
        <div class="pw-task done">
          <i>${icon[x.type] || '•'}</i>
          <span title="${escapeHtml(x.text)}">${escapeHtml(x.text)}</span>
          <em>✓</em>
        </div>`).join('')
      const acts = d.acts || {}
      const extraRows = (acts.extra_speaks || []).map((x) => `
        <div class="pw-task done">
          <i>${icon[x.type] || '🎤'}</i>
          <span title="${escapeHtml(x.text)}">${escapeHtml(x.text)}</span>
          <em>✓</em>
        </div>`).join('')
      const statRows = []
      for (const r of acts.readings || []) {
        const ch = r.from != null ? ` 第${r.from}${r.to !== r.from ? `–${r.to}` : ''}章` : ''
        statRows.push(`📖 泛读 ${String(r.book).slice(0, 14)}${ch} ${r.min} 分钟${r.words ? `（${r.words} 词）` : ''}`)
      }
      if (acts.vocab_n) statRows.push(`🔤 背单词 ${acts.vocab_n} 个${acts.vocab_min ? `（${acts.vocab_min} 分钟）` : ''}`)
      const statHtml = statRows.length
        ? `<div class="pw-acts">${statRows.map((s) => `<span>${escapeHtml(s)}</span>`).join('')}</div>` : ''
      const full = !d.future && d.total > 0 && d.done >= d.total
      const hasLive = Boolean(rows || extraRows || statRows.length)
      return `<div class="pw-day ${isToday ? 'today' : ''} ${d.future ? 'future' : ''} ${full ? 'fulled' : ''}">
        <div class="pw-day-head">
          <b>周${dayNames[i]}</b><span>${dd} 日</span>
          <em>${d.future ? '待来' : (d.done ? `${d.done} ✓` : '—')}</em>
        </div>
        <div class="pw-tasks">${hasLive ? rows + extraRows + statHtml : (d.future ? '' : '<i class="pw-none">无完成</i>')}</div>
      </div>`
    }

    // 周目标 chips（周历「本周目标」区与全览行共用，口径一致）
    function weekGoalChips(t, ed) {
      const chips = []
      if (t.focus_kps?.length) chips.push(`🎯 攻坚 ${t.focus_kps.map((l) => '第' + l + '讲').join('、')}`)
      if (t.lectures?.length) chips.push(`📚 语法课程 ${[...t.lectures].sort((a, b) => a - b).join('/')}讲`)
      if (t.vocab_goal != null) {
        chips.push(t.vocab_lv != null
          ? `🔤 词汇 学到L${t.vocab_lv}的${t.vocab_pct ?? 100}%（${t.vocab_goal}词）`
          : `🔤 词汇 累计${t.vocab_goal}词`)
      }
      if (t.fce?.length) chips.push(`🎧 FCE ${t.fce.join('+')}`)
      const books = ed?.books || []
      for (const [k, v] of Object.entries(t.reading || {})) {
        const b = /^\d+$/.test(k)
          ? books.find((x) => String(x.id) === k)
          : books.find((x) => x.title.toLowerCase().includes(k.toLowerCase()))
        if (Array.isArray(v)) {
          const optMap = new Map((b?.chapter_opts || []).map((c) => [c.idx, c]))
          const nos = v.map((i) => optMap.get(i)?.no).filter(Boolean)
          chips.push(`📖 泛读 ${(b ? b.title : k).slice(0, 14)} ${nos.length > 2 ? `第${Math.min(...nos)}–${Math.max(...nos)}章` : nos.map((n) => `第${n}章`).join('、')}`)
        } else {
          chips.push(`📖 泛读 ${(b ? b.title : k).slice(0, 14)} → ${v}%`)
        }
      }
      if (t.articles?.length) chips.push(`📄 精读 ${t.articles.length} 篇`)
      if (t.vocab_papers?.length) chips.push(`📝 试卷 ${t.vocab_papers.length} 卷`)
      if (t.speak) chips.push(`🎤 朗读 ${t.speak} 篇`)
      return chips
    }

    // 阅读实况文案：全书口径——读到第几章（该章占全书的百分比区间）、
    // 已读/总词数、累计阅读时长。区间和词数由后端按各章 word_count 算出。




    // ---- 月历视图：数据源与周历同口径（week-view），卡片渲染复用 dayCell ----
    let monthData = null
    async function renderMonth() {
      viewHost.innerHTML = '<p class="muted">月历加载中…</p>'
      const [y, m] = viewMonth.split('-').map(Number)
      const prevM = m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`
      const nextM = m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`
      // 该月覆盖的周（首周周一 ~ 末周周日），逐周拉 week-view 合并日数据
      const firstDay = new Date(y, m - 1, 1)
      const lastDay = new Date(y, m, 0)
      const mon = (d) => iso(new Date(d.getTime() - (d.getDay() + 6) % 7 * 86400000))
      const weeks = []
      for (let ws = mon(firstDay); ws <= iso(lastDay); ws = addDays(ws, 7)) weeks.push(ws)
      let dayMap = new Map()
      try {
        const wvs = await Promise.all(weeks.map((ws) =>
          api.planWeekView({ weekStart: ws, user: state.student }).catch(() => null)))
        for (const wv of wvs) {
          if (!wv) continue
          for (const d of wv.days || []) dayMap.set(d.date, d)
        }
      } catch (e) {
        viewHost.innerHTML = `<p style="color:#b42318">月历加载失败：${escapeHtml(e.message)}</p>`
        return
      }
      monthData = { days: [...dayMap.values()].sort((a, b) => a.date < b.date ? -1 : 1) }
      // 月网格：首行前置空位对齐周几，只显示该月日期
      const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7
      const dim = lastDay.getDate()
      const cells = []
      for (let i = 0; i < lead; i++) cells.push('<div class="pm-day out"></div>')
      const names = ['一', '二', '三', '四', '五', '六', '日']
      for (let i = 1; i <= dim; i++) {
        const dd = String(i).padStart(2, '0')
        const date = `${viewMonth}-${dd}`
        const d = dayMap.get(date) || { date, future: date > today, done: 0, total: 0, tasks: [], acts: {} }
        cells.push(dayCell(d, (lead + i - 1) % 7, names))
      }
      viewHost.innerHTML = `
        <section class="pw-view">
          <header class="pw-nav">
            <button class="chip" data-mnav="prev">← 上月</button>
            <div class="pw-title"><h3>${y} 年 ${m} 月</h3>
              <p class="muted">${state.student} · 与周历同口径的逐日完成与实录</p></div>
            <button class="chip" data-mnav="next">下月 →</button>
          </header>
          <div class="pw-grid pm-month-grid">${cells.join('')}</div>
        </section>`
      viewHost.querySelectorAll('[data-mnav]').forEach((b) =>
        b.addEventListener('click', () => {
          viewMonth = b.dataset.mnav === 'prev' ? prevM : nextM
          renderMonth()
        }))
    }

    // ---- 全览视图：每周一行，chips 与周历「本周目标」区同源同口径 ----
    async function renderOverview() {
      viewHost.innerHTML = '<p class="muted">全览加载中…</p>'
      let ov
      try {
        ov = await api.planOverview({ user: state.student })
      } catch (e) {
        viewHost.innerHTML = `<p style="color:#b42318">全览加载失败：${escapeHtml(e.message)}</p>`
        return
      }
      const ed = await getEditorData()
      viewHost.innerHTML = `
        <section class="pw-view">
          <header class="pw-nav">
            <div class="pw-title"><h3>冲刺全览 · 至 2027-01-31</h3>
              <p class="muted">${state.student} · 每周计划与完成汇总（点周行进周历）</p></div>
          </header>
          <div class="po-list">
            ${(ov.weeks || []).map((w) => {
              const chips = weekGoalChips(w, ed)
              const pct = w.total ? Math.round((w.done / w.total) * 100) : null
              return `<div class="po-row ${w.past ? 'past' : ''} ${w.is_current ? 'cur' : ''} ${w.has_plan ? '' : 'noplan'}" data-ws="${w.week_start}">
                <span class="po-date">${weekLabel(w.week_start)}</span>
                <span class="po-chips">${chips.length ? chips.map((c) => `<i>${escapeHtml(c)}</i>`).join('') : '<em class="muted">未排计划</em>'}</span>
                <span class="po-done">${w.total ? `<b>${pct}%</b>（${w.done}/${w.total}）` : ''}</span>
              </div>`
            }).join('')}
          </div>
        </section>`
      viewHost.querySelectorAll('[data-ws]').forEach((r) =>
        r.addEventListener('click', () => {
          viewWeek = r.dataset.ws
          view.mode = 'week'
          body.querySelectorAll('[data-vmode]').forEach((x) =>
            x.classList.toggle('primary', x.dataset.vmode === 'week'))
          renderWeeks()
        }))
    }

    // ---- 周编辑器：结构化选项全部来自备课内容（讲次/FCE Part/书目/词汇） ----
    let editorDataCache = null
    async function getEditorData() {
      if (!editorDataCache) {
        try { editorDataCache = await api.planEditorData({ user: state.student }) }
        catch { editorDataCache = { lectures: [], fce_parts: [], books: [], vocab_mastered: 0 } }
      }
      return editorDataCache
    }

    function normalizeReadingKey(key, books) {
      // 旧数据可能是书名关键词；归一为 book id 字符串（匹配不到原样保留）
      if (/^\d+$/.test(String(key))) return String(key)
      const segs = String(key).toLowerCase().match(/[a-z]{3,}/g) || []
      if (segs.length) {
        const hit = books.find((b) => segs.every((w) => b.title.toLowerCase().includes(w)))
        if (hit) return String(hit.id)
      }
      return String(key)
    }

    async function openEditor(ws) {
      const w = byStart.get(ws) || { week_start: ws, tasks: {}, notes: '' }
      const t = w.tasks || {}
      const ed = await getEditorData()
      const books = ed.books || []
      const lectures = ed.lectures || []
      const goals = ed.goals || {}
      const done = ed.done || {}
      const lecTitle = (n) => {
        const l = lectures.find((x) => x.number === n)
        return l ? l.title : ''
      }
      // 目标池与补充池：goal 选中的进「从总目标选」（已完成过滤）；
      // 其余全部进「额外补充」（旧课复习/考试材料，不限完成态）
      const doneLec = new Set(done.lectures || [])
      const goalLecs = lectures.filter(
        (l) => (goals.lectures || []).includes(l.number) && !doneLec.has(l.number))
      const extraLecs = lectures.filter((l) => !(goals.lectures || []).includes(l.number))
      const doneFce = new Set(done.fce_parts || [])
      const goalFce = (goals.fce_parts || []).filter((x) => !doneFce.has(x))
      const fceLabels = new Set((ed.fce_parts || []).map((x) => x.label))
      const extraFce = (ed.fce_parts || []).filter(
        (x) => !(goals.fce_parts || []).includes(x.label))
      const doneArt = new Set(done.articles || [])
      // 精读目标池=全部精读文章（主区直选；总目标已选的仅作视觉参考）
      const goalArts = (ed.articles || []).filter(
        (a) => (goals.articles || []).includes(a.id) && !doneArt.has(a.id))
      const doneBk = new Set(done.books || [])
      const goalBooks = books.filter(
        (b) => (goals.reading || []).includes(b.id) && !doneBk.has(b.id))
      const extraBooks = books.filter(
        (b) => !(goals.reading || []).includes(b.id))
      const goalPapers = (ed.goal_assets?.vocab_papers || []).filter(
        (x) => (goals.vocab_papers || []).includes(x.paper_id))
      const extraPapers = (ed.goal_assets?.vocab_papers || []).filter(
        (x) => !(goals.vocab_papers || []).includes(x.paper_id))
      const paperSel = new Set(t.vocab_papers || [])

      const lecSel = new Set(t.lectures || [])
      const fceSel = new Set(t.fce || [])
      // reading: {bookId: [章idx...]（新章选格式） | pct 数字（旧格式）}
      const readSel = new Map()
      for (const [k, v] of Object.entries(t.reading || {})) {
        readSel.set(normalizeReadingKey(k, books), v)
      }
      let speakOn = Boolean(t.speak), speakN = t.speak || 2
      // 词汇目标（级别百分比口径）：累计词数表来自 goal_assets.vocab_levels
      // （cum_words 由后端算好）；旧数据 vocab_goal=绝对词数 → 反推最近的
      // (级别, 百分比) 组合作为初值。
      const vLevels = ed.goal_assets?.vocab_levels || []
      const mastered = ed.vocab_mastered || 0
      let vocabLv = vLevels.length ? vLevels[vLevels.length - 1].level : 5
      let vocabPct = 100
      {
        const goal = t.vocab_goal
        if (goal != null && vLevels.length) {
          let best = null
          for (const v of vLevels) {
            for (const pct of [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]) {
              const n = Math.round((v.cum_words || 0) * pct / 100)
              if (n <= goal && (!best || n > best.n)) best = { lv: v.level, pct, n }
            }
          }
          if (best) { vocabLv = best.lv; vocabPct = best.pct }
        }
      }
      const artSel = new Set(t.articles || [])
      const artChips = () => (ed.articles || []).map((a) => `
        <button type="button" class="pe-part ${artSel.has(a.id) ? 'on' : ''}" data-art="${a.id}" title="${escapeHtml(a.title)} ${a.words} 词">
          ${a.kind === 'base' ? '📄' : '✍️'} ${escapeHtml(a.title.slice(0, 18))}<i>${a.words}</i>
        </button>`).join('')

      // 池化讲次 chips（done=✓ 标记，仍可选用于复习）
      const lecChipsPool = (pool, selSet, attr) => pool.map((l) => `
        <button type="button" class="pe-lec ${selSet.has(l.number) ? 'on' : ''}" data-${attr}="${l.number}" title="${escapeHtml(l.category || '')}">
          <b>${l.number}</b>${escapeHtml(l.title)}${doneLec.has(l.number) ? '<i>✓</i>' : ''}
        </button>`).join('')
      const fcePartsPool = (pool) => pool.map((x) => `
        <button type="button" class="pe-part ${fceSel.has(x.label) ? 'on' : ''}" data-fce="${escapeHtml(x.label)}" title="${escapeHtml(x.label)}">${escapeHtml(x.label.split(' · ').slice(1).join('·'))}<i>${x.questions}</i></button>`).join('')
      const artChipsPool = (pool) => pool.map((a) => `
        <button type="button" class="pe-part ${artSel.has(a.id) ? 'on' : ''}" data-art="${a.id}" title="${escapeHtml(a.title)} ${a.words} 词">
          ${a.kind === 'base' ? '📄' : '✍️'} ${escapeHtml(a.title.slice(0, 18))}<i>${a.words}</i>
        </button>`).join('')
      const paperChipsPool = (pool) => pool.map((x) => `
        <button type="button" class="pe-part ${paperSel.has(x.paper_id) ? 'on' : ''}" data-paper="${escapeHtml(x.paper_id)}">L${x.level} · ${x.paper_id.slice(-8)}<i>${(x.created_at || '').slice(5, 10)}</i></button>`).join('')

      const fceGroups = [1, 2, 3, 4].map((tid) => {
        const parts = (ed.fce_parts || []).filter((x) => x.test_id === tid)
        if (!parts.length) return ''
        return `<div class="pe-fce-group">
          <b>${parts[0].test_title}</b>
          <div class="pe-fce-parts">
            ${parts.map((x) => `<button type="button" class="pe-part ${fceSel.has(x.label) ? 'on' : ''}" data-fce="${escapeHtml(x.label)}">${escapeHtml(x.label.replace(parts[0].test_title + ' · ', ''))}<i>${x.questions}</i></button>`).join('')}
          </div>
        </div>`
      }).join('')

      const bookRows = () => [...readSel.entries()].map(([id, v]) => {
        const b = books.find((x) => String(x.id) === String(id))
        const opts = b?.chapter_opts || []
        const optMap = new Map(opts.map((c) => [String(c.idx), c]))
        // 旧 pct 格式也渲染章 chips（点任意章即升级为章选，旧%丢弃）
        const selArr = Array.isArray(v) ? v : []
        const legacy = Array.isArray(v) ? '' : `<i>旧格式 · 目标${Number(v) || 100}%（点章转为按章选择）</i>`
        const selW = selArr.reduce((a, i) => a + (optMap.get(String(i))?.words || 0), 0)
        const nos = selArr.map((i) => optMap.get(String(i))?.no).filter(Boolean)
        const rng = nos.length > 2 ? `第${Math.min(...nos)}–${Math.max(...nos)}章`
          : nos.map((n) => `第${n}章`).join('、')
        return `<div class="pe-book-row pe-book-chrow" data-bk="${id}">
          <div class="pe-book-chhead">
            <span class="pe-book-title">${escapeHtml(b ? b.title.slice(0, 30) : id)}${legacy}</span>
            ${selArr.length ? `<span class="pe-book-sum">${rng} · ${selW} 词</span>` : ''}
            <button type="button" class="chip" data-bk-del="${id}">✕</button>
          </div>
          <div class="pe-chchips">
            ${opts.map((c) => `<button type="button" class="chip pe-chchip ${selArr.includes(c.idx) ? 'primary' : ''}" data-bk="${id}" data-ch="${c.idx}" title="${escapeHtml(c.title)}">第${c.no}章 ${fmtK(c.words)}词</button>`).join('')}
          </div>
        </div>`
      }).join('')

      const dlg = document.createElement('div')
      dlg.className = 'plan-editor'
      dlg.innerHTML = `
        <div class="plan-editor-mask"></div>
        <div class="plan-editor-body card pe-body">
          <h3>编辑 ${weekLabel(ws)} 周计划 <i>· ${state.student}</i></h3>
          <div class="pe-zone pe-zone-goal">
            <h3 class="pe-zone-title">🎯 从总目标选 <i>总目标的子集；已完成项自动排除</i></h3>
            <div class="pe-sec">
              <h4>📚 语法课程推进</h4>
              <div class="pe-lec-grid" id="pe-lec-grid">${lecChipsPool(goalLecs, lecSel, 'lc') || '<p class="muted">总目标未选讲次，去学生管理配置</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>🔤 词汇目标 <i>学到 L几 的百分比；已掌握 ${ed.vocab_mastered || 0} 词</i></h4>
              <div class="pe-vocab-row">
                <select id="pe-vocab-lv">
                  ${(ed.goal_assets?.vocab_levels || []).map((v) =>
                    `<option value="${v.level}" ${vocabLv === v.level ? 'selected' : ''}>学到 L${v.level}</option>`).join('')}
                </select>
                <label>完成 <input id="pe-vocab-pct" type="number" min="1" max="100" value="${vocabPct}" style="width:56px"/>%</label>
                <span id="pe-vocab-diff" class="pe-vocab-diff"></span>
              </div>
            </div>
            <div class="pe-sec">
              <h4>🎧 FCE 做题</h4>
              <div class="pe-fce-parts">${fcePartsPool((ed.fce_parts || []).filter((x) => goalFce.includes(x.label))) || '<p class="muted">总目标未选 FCE Part</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>📄 精读课文 <i>全部精读文章可选；做完录音自动勾</i></h4>
              <div class="pe-fce-parts" id="pe-articles">${artChipsPool((ed.articles || [])) || '<p class="muted">暂无精读文章</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>📖 泛读 <i>按章选择；chips 显示每章词数，点选/再点取消</i></h4>
              <div id="pe-books">${bookRows()}</div>
              <div class="pe-add-book">
                <select id="pe-book-sel">
                  <option value="">＋ 添加书目…</option>
                  ${goalBooks.concat(extraBooks).filter((b) => !readSel.has(String(b.id))).map((b) =>
                    `<option value="${b.id}">${escapeHtml(b.title.slice(0, 34))}（${b.valid_chapters || b.chapters} 章 / ${fmtK(b.goal_words)} 词）</option>`).join('')}
                </select>
              </div>
            </div>
            <div class="pe-sec">
              <h4>📝 单词试卷</h4>
              <div class="pe-fce-parts">${paperChipsPool(goalPapers) || '<p class="muted">总目标未关联试卷</p>'}</div>
            </div>
          </div>
          <div class="pe-zone pe-zone-extra">
            <h3 class="pe-zone-title">➕ 额外补充 <i>复习旧课 / 其他考试材料（不限完成态）</i></h3>
            <div class="pe-sec">
              <h4>📚 旧课复习 <i>✓ = 已完成，可复习巩固</i></h4>
              <div class="pe-lec-grid" id="pe-extra-lec">${lecChipsPool(extraLecs, lecSel, 'lx') || '<p class="muted">无</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>🎧 FCE 补充练习</h4>
              <div class="pe-fce-parts">${fcePartsPool(extraFce) || '<p class="muted">无</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>📖 泛读补充</h4>
              <div class="pe-add-book">
                <select id="pe-extra-book-sel">
                  <option value="">＋ 补充书目…</option>
                  ${extraBooks.filter((b) => !readSel.has(String(b.id))).map((b) =>
                    `<option value="${b.id}">${escapeHtml(b.title.slice(0, 34))}（${fmtK(b.total_words)} 词）</option>`).join('')}
                </select>
              </div>
            </div>
            <div class="pe-sec">
              <h4>📝 试卷补充</h4>
              <div class="pe-fce-parts">${paperChipsPool(extraPapers) || '<p class="muted">无</p>'}</div>
            </div>
          </div>
          <div class="pe-sec">
            <h4>🎤 朗读（阅读训练）</h4>
            <label class="pe-speak"><input type="checkbox" id="pe-speak-on" ${speakOn ? 'checked' : ''}/> 本周安排朗读，每周
              <input id="pe-speak-n" type="number" min="1" max="7" value="${speakN}" style="width:48px"/> 篇（录音自动进批改）
            </label>
          </div>
          <div class="chip-row">
            <button class="btn-primary" id="pe-save">保存</button>
            <button class="chip" id="pe-cancel">取消</button>
          </div>
        </div>`
      document.body.appendChild(dlg)
      const close = () => dlg.remove()

      // 交互绑定
      const bindToggle = (root, attr, set) => {
        root.querySelectorAll(`[data-${attr}]`).forEach((b) =>
          b.addEventListener('click', () => {
            const n = Number(b.dataset[attr])
            if (set.has(n)) { set.delete(n); b.classList.remove('on') }
            else { set.add(n); b.classList.add('on') }
          }))
      }
      bindToggle(dlg.querySelector('#pe-lec-grid'), 'lc', lecSel)
      const extraGrid = dlg.querySelector('#pe-extra-lec')
      if (extraGrid) bindToggle(extraGrid, 'lx', lecSel)
      // 试卷勾选（两区共用）
      dlg.querySelectorAll('[data-paper]').forEach((b) =>
        b.addEventListener('click', () => {
          const k = b.dataset.paper
          if (paperSel.has(k)) { paperSel.delete(k); b.classList.remove('on') }
          else { paperSel.add(k); b.classList.add('on') }
        }))
      dlg.querySelectorAll('[data-art]').forEach((b) =>
        b.addEventListener('click', () => {
          const id = Number(b.dataset.art)
          if (artSel.has(id)) { artSel.delete(id); b.classList.remove('on') }
          else { artSel.add(id); b.classList.add('on') }
        }))
      dlg.querySelectorAll('[data-fce]').forEach((b) =>
        b.addEventListener('click', () => {
          const k = b.dataset.fce
          if (fceSel.has(k)) { fceSel.delete(k); b.classList.remove('on') }
          else { fceSel.add(k); b.classList.add('on') }
        }))
      // 词汇目标（级别百分比口径）：选择即显示目标词数与当前差距
      const vocabDiff = () => {
        const lvSel = dlg.querySelector('#pe-vocab-lv')
        const pctInp = dlg.querySelector('#pe-vocab-pct')
        const hint = dlg.querySelector('#pe-vocab-diff')
        if (!lvSel || !pctInp || !hint) return
        const lv = Number(lvSel.value)
        const pct = Math.min(100, Math.max(1, Number(pctInp.value) || 0))
        const v = vLevels.find((x) => x.level === lv)
        const cum = v?.cum_words || v?.words || 0
        const target = Math.round(cum * pct / 100)
        const delta = target - mastered
        hint.innerHTML = delta > 0
          ? `目标 <b>${target}</b> 词 · 还需掌握 <b>+${delta}</b> 词`
          : `目标 <b>${target}</b> 词 · 已达成（现 ${mastered} 词）`
        hint.className = `pe-vocab-diff ${delta > 0 ? '' : 'ok'}`
      }
      dlg.querySelector('#pe-vocab-lv').addEventListener('change', vocabDiff)
      dlg.querySelector('#pe-vocab-pct').addEventListener('input', vocabDiff)
      vocabDiff()
      const rerenderBooks = () => {
        dlg.querySelector('#pe-books').innerHTML = bookRows()
        dlg.querySelectorAll('[data-bk-del]').forEach((b) =>
          b.addEventListener('click', () => { readSel.delete(b.dataset.bkDel); rerenderBooks() }))
        // 章选格式：chip 点选切换（数组维护）；旧数字格式行不变（改选章即升级）
        dlg.querySelectorAll('.pe-chchip').forEach((chip) =>
          chip.addEventListener('click', () => {
            const { bk, ch } = chip.dataset
            const b = books.find((x) => String(x.id) === String(bk))
            const cur = readSel.get(bk)
            let arr = Array.isArray(cur) ? [...cur]
              : (b?.chapter_opts || []).map((c) => c.idx) // 旧 pct → 默认全选有效章
            const i = arr.indexOf(Number(ch))
            if (i >= 0) arr.splice(i, 1)
            else arr.push(Number(ch))
            arr.sort((x, y) => x - y)
            readSel.set(bk, arr)
            rerenderBooks()
          }))
        // 下拉里去掉已选书
        const sel = dlg.querySelector('#pe-book-sel')
        sel.value = ''
        ;[...sel.options].forEach((o) => { if (o.value) o.disabled = readSel.has(o.value) })
      }
      rerenderBooks()
      dlg.querySelector('#pe-book-sel').addEventListener('change', (e) => {
        if (!e.target.value) return
        const b = books.find((x) => String(x.id) === e.target.value)
        // 新增书默认全选有效章
        readSel.set(e.target.value, (b?.chapter_opts || []).map((c) => c.idx))
        rerenderBooks()
      })
      const extraSel = dlg.querySelector('#pe-extra-book-sel')
      if (extraSel) extraSel.addEventListener('change', (e) => {
        if (!e.target.value) return
        const b = books.find((x) => String(x.id) === e.target.value)
        readSel.set(e.target.value, (b?.chapter_opts || []).map((c) => c.idx))
        rerenderBooks()
        extraSel.value = ''
      })

      dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
      dlg.querySelector('#pe-cancel').addEventListener('click', close)
      dlg.querySelector('#pe-save').addEventListener('click', async () => {
        const tasks = {}
        const lec = [...lecSel]
        if (lec.length) tasks.lectures = lec
        // 词汇目标：级别百分比口径（vocab_lv/vocab_pct），兼容字段 vocab_goal
        // 由后端按 cum_words 折算；两者都存便于旧端点/周目标区显示
        const lv = Number(dlg.querySelector('#pe-vocab-lv').value)
        const pct = Math.min(100, Math.max(1, Number(dlg.querySelector('#pe-vocab-pct').value) || 0))
        const vInfo = vLevels.find((x) => x.level === lv)
        const vTarget = Math.round((vInfo?.cum_words || 0) * pct / 100)
        if (vTarget > 0) {
          tasks.vocab_lv = lv
          tasks.vocab_pct = pct
          tasks.vocab_goal = vTarget
        }
        if (fceSel.size) tasks.fce = [...fceSel]
        if (readSel.size) tasks.reading = Object.fromEntries(readSel)
        if (artSel.size) tasks.articles = [...artSel]
        if (paperSel.size) tasks.vocab_papers = [...paperSel]
        if (dlg.querySelector('#pe-speak-on').checked) tasks.speak = Number(dlg.querySelector('#pe-speak-n').value) || 2
        const notes = w.notes || ''  // 重点区已移除，保留存量
        try {
          await api.planWeekPut(ws, { tasks, notes, user: state.student })
          close()
          const fresh = await api.planWeeks({ user: state.student })
          byStart.clear()
          fresh.weeks.forEach((x) => byStart.set(x.week_start, x))
          editorDataCache = null // 词汇现况等刷新
          renderView()
        } catch (e) { alert('保存失败：' + e.message) }
      })
    }

    function fmtK(n) {
      const v = Number(n) || 0
      return v >= 1000 ? (v / 1000).toFixed(1) + 'k' : String(v)
    }
  }
}

function iso(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function addDays(isoStr, n) {
  const d = new Date(isoStr + 'T00:00:00')
  d.setDate(d.getDate() + n)
  return iso(d)
}

function weekLabel(ws) {
  const [y, m, d] = ws.split('-').map(Number)
  const end = new Date(y, m - 1, d + 6)
  return `${m}.${d}–${end.getMonth() + 1}.${end.getDate()}`
}

function toast(msg) {
  const t = document.createElement('div')
  t.className = 'plan-toast'
  t.textContent = msg
  document.body.appendChild(t)
  setTimeout(() => t.remove(), 3200)
}

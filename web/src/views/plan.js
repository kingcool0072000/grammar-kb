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
    const { weeks = [], current_week: curWeek, last_week_review: lastReview } = data
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
      ${progressCardHtml(goalProg)}
      <div id="plan-review">${reviewHtml(lastReview)}</div>
      <div id="plan-view"></div>
    `
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




    // 总目标进度卡：总% + 六维度进度条
    function progressCardHtml(gp) {
      if (!gp || gp.percent == null) {
        return `<div class="gp-card gp-empty">
          <span>🎯 ${escapeHtml(state.student)} 的总目标未配置</span>
          <button class="chip" id="gp-go-cfg">去配置</button>
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
            <b>${gp.percent}%</b><span>总进度</span>
          </div>
          <div class="gp-meta">
            <b>🎯 ${escapeHtml(state.student)} 的总目标</b>
            <span class="muted">${gp.done_items}/${gp.total_items} 项完成${gp.deadline ? ` · 截止 ${gp.deadline}` : ''}</span>
          </div>
        </div>
        <div class="gp-dims">${bars}</div>
      </div>`
    }

    function reviewHtml(rv) {
      if (!rv) return ''
      const s = rv.summary || {}
      // 改善项不在此重复展示——历史周的 notes 行内可见（避免同屏两份相同文字）
      const rows = [
        ['📚 课程', s.lecture_count ? `${s.lecture_count} 讲${s.avg_score != null ? ` · 均分 ${s.avg_score}` : ''}` + (s.low_scores?.length ? ` · 低分：${s.low_scores.map((x) => `第${x.lecture}讲${x.score}分`).join('、')}` : '') : '无作业记录', Boolean(s.lecture_count)],
        ['🔤 词汇', s.vocab_mastered ? `新掌握 ${s.vocab_mastered} 词` : '无新掌握词', Boolean(s.vocab_mastered)],
        ['🎧 FCE', s.fce_parts?.length ? s.fce_parts.join('、') : '无练习', Boolean(s.fce_parts?.length)],
      ]
      return `<section class="fce-group plan-review">
        <div class="fce-group-title">👀 上周回顾（${weekLabel(rv.week_start)}）</div>
        ${rows.map(([k, v, has]) => `<div class="plan-review-row ${has ? '' : 'empty'}"><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join('')}
      </section>`
    }

    // ---- 周历视图：← 本周 → 翻周，7 天格子逐日任务 + 周拆解条 ----
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
      const bd = wv.breakdown || {}
      const isCur = wv.week_start === curWeek
      const seeded = [...byStart.keys()].sort()
      const earliest = seeded.length ? seeded[0] : iso(new Date(new Date(curWeek + 'T00:00:00').getTime() - 14 * 86400000))

      // 周拆解条（自动拆解：阅读篇数/泛读词数/题型类数）
      const drillTxt = bd.drill_types?.length
        ? `${bd.drill_types.join(' + ')}（约 ${bd.drill_count} 题）`
        : '无'
      const parts = []
      parts.push(`📖 阅读篇数 <b>${bd.reading_books ?? 0}</b> 本`)
      if (bd.reading_words_total) parts.push(`🔤 泛读词数 <b>${bd.reading_words_total}</b> 词（日均 ${bd.reading_words_per_day}）`)
      if (bd.lecture_count) parts.push(`📚 讲次 <b>${bd.lecture_count}</b> 讲`)
      if (bd.fce_count) parts.push(`🎧 FCE <b>${bd.fce_count}</b> 项`)
      parts.push(`✏️ 题型 <b>${drillTxt}</b>`)

      const dayNames = ['一', '二', '三', '四', '五', '六', '日']
      // 书名解析需要编辑器数据（缓存）；失败不阻塞周历
      const ed = await getEditorData()
      host.innerHTML = `
        <section class="pw-view">
          <header class="pw-nav">
            <button class="chip" data-wk="prev" ${viewWeek <= earliest ? 'disabled' : ''}>← 上一周</button>
            <div class="pw-title">
              <h3>${isCur ? '本周' : '周'} · ${weekLabel(wv.week_start)}${wv.current_week ? '' : '（已结束）'}</h3>
              <p class="muted">${parts.map((x) => `<span>${x}</span>`).join(' · ')}</p>
            </div>
            <button class="chip" data-wk="next" ${wv.week_start >= '2027-01-25' ? 'disabled' : ''}>下一周 →</button>
            <button class="chip" data-edit="${wv.week_start}">✏️ 编辑本周</button>
            ${isCur ? '' : '<button class="chip" data-wk="cur">回到本周</button>'}
          </header>
          ${weekGoalsHtml(t, bd, ed)}
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
      if (acts.reading_min) statRows.push(`📖 泛读 ${acts.reading_min} 分钟${acts.reading_words ? `（${acts.reading_words} 词）` : ''}`)
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

    // 周目标独立区：结构化展示本周排了什么（与编辑器的目标/补充双区对应）
    function weekGoalsHtml(t, bd, ed) {
      const chips = []
      if (t.focus_kps?.length) chips.push(`🎯 攻坚 ${t.focus_kps.map((l) => '第' + l + '讲').join('、')}`)
      if (t.lectures?.length) chips.push(`📚 讲次 ${[...t.lectures].sort((a, b) => a - b).join('/')}讲`)
      if (t.vocab_goal != null) chips.push(`🔤 词汇 累计${t.vocab_goal}词`)
      if (t.fce?.length) chips.push(`🎧 FCE ${t.fce.join('+')}`)
      const books = ed?.books || []
      for (const it of bd?.reading_items || []) {
        const k = String(it.book)
        const b = /^\d+$/.test(k)
          ? books.find((x) => String(x.id) === k)
          : books.find((x) => x.title.toLowerCase().includes(k.toLowerCase()))
        chips.push(`📖 泛读 ${(b ? b.title : k).slice(0, 14)} → ${it.goal_pct}%`)
      }
      if (t.articles?.length) chips.push(`📄 精读 ${t.articles.length} 篇`)
      if (t.vocab_papers?.length) chips.push(`📝 试卷 ${t.vocab_papers.length} 卷`)
      if (t.speak) chips.push(`🎤 朗读 ${t.speak} 篇`)
      return `<section class="pw-goals">
        <div class="pw-goals-title">🎯 本周目标</div>
        <div class="pw-goals-chips">${chips.length
          ? chips.map((c) => `<i>${escapeHtml(c)}</i>`).join('')
          : '<em class="muted">本周未排计划</em>'}</div>
      </section>`
    }

    // 阅读实况文案：全书口径——读到第几章（该章占全书的百分比区间）、
    // 已读/总词数、累计阅读时长。区间和词数由后端按各章 word_count 算出。




    // ---- 月历视图 ----
    let monthData = null
    async function renderMonth() {
      viewHost.innerHTML = '<p class="muted">月历加载中…</p>'
      try {
        monthData = await api.planMonthView({ month: viewMonth, user: state.student })
      } catch (e) {
        viewHost.innerHTML = `<p style="color:#b42318">月历加载失败：${escapeHtml(e.message)}</p>`
        return
      }
      const names = ['一', '二', '三', '四', '五', '六', '日']
      const [y, m] = viewMonth.split('-').map(Number)
      const prevM = m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`
      const nextM = m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`
      viewHost.innerHTML = `
        <section class="pw-view">
          <header class="pw-nav">
            <button class="chip" data-mnav="prev">← 上月</button>
            <div class="pw-title"><h3>${y} 年 ${m} 月</h3>
              <p class="muted">${state.student} 的逐日任务完成情况</p></div>
            <button class="chip" data-mnav="next">下月 →</button>
          </header>
          <div class="pm-grid">
            ${names.map((n) => `<div class="pm-dow">周${n}</div>`).join('')}
            ${(monthData.days || []).map((d) => {
              const dd = Number(d.date.slice(8))
              const isToday = d.date === today
              const full = !d.future && d.total > 0 && d.done >= d.total
              const pct = d.total ? Math.round((d.done / d.total) * 100) : 0
              return `<div class="pm-day ${d.in_month ? '' : 'out'} ${d.future ? 'future' : ''} ${isToday ? 'today' : ''} ${full ? 'fulled' : ''}"
                    title="${d.date} · ${d.total ? `${d.done}/${d.total}` : '无任务'}">
                <b>${dd}</b>
                ${d.future ? '' : d.total ? `<i style="height:${pct}%"></i><em>${d.done}/${d.total}</em>` : '<em>—</em>'}
              </div>`
            }).join('')}
          </div>
        </section>`
      viewHost.querySelectorAll('[data-mnav]').forEach((b) =>
        b.addEventListener('click', () => {
          viewMonth = b.dataset.mnav === 'prev' ? prevM : nextM
          renderMonth()
        }))
    }

    // ---- 全览视图 ----
    async function renderOverview() {
      viewHost.innerHTML = '<p class="muted">全览加载中…</p>'
      let ov
      try {
        ov = await api.planOverview({ user: state.student })
      } catch (e) {
        viewHost.innerHTML = `<p style="color:#b42318">全览加载失败：${escapeHtml(e.message)}</p>`
        return
      }
      viewHost.innerHTML = `
        <section class="pw-view">
          <header class="pw-nav">
            <div class="pw-title"><h3>冲刺全览 · 至 2027-01-31</h3>
              <p class="muted">${state.student} · 每周计划与完成汇总（点周行进周历）</p></div>
          </header>
          <div class="po-list">
            ${(ov.weeks || []).map((w) => {
              const chips = []
              if (w.focus_kps?.length) chips.push(`🎯 ${w.focus_kps.map((l) => l + '讲').join('/')}`)
              if (w.lectures?.length) chips.push(`📚 ${[...w.lectures].sort((a, b) => a - b).join('/')}讲`)
              if (w.vocab_goal != null) chips.push(`🔤 ${w.vocab_goal}词`)
              const rKeys = Object.keys(w.reading || {})
              if (rKeys.length) chips.push(`📖 ${rKeys.map((k) => `${k}${w.reading[k]}%`).join('、')}`)
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
      const goalArts = (ed.articles || []).filter(
        (a) => (goals.articles || []).includes(a.id) && !doneArt.has(a.id))
      const extraArts = (ed.articles || []).filter(
        (a) => !(goals.articles || []).includes(a.id))
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

      const focusSel = new Set(t.focus_kps || [])
      const lecSel = new Set(t.lectures || [])
      const fceSel = new Set(t.fce || [])
      // reading: {bookId: pct}
      const readSel = new Map()
      for (const [k, v] of Object.entries(t.reading || {})) {
        readSel.set(normalizeReadingKey(k, books), Number(v))
      }
      let speakOn = Boolean(t.speak), speakN = t.speak || 2
      const artSel = new Set(t.articles || [])
      const artChips = () => (ed.articles || []).map((a) => `
        <button type="button" class="pe-part ${artSel.has(a.id) ? 'on' : ''}" data-art="${a.id}" title="${escapeHtml(a.title)} ${a.words} 词">
          ${a.kind === 'base' ? '📄' : '✍️'} ${escapeHtml(a.title.slice(0, 18))}<i>${a.words}</i>
        </button>`).join('')

      const lecChips = (selSet, attr) => lectures.map((l) => `
        <button type="button" class="pe-lec ${selSet.has(l.number) ? 'on' : ''}" data-${attr}="${l.number}" title="${escapeHtml(l.category || '')}">
          <b>${l.number}</b>${escapeHtml(l.title)}
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

      const bookRows = () => [...readSel.entries()].map(([id, pct]) => {
        const b = books.find((x) => String(x.id) === String(id))
        const cfgTxt = b?.configured_chapters?.length
          ? `必读 ${b.configured_chapters.length} 章 · ${fmtK(b.goal_words)} 词`
          : (b ? `全书 ${b.chapters} 章 · ${fmtK(b.total_words)} 词` : '')
        return `<div class="pe-book-row" data-bk="${id}">
          <span class="pe-book-title">${escapeHtml(b ? b.title.slice(0, 34) : id)}${cfgTxt ? `<i>${cfgTxt}</i>` : ''}</span>
          <label>目标 <input type="number" min="1" max="100" value="${pct}" data-bk-pct="${id}" style="width:56px"/>%</label>
          <button type="button" class="chip" data-bk-del="${id}">✕</button>
        </div>`
      }).join('')

      const dlg = document.createElement('div')
      dlg.className = 'plan-editor'
      dlg.innerHTML = `
        <div class="plan-editor-mask"></div>
        <div class="plan-editor-body card pe-body">
          <h3>编辑 ${weekLabel(ws)} 周计划 <i>· ${state.student}</i></h3>
          <div class="pe-sec">
            <h4>🎯 攻坚讲次 <i>≤5 个，来自诊断信号优先</i></h4>
            <div class="pe-lec-grid" id="pe-focus-grid">${lecChips(focusSel, 'fl')}</div>
          </div>
          <div class="pe-zone pe-zone-goal">
            <h3 class="pe-zone-title">🎯 从总目标选 <i>总目标的子集；已完成项自动排除</i></h3>
            <div class="pe-sec">
              <h4>📚 讲次推进</h4>
              <div class="pe-lec-grid" id="pe-lec-grid">${lecChipsPool(goalLecs, lecSel, 'lc') || '<p class="muted">总目标未选讲次，去学生管理配置</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>🔤 词汇目标 <i>已掌握 ${ed.vocab_mastered || 0} 词${(goals.vocab_levels || []).length ? ` · 目标级别 L${(goals.vocab_levels || []).join('/L')}` : ''}</i></h4>
              <div class="pe-vocab-row">
                <input id="pe-vocab" type="number" min="0" value="${t.vocab_goal ?? ''}" placeholder="累计目标词数"/>
                <button type="button" class="chip" data-vadd="20">+20</button>
                <button type="button" class="chip" data-vadd="50">+50</button>
                <button type="button" class="chip" data-vadd="100">+100</button>
              </div>
            </div>
            <div class="pe-sec">
              <h4>🎧 FCE 做题</h4>
              <div class="pe-fce-parts">${fcePartsPool((ed.fce_parts || []).filter((x) => goalFce.includes(x.label))) || '<p class="muted">总目标未选 FCE Part</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>📄 精读课文 <i>做完录音自动勾</i></h4>
              <div class="pe-fce-parts" id="pe-articles">${artChipsPool(goalArts) || '<p class="muted">总目标未选精读文章</p>'}</div>
            </div>
            <div class="pe-sec">
              <h4>📖 泛读 <i>按必读章配置算词数</i></h4>
              <div id="pe-books">${bookRows()}</div>
              <div class="pe-add-book">
                <select id="pe-book-sel">
                  <option value="">＋ 添加书目…</option>
                  ${goalBooks.filter((b) => !readSel.has(String(b.id))).map((b) =>
                    `<option value="${b.id}">🎯 ${escapeHtml(b.title.slice(0, 34))}${b.configured_chapters?.length ? `（必读${b.configured_chapters.length}章/${fmtK(b.goal_words)}词）` : ''}</option>`).join('')}
                </select>
                <label>目标 <input id="pe-book-pct" type="number" min="1" max="100" value="100" style="width:56px"/>%</label>
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
              <h4>📄 精读补充</h4>
              <div class="pe-fce-parts">${artChipsPool(extraArts) || '<p class="muted">无</p>'}</div>
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
      bindToggle(dlg.querySelector('#pe-focus-grid'), 'fl', focusSel)
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
      dlg.querySelectorAll('[data-vadd]').forEach((b) =>
        b.addEventListener('click', () => {
          const inp = dlg.querySelector('#pe-vocab')
          inp.value = (Number(inp.value) || ed.vocab_mastered || 0) + Number(b.dataset.vadd)
        }))
      const rerenderBooks = () => {
        dlg.querySelector('#pe-books').innerHTML = bookRows()
        dlg.querySelectorAll('[data-bk-del]').forEach((b) =>
          b.addEventListener('click', () => { readSel.delete(b.dataset.bkDel); rerenderBooks() }))
        dlg.querySelectorAll('[data-bk-pct]').forEach((i) =>
          i.addEventListener('change', () => readSel.set(i.dataset.bkPct, Number(i.value) || 100)))
        // 下拉里去掉已选书
        const sel = dlg.querySelector('#pe-book-sel')
        sel.value = ''
        ;[...sel.options].forEach((o) => { if (o.value) o.disabled = readSel.has(o.value) })
      }
      rerenderBooks()
      dlg.querySelector('#pe-book-sel').addEventListener('change', (e) => {
        if (!e.target.value) return
        readSel.set(e.target.value, Number(dlg.querySelector('#pe-book-pct').value) || 100)
        rerenderBooks()
      })
      const extraSel = dlg.querySelector('#pe-extra-book-sel')
      if (extraSel) extraSel.addEventListener('change', (e) => {
        if (!e.target.value) return
        readSel.set(e.target.value, 100)
        rerenderBooks()
        extraSel.value = ''
      })

      dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
      dlg.querySelector('#pe-cancel').addEventListener('click', close)
      dlg.querySelector('#pe-save').addEventListener('click', async () => {
        const tasks = {}
        const focus = [...focusSel].slice(0, 5)
        if (focus.length) tasks.focus_kps = focus
        const lec = [...lecSel]
        if (lec.length) tasks.lectures = lec
        const vocab = Number(dlg.querySelector('#pe-vocab').value)
        if (vocab > 0) tasks.vocab_goal = vocab
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

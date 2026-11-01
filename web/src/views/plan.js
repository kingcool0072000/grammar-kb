import { api } from '../api.js'
import { escapeHtml, escAttr, todayIso, studyGoalMin, taskWhy, taskWhyShort } from '../render.js'
import { subTabBar, bindSubTabs } from '../tabs.js'
import { openWeekEditor } from './planEditor.js'

function vocabPaperHead(level) {
  return Number(level) < 0 ? '已学混合卷' : `L${level}`
}

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
      ${subTabBar('ptab', [['board', '📋 计划看板'], ['students', '🎒 学生管理']], state.tab)}
      <div id="plan-body"></div>
    `
    bindSubTabs(el, 'ptab', (t) => { state.tab = t; render() })
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
    const today = todayIso()
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
      <div id="review-alert"></div>
      <div id="goal-card"></div>
      <div id="rules-card"></div>
      <div id="plan-view"></div>
    `
    renderGoalCard()
    renderReviewAlert()
    renderRulesCard()
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
        const raw = d.total ? Math.round(d.done / d.total * 100) : null
        const width = raw == null ? 0 : Math.max(0, Math.min(100, raw))
        const pctLabel = raw == null ? '—' : `${raw}%`
        return `<div class="gp-dim">
          <span class="gp-dim-name">${d.icon} ${escapeHtml(d.name)}${d.extra ? `<i>${escapeHtml(d.extra)}</i>` : ''}</span>
          <div class="gp-dim-bar" title="${pctLabel}"><i style="width:${width}%"></i></div>
          <b class="gp-dim-pct">${pctLabel}</b>
          <b class="gp-dim-n">${d.done}/${d.total}</b>
        </div>`
      }).join('')
      const sum = gp.percent == null ? '' : `<span class="gp-total">综合 ${gp.percent}%（${gp.done_items}/${gp.total_items}）· 看各模块</span>`
      return `<div class="gp-card">
        <div class="gp-head">
          <div class="gp-meta">
            <b>${title}</b>
            ${sum}
            ${!isWeek && gp.deadline ? `<span class="muted">截止 ${escapeHtml(String(gp.deadline))}</span>` : ''}
          </div>
          <div class="gp-mode-tabs">
            <button class="chip ${isWeek ? 'primary' : ''}" data-gmode="week">周目标</button>
            <button class="chip ${!isWeek ? 'primary' : ''}" data-gmode="total">总目标</button>
          </div>
        </div>
        <div class="gp-dims">${bars}</div>
      </div>`
    }

    async function renderReviewAlert() {
      const host = body.querySelector('#review-alert')
      if (!host) return
      const who = state.student
      let s = null
      try { s = await api.reviewSummary({ user: who }) } catch { s = null }
      if (state.student !== who || !body.querySelector('#review-alert')) return
      if (!s || !s.alert) { host.innerHTML = ''; return }
      host.innerHTML = `
        <div class="pw-alert">
          错词本卡住：<b>${escapeHtml(who)}</b> 在周期 ${s.in_cycle} 词，近 14 日没有从错词周期攻克，背词还在继续。先安排攻克复习。
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

    // ---- 完成判定规则卡：规则白纸黑字展示 + 开关即改即存 ----
    // 规则本体（人话描述与默认值）在后端 RULES 注册表，这里只渲染与回写。
    // 曾因「第N讲完成了吗」多处各写一套口径而反复出错——现在判定统一
    // 走后端内核，此卡是教师看得见、可调整的唯一入口。
    async function renderRulesCard() {
      const host = body.querySelector('#rules-card')
      if (!host) return
      let rules = null
      try {
        rules = (await api.planRules({ user: state.student })).rules
      } catch { rules = null }
      if (!rules || !rules.length) { host.innerHTML = ''; return }
      const open = sessionStorage.getItem('gkb-rules-open') === '1'
      host.innerHTML = `
        <div class="gp-card pw-rules">
          <div class="pw-rules-head">
            <b>📐 完成判定规则</b>
            <span class="muted">「算不算完成」在此一目了然；改动立即生效</span>
            <span class="pw-flex"></span>
            <button class="chip" id="pw-rules-toggle">${open ? '收起' : '展开'}</button>
          </div>
          ${open ? `<div class="pw-rules-list">
            ${rules.map((r) => `
              <div class="pw-rule">
                <label class="pw-rule-name">
                  <input type="checkbox" data-rule="${escapeHtml(r.id)}" ${r.value ? 'checked' : ''} />
                  ${escapeHtml(r.name)}
                </label>
                <span class="muted">${escapeHtml(r.desc)}</span>
              </div>`).join('')}
          </div>` : ''}
        </div>`
      host.querySelector('#pw-rules-toggle').addEventListener('click', () => {
        sessionStorage.setItem('gkb-rules-open', open ? '0' : '1')
        renderRulesCard()
      })
      host.querySelectorAll('[data-rule]').forEach((chk) =>
        chk.addEventListener('change', async () => {
          const id = chk.dataset.rule
          try {
            await api.planRuleSet(id, { value: chk.checked ? 1 : 0, user: state.student })
            // 规则变化直接影响完成度：刷新目标卡与当前视图
            try {
              const [gp, wv] = await Promise.all([
                api.planGoalProgress({ user: state.student }),
                api.planWeekView({ weekStart: viewWeek, user: state.student }),
              ])
              goalProg = gp
              weekProg = { ...(wv.goal_progress || null), week_label: weekLabel(wv.week_start) }
            } catch { /* 刷新失败保留旧值 */ }
            renderGoalCard()
            renderView()
          } catch (e) {
            alert(`规则保存失败：${e.message}`)
            chk.checked = !chk.checked
          }
        }))
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
              <p class="muted">${weekRateText(wv.completion)}</p>
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
      const icon = { micro_drill: '✏️', wrong_words: '🔤', lecture: '📚', fce: '🎧', reading: '📖', speak: '🎤', article: '📄', paper: '📝', vocab: '🔤', hw_paper: '🗒', video: '🎬' }
      // 已到的日子列出全部计划项，并带上完成原因。完成率来自学习信号，
      // 没有计划项时不显示 0%（避免被理解成「还没打卡」）。
      const taskRow = (x, ok) => {
        const why = taskWhy(x.why)
        const whyShort = d.future ? '' : taskWhyShort(x.why)
        const tip = [x.detail, why].filter(Boolean).join(' · ')
          || x.text || ''
        const inner = `
          <i>${icon[x.type] || '•'}</i>
          <span title="${escapeHtml(x.text)}">${escapeHtml(x.text)}</span>
          ${whyShort ? `<small class="pw-why">${escapeHtml(whyShort)}</small>` : ''}
          ${ok ? '<em>✓</em>' : ''}`
        // 精读篇目可点：直达阅读训练详情（教师备课可顺手看正文；
        // #/reading 学生专属，教师走阅读内容管理页）
        if (x.type === 'article' && x.article_id)
          return `<a class="pw-task link ${ok ? 'done' : `plan ${isToday ? 'today' : ''}`}"
            href="#/readingAdmin"
            title="${escapeHtml(tip || '阅读内容里查看/管理这篇文章')}">${inner}</a>`
        // 视频任务可点：师生都直达视频学习详情（#/videoStudy?vid= 自动展开）
        if (x.type === 'video' && x.video_id)
          return `<a class="pw-task link ${ok ? 'done' : `plan ${isToday ? 'today' : ''}`}"
            href="#/videoStudy?vid=${encodeURIComponent(x.video_id)}"
            title="${escapeHtml(tip || '视频学习里看视频并复述')}">${inner}</a>`
        return `<div class="pw-task ${ok ? 'done' : `plan ${isToday ? 'today' : ''}`}"
          title="${escapeHtml(tip)}">${inner}</div>`
      }
      const rows = (d.tasks || []).filter((x) => x.done).map((x) => taskRow(x, true)).join('')
      const planRows = (d.tasks || []).filter((x) => !x.done).map((x) => taskRow(x, false)).join('')
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
      // 每日有效学习时长 vs 目标（周中45/周末90）：泛读+精读+专题专注秒数
      // 与背词时长合计，达标绿√
      const goalMin = studyGoalMin(d.date)
      const studyMin = acts.study_min || 0
      const minBadge = d.future ? '' :
        `<i class="pw-min ${studyMin >= goalMin ? 'ok' : ''}" title="每日目标 ${goalMin} 分钟（周中45/周末90）">${studyMin ? `${studyMin}′` : '0′'}/${goalMin}′${studyMin >= goalMin ? ' ✓' : ''}</i>`
      const full = !d.future && d.total > 0 && d.done >= d.total
      const rateTitle = d.future
        ? '还没到这一天'
        : (d.total
          ? `${d.done}/${d.total} · 按学习信号计算，不看打卡`
          : '当天没有计划项，不显示 0%')
      const bodyRows = d.future ? planRows : (rows + planRows)
      const hasBody = Boolean(bodyRows || extraRows || statRows.length)
      return `<div class="pw-day ${isToday ? 'today' : ''} ${d.future ? 'future' : ''} ${full ? 'fulled' : ''}">
        <div class="pw-day-head">
          <b>周${dayNames[i]}</b><span>${dd} 日</span>
          ${minBadge}
          <em title="${escapeHtml(rateTitle)}">${dayRateLabel(d)}</em>
        </div>
        <div class="pw-tasks">${hasBody
          ? bodyRows + extraRows + statHtml
          : `<i class="pw-none">${d.future ? '无安排' : '当天没有计划项'}</i>`}</div>
      </div>`
    }

    function dayRateLabel(d) {
      if (d.future) return d.total ? `${d.total} 项` : '待来'
      if (!d.total) return '—'
      const pct = d.percent != null ? d.percent : Math.round(d.done / d.total * 100)
      return `${pct}%`
    }

    function weekRateText(comp) {
      if (!comp || !comp.total) return '已到的日子还没有计划项 · 完成率按学习信号计算，不需要打卡'
      return `完成率 ${comp.percent}%（${comp.done}/${comp.total}）· 按学习信号自动计算，不需要打卡`
    }

    // 周目标 chips（周历「本周目标」区与全览行共用，口径一致）
    function weekGoalChips(t, ed) {
      const chips = []
      if (t.focus_kps?.length) chips.push(`🎯 攻坚 ${t.focus_kps.map((l) => '第' + l + '讲').join('、')}`)
      if (t.lectures?.length) chips.push(`📚 语法课程 ${[...t.lectures].sort((a, b) => a - b).join('/')}讲`)
      if (t.hw_papers?.length) chips.push(`📝 作业卷 ${[...t.hw_papers].sort((a, b) => a - b).join('/')}卷`)
      if (t.vocab_new != null && t.vocab_lv != null) {
        chips.push(`🔤 本周新掌握 L${t.vocab_lv} 词 ${t.vocab_new} 个`)
      } else if (t.vocab_goal != null) {
        chips.push(t.vocab_lv != null
          ? `🔤 词汇 L${t.vocab_lv}（进度按本周新掌握算）`
          : `🔤 词汇 ${t.vocab_goal}词`)
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
      if (t.videos?.length) {
        const pool = ed?.videos || []
        const names = t.videos.map((vid) => {
          const v = pool.find((x) => x.vid === vid)
          return (v?.title || vid).slice(0, 12)
        })
        chips.push(`🎬 视频 ${names.join('、')}`)
      }
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
              const pct = w.percent != null ? w.percent : (w.total ? Math.round((w.done / w.total) * 100) : null)
              return `<div class="po-row ${w.past ? 'past' : ''} ${w.is_current ? 'cur' : ''} ${w.has_plan ? '' : 'noplan'}" data-ws="${w.week_start}">
                <span class="po-date">${weekLabel(w.week_start)}</span>
                <span class="po-chips">${chips.length ? chips.map((c) => `<i>${escapeHtml(c)}</i>`).join('') : '<em class="muted">未排计划</em>'}</span>
                <span class="po-done" title="按学习信号计算，不看打卡">${pct == null ? '' : `<b>${pct}%</b>（${w.done}/${w.total}）`}</span>
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

    let editorDataCache = new Map()
    async function getEditorData(weekStart) {
      const key = `${state.student}|${weekStart || ''}`
      if (!editorDataCache.has(key)) {
        try {
          editorDataCache.set(key, await api.planEditorData({
            user: state.student, weekStart,
          }))
        } catch {
          editorDataCache.set(key, {
            lectures: [], fce_parts: [], books: [], vocab_mastered: 0, videos: [],
          })
        }
      }
      return editorDataCache.get(key)
    }

    async function openEditor(ws) {
      const ed = await getEditorData(ws)
      const saved = byStart.get(ws) || { week_start: ws, tasks: {}, notes: '' }
      openWeekEditor({
        ws,
        student: state.student,
        week: saved,
        ed,
        recentWeeks: [...byStart.values()],
        weekLabel: weekLabel(ws),
        onSaved: async () => {
          const fresh = await api.planWeeks({ user: state.student })
          byStart.clear()
          fresh.weeks.forEach((x) => byStart.set(x.week_start, x))
          editorDataCache.clear()
          renderView()
        },
      })
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

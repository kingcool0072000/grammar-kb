import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 计划表：周历视图。本周大卡（今日任务）+ 月历式周网格往下排，
// 学情分析并入为子 Tab（#/plan?tab=analytics / #/plan 切换）。
// 计划数据由 scripts/seed_plan_weeks.py 自动排程预填，教师可编辑微调。
// 词汇考试：登记成绩（≥80 解锁下一级）+ 打印考卷入口。
export async function mountPlan(el) {
  const subTab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab') || 'plan'
  const state = {
    tab: subTab === 'analytics' ? 'analytics' : 'plan',
    view: 'week',   // week | month | overview
    student: 'malin',
  }

  render()

  function render() {
    el.innerHTML = `
      <div class="view-head">
        <h1>计划表</h1>
        <p>四目标冲刺（2027-01-31）：哈1完课 · FCE 8 Test · L0-L5 词汇 · 泛读 3 本。计划已自动排好，可微调。</p>
      </div>
      <div class="ana-tabs plan-subtabs">
        <button class="reading-btn ${state.tab === 'plan' ? 'primary' : ''}" data-ptab="plan">📅 周历计划</button>
        <button class="reading-btn ${state.tab === 'analytics' ? 'primary' : ''}" data-ptab="analytics">📊 学情分析</button>
      </div>
      <div id="plan-body"></div>
    `
    el.querySelectorAll('[data-ptab]').forEach((b) =>
      b.addEventListener('click', () => {
        state.tab = b.dataset.ptab
        render()
      }),
    )
    const body = el.querySelector('#plan-body')
    if (state.tab === 'analytics') {
      import('./analytics.js').then(({ mountAnalytics }) => {
        const host = document.createElement('div')
        body.replaceChildren(host)
        mountAnalytics(host, { bare: true })
      })
    } else {
      mountCalendar(body)
    }
  }

  async function mountCalendar(body) {
    body.innerHTML = '<p class="muted">加载中…</p>'
    let data, diag, review, daily
    try {
      ;[data, diag, review, daily] = await Promise.all([
        api.planWeeks(), api.planDiagnosis(), api.planReview(),
        api.planDailyPreview().catch(() => null),
      ])
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

    body.innerHTML = `
      ${goalsBarHtml(goals)}
      ${loopDeckHtml()}
      <div class="pw-viewbar">
        <div class="pw-modes">
          <button class="chip ${view.mode === 'week' ? 'primary' : ''}" data-vmode="week">📅 周历</button>
          <button class="chip ${view.mode === 'month' ? 'primary' : ''}" data-vmode="month">🗓 月历</button>
          <button class="chip ${view.mode === 'overview' ? 'primary' : ''}" data-vmode="overview">🗺 全览</button>
        </div>
        <label class="pw-student">学生
          <select id="pw-user"></select>
        </label>
      </div>
      <div id="plan-review">${reviewHtml(lastReview)}</div>
      <div id="plan-view"></div>
    `
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
      renderView()
    })

    function renderView() {
      if (view.mode === 'month') renderMonth()
      else if (view.mode === 'overview') renderOverview()
      else renderWeeks()
    }
    renderView()



    // ---- 总目标（分学生冲刺目标，结构化配置） ----
    function goalsBarHtml(g) {
      const parts = []
      if (g?.deadline) parts.push(`⏰ ${g.deadline}`)
      if (g?.vocab_target) parts.push(`🔤 词汇 ${g.vocab_target} 词`)
      if (g?.lecture_target) parts.push(`📚 讲次到第 ${g.lecture_target} 讲`)
      if (g?.fce_target) parts.push(`🎧 FCE ${g.fce_target} Test`)
      if (g?.reading?.length) parts.push(`📖 ${g.reading.length} 本`)
      const txt = parts.length ? parts.join(' · ') : '未配置总目标（点右侧配置）'
      return `<div class="pg-bar">
        <div class="pg-bar-main">
          <b>🎯 ${state.student} 总目标</b>
          <span>${escapeHtml(txt)}</span>
        </div>
        <button class="chip" id="pg-edit">⚙️ 配置总目标</button>
      </div>`
    }

    function openGoalsDialog() {
      const g = goals || {}
      const dlg = document.createElement('div')
      dlg.className = 'plan-editor'
      dlg.innerHTML = `
        <div class="plan-editor-mask"></div>
        <div class="plan-editor-body card pe-body">
          <h3>配置 ${state.student} 的总目标</h3>
          <label class="plan-field">冲刺截止日期
            <input id="pg-deadline" type="date" value="${g.deadline || '2027-01-31'}"/>
          </label>
          <label class="plan-field">词汇总目标（累计掌握词数）
            <input id="pg-vocab" type="number" min="0" value="${g.vocab_target ?? ''}" placeholder="2000"/>
          </label>
          <label class="plan-field">讲次总目标（哈1 课程推进到）
            <select id="pg-lecture">
              <option value="">— 选择 —</option>
              ${(editorDataCache?.lectures || []).map((l) =>
                `<option value="${l.number}" ${g.lecture_target === l.number ? 'selected' : ''}>第 ${l.number} 讲 · ${escapeHtml(l.title)}</option>`).join('')}
            </select>
          </label>
          <label class="plan-field">FCE 总目标（完成 Test 套数）
            <input id="pg-fce" type="number" min="0" max="8" value="${g.fce_target ?? ''}" placeholder="8"/>
          </label>
          <div class="chip-row">
            <button class="btn-primary" id="pg-save">保存</button>
            <button class="chip" id="pg-cancel">取消</button>
          </div>
        </div>`
      document.body.appendChild(dlg)
      const close = () => dlg.remove()
      dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
      dlg.querySelector('#pg-cancel').addEventListener('click', close)
      dlg.querySelector('#pg-save').addEventListener('click', async () => {
        const ng = {}
        const dl = dlg.querySelector('#pg-deadline').value
        if (dl) ng.deadline = dl
        const vb = Number(dlg.querySelector('#pg-vocab').value)
        if (vb > 0) ng.vocab_target = vb
        const lc = Number(dlg.querySelector('#pg-lecture').value)
        if (lc > 0) ng.lecture_target = lc
        const fc = Number(dlg.querySelector('#pg-fce').value)
        if (fc > 0) ng.fce_target = fc
        if (g?.reading) ng.reading = g.reading // 泛读总目标沿用已存（周计划里逐周配）
        try {
          await api.planGoalsPut(state.student, ng)
          goals = ng
          close()
          const bar = body.querySelector('.pg-bar')
          if (bar) bar.outerHTML = goalsBarHtml(goals)
          bindGoalsBar()
        } catch (e) { alert('保存失败：' + e.message) }
      })
    }

    function bindGoalsBar() {
      const btn = body.querySelector('#pg-edit')
      if (btn) btn.addEventListener('click', async () => {
        await getEditorData() // 讲次下拉数据
        openGoalsDialog()
      })
    }
    bindGoalsBar()

    // ---- 外循环驾驶舱：诊断 → 周目标 → 展开 → 验收 一屏串联 ----
    function currentFocus() {
      const w = byStart.get(curWeek)
      return (w?.tasks?.focus_kps) || []
    }

    function loopDeckHtml() {
      const items = (diag && diag.items) || []
      const focus = currentFocus()
      const rvItems = (review && review.items) || []
      const dailyTasks = (daily && daily.tasks) || []
      const reviewMap = new Map(rvItems.map((i) => [i.lecture, i]))

      const statusMeta = {
        converged: ['✅ 已收敛', 'ok'],
        rolling: ['🔁 滚动', 'roll'],
        escalate: ['🚀 建议转专题', 'esc'],
      }
      const reviewHtmlRows = rvItems.length
        ? rvItems.map((i) => {
            const [label, cls] = statusMeta[i.status] || statusMeta.rolling
            return `<div class="ld-review-item ${cls}">
              <span class="ld-lec">第${i.lecture}讲${i.title ? ' · ' + escapeHtml(i.title.slice(0, 10)) : ''}</span>
              <span class="ld-badge">${label}</span>
              <span class="ld-detail">${i.retested
                ? (i.last_wrong === 0 ? '重测全对' : `重测仍错 ${i.last_wrong} 题`)
                : '本周未重测'}${i.focus_streak > 1 ? ` · 连续 ${i.focus_streak} 周在焦` : ''}</span>
            </div>`
          }).join('')
        : '<p class="muted" style="margin:0">上周未设攻坚讲次，无验收记录。</p>'

      const dailyHtml = dailyTasks.length
        ? dailyTasks.map((t) => `<div class="ld-task"><span class="ld-task-type">${taskIcon(t.type)}</span><b>${escapeHtml(t.text)}</b>${t.detail ? `<i>${escapeHtml(t.detail)}</i>` : ''}</div>`).join('')
        : '<p class="muted" style="margin:0">本周还没排任务，先在下方周历编辑或从诊断区钉入讲次。</p>'

      return `<section class="ld-deck">
        <div class="ld-deck-head">
          <h2>🔁 外循环 · 周驾驶舱</h2>
          <p class="muted">诊断信号 → 钉入本周攻坚 → 系统展开每日任务 → 下周自动验收</p>
        </div>
        <div class="ld-cols">
          <div class="ld-col ld-col-diag">
            <h3>① 诊断感知 <i>近 8 周错题聚类</i></h3>
            ${items.length ? `<div class="ld-diag-list">${items.slice(0, 6).map((x) => {
              const pinned = focus.includes(x.lecture)
              return `<div class="ld-diag-row ${pinned ? 'pinned' : ''}">
                <button class="ld-pin ${pinned ? 'on' : ''}" data-pin="${x.lecture}" title="${pinned ? '移出本周攻坚' : '钉入本周攻坚'}">${pinned ? '📌' : '＋'}</button>
                <span class="ld-lec">第${x.lecture}讲</span>
                <span class="ld-title">${escapeHtml(x.title || '')}</span>
                <span class="ld-wrong">${x.total_wrong} 错</span>
                <span class="ld-meta">${x.attempts} 次 · ${x.last_score} 分 · ${x.last_date.slice(5)}</span>
              </div>`
            }).join('')}</div>` : '<p class="muted" style="margin:0">近 8 周无错题记录。</p>'}
          </div>
          <div class="ld-col ld-col-review">
            <h3>④ 上周验收 <i>${review ? weekLabel(review.week_start) : ''}</i></h3>
            ${reviewHtmlRows}
          </div>
          <div class="ld-col ld-col-daily">
            <h3>③ 每日任务展开 <i>自动派生 · 学生端将逐日执行</i></h3>
            ${dailyHtml}
          </div>
        </div>
      </section>`
    }

    function taskIcon(type) {
      return { micro_drill: '✏️', wrong_words: '🔤', lecture: '📚', fce: '🎧', reading: '📖' }[type] || '•'
    }

    async function togglePin(lecture) {
      const lec = Number(lecture)
      const focus = currentFocus()
      const next = focus.includes(lec)
        ? focus.filter((l) => l !== lec)
        : [...new Set([...focus, lec])].slice(0, 5)
      const w = byStart.get(curWeek) || { tasks: {}, notes: '' }
      const tasks = { ...(w.tasks || {}), focus_kps: next }
      if (!next.length) delete tasks.focus_kps
      try {
        await api.planWeekPut(curWeek, { tasks, notes: w.notes || '' })
        const [freshWeeks, freshDaily] = await Promise.all([
          api.planWeeks(), api.planDailyPreview(),
        ])
        freshWeeks.weeks.forEach((x) => byStart.set(x.week_start, x))
        daily = freshDaily
        renderWeeks()
        const deck = body.querySelector('.ld-deck')
        if (deck) {
          deck.outerHTML = loopDeckHtml()
          bindLoopDeck()
        }
      } catch (e) { alert('保存失败：' + e.message) }
    }

    function bindLoopDeck() {
      body.querySelectorAll('[data-pin]').forEach((b) =>
        b.addEventListener('click', () => togglePin(b.dataset.pin)))
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
        wv = await api.planWeekView({ weekStart: viewWeek })
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
          <div class="pw-grid">
            ${(wv.days || []).map((d, i) => dayCell(d, i, dayNames)).join('')}
          </div>
          ${wv.notes ? `<div class="plan-notes"><span>📝 重点</span><div>${escapeHtml(wv.notes).replace(/\n/g, '<br/>')}</div></div>` : ''}
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
      const icon = { micro_drill: '✏️', wrong_words: '🔤', lecture: '📚', fce: '🎧', reading: '📖' }
      const rows = (d.tasks || []).map((x) => `
        <div class="pw-task ${x.done ? 'done' : ''}">
          <i>${icon[x.type] || '•'}</i>
          <span title="${escapeHtml(x.text)}">${escapeHtml(x.text)}</span>
          <em>${x.done ? '✓' : ''}</em>
        </div>`).join('')
      const full = !d.future && d.total > 0 && d.done >= d.total
      return `<div class="pw-day ${isToday ? 'today' : ''} ${d.future ? 'future' : ''} ${full ? 'fulled' : ''}">
        <div class="pw-day-head">
          <b>周${dayNames[i]}</b><span>${dd} 日</span>
          <em>${d.future ? '待来' : (d.total ? `${d.done}/${d.total}` : '—')}</em>
        </div>
        <div class="pw-tasks">${rows || (d.future ? '' : '<i class="pw-none">无任务</i>')}</div>
      </div>`
    }

    // 阅读实况文案：全书口径——读到第几章（该章占全书的百分比区间）、
    // 已读/总词数、累计阅读时长。区间和词数由后端按各章 word_count 算出。




    // ---- 月历视图 ----
    let monthData = null
    async function renderMonth() {
      viewHost.innerHTML = '<p class="muted">月历加载中…</p>'
      try {
        monthData = await api.planMonthView({ month: viewMonth })
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
        ov = await api.planOverview()
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
      const lecTitle = (n) => {
        const l = lectures.find((x) => x.number === n)
        return l ? l.title : ''
      }

      const focusSel = new Set(t.focus_kps || [])
      const lecSel = new Set(t.lectures || [])
      const fceSel = new Set(t.fce || [])
      // reading: {bookId: pct}
      const readSel = new Map()
      for (const [k, v] of Object.entries(t.reading || {})) {
        readSel.set(normalizeReadingKey(k, books), Number(v))
      }
      let speakOn = Boolean(t.speak), speakN = t.speak || 2

      const lecChips = (selSet, attr) => lectures.map((l) => `
        <button type="button" class="pe-lec ${selSet.has(l.number) ? 'on' : ''}" data-${attr}="${l.number}" title="${escapeHtml(l.category || '')}">
          <b>${l.number}</b>${escapeHtml(l.title)}
        </button>`).join('')

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
          <div class="pe-sec">
            <h4>📚 本周讲次任务</h4>
            <div class="pe-lec-grid" id="pe-lec-grid">${lecChips(lecSel, 'lc')}</div>
          </div>
          <div class="pe-sec">
            <h4>🔤 词汇目标 <i>当前已掌握 ${ed.vocab_mastered || 0} 词</i></h4>
            <div class="pe-vocab-row">
              <input id="pe-vocab" type="number" min="0" value="${t.vocab_goal ?? ''}" placeholder="累计目标词数"/>
              <button type="button" class="chip" data-vadd="20">+20</button>
              <button type="button" class="chip" data-vadd="50">+50</button>
              <button type="button" class="chip" data-vadd="100">+100</button>
            </div>
          </div>
          <div class="pe-sec">
            <h4>🎧 FCE 做题 <i>勾选本周要做的 Part</i></h4>
            ${fceGroups || '<p class="muted">FCE 题库不可用</p>'}
          </div>
          <div class="pe-sec">
            <h4>📖 泛读目标 <i>目标按泛读馆必读章配置算词数</i></h4>
            <div id="pe-books">${bookRows()}</div>
            <div class="pe-add-book">
              <select id="pe-book-sel">
                <option value="">＋ 从书架添加书目…</option>
                ${books.filter((b) => !readSel.has(String(b.id))).map((b) =>
                  `<option value="${b.id}">${escapeHtml(b.title.slice(0, 40))}${b.configured_chapters?.length ? `（必读 ${b.configured_chapters.length} 章/${fmtK(b.goal_words)} 词）` : `（${b.chapters} 章/${fmtK(b.total_words)} 词）`}</option>`).join('')}
              </select>
              <label>目标 <input id="pe-book-pct" type="number" min="1" max="100" value="100" style="width:56px"/>%</label>
            </div>
          </div>
          <div class="pe-sec">
            <h4>🎤 朗读（阅读训练）</h4>
            <label class="pe-speak"><input type="checkbox" id="pe-speak-on" ${speakOn ? 'checked' : ''}/> 本周安排朗读，每周
              <input id="pe-speak-n" type="number" min="1" max="7" value="${speakN}" style="width:48px"/> 篇（录音自动进批改）
            </label>
          </div>
          <label class="plan-field">本周重点 / 改善项
            <textarea id="pe-notes" rows="3">${escapeHtml(w.notes || '')}</textarea>
          </label>
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
        if (dlg.querySelector('#pe-speak-on').checked) tasks.speak = Number(dlg.querySelector('#pe-speak-n').value) || 2
        const notes = dlg.querySelector('#pe-notes').value.trim()
        try {
          await api.planWeekPut(ws, { tasks, notes })
          close()
          const fresh = await api.planWeeks()
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

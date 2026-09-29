import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 计划表：周历视图。本周大卡（今日任务）+ 月历式周网格往下排，
// 学情分析并入为子 Tab（#/plan?tab=analytics / #/plan 切换）。
// 计划数据由 scripts/seed_plan_weeks.py 自动排程预填，教师可编辑微调。
// 词汇考试：登记成绩（≥80 解锁下一级）+ 打印考卷入口。
export async function mountPlan(el) {
  const subTab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab') || 'plan'
  const state = { tab: subTab === 'analytics' ? 'analytics' : 'plan' }

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
    let data, exams, diag, review, daily
    try {
      ;[data, exams, diag, review, daily] = await Promise.all([
        api.planWeeks(), api.vocabExams(), api.planDiagnosis(), api.planReview(),
        api.planDailyPreview().catch(() => null),
      ])
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    const { weeks = [], current_week: curWeek, last_week_review: lastReview } = data
    const byStart = new Map(weeks.map((w) => [w.week_start, w]))
    const today = new Date().toISOString().slice(0, 10)
    // 周历视图状态：默认本周，可前后翻
    let viewWeek = curWeek

    // ---- 词汇考试面板 ----
    const vex = exams || {}
    const passed = vex.passed_levels || []
    const unlocked = vex.unlocked_level ?? 0

    body.innerHTML = `
      ${loopDeckHtml()}
      <section class="fce-group plan-vocab-exam">
        <div class="fce-group-title">🎓 词汇级别考试</div>
        <p class="plan-vex-state">已过：${passed.length ? passed.map((l) => 'L' + l).join('、') : '无'} · 当前解锁到 <b>L${unlocked}</b>（80 分解锁下一级）</p>
        <div class="plan-vex-row">
          <label>级别 <select id="pv-level">${[0, 1, 2, 3, 4].map((l) => `<option value="${l}" ${l === unlocked ? 'selected' : ''}>L${l} → L${l + 1}</option>`).join('')}</select></label>
          <label>学生 <select id="pv-user"><option value="malin">malin</option><option value="mxy">mxy</option></select></label>
          <label>日期 <input id="pv-date" type="date" value="${today}"/></label>
          <label>得分 <input id="pv-score" type="number" min="0" max="100" placeholder="0-100" style="width:90px"/></label>
          <button class="btn-primary" id="pv-record">登记成绩</button>
          <button class="chip" id="pv-paper">🖨 打印考卷</button>
        </div>
        <div id="pv-exam-list">${renderExamList(vex.exams || [])}</div>
      </section>
      <div id="plan-review">${reviewHtml(lastReview)}</div>
      <div id="plan-weeks"></div>
    `

    bindLoopDeck()
    bindVocabExam()
    renderWeeks()

    function renderExamList(items) {
      if (!items.length) return '<p class="muted" style="margin:6px 0 0">还没有考试记录。线下考完在这里登记，≥80 分自动解锁下一级词库。</p>'
      return `<div class="plan-vex-history">${items.slice(0, 8).map((x) => `
        <span class="plan-vex-item ${x.score >= 80 ? 'ok' : 'fail'}">L${x.level} · ${x.score} 分 · ${x.exam_date}<button data-del="${x.id}" title="删除">×</button></span>`).join('')}</div>`
    }

    function bindVocabExam() {
      body.querySelector('#pv-record').addEventListener('click', async () => {
        const user = body.querySelector('#pv-user').value
        const level = Number(body.querySelector('#pv-level').value)
        const score = Number(body.querySelector('#pv-score').value)
        const exam_date = body.querySelector('#pv-date').value
        if (!(score >= 0 && score <= 100) || !exam_date) return alert('得分与日期须填完整')
        try {
          const r = await api.vocabExamAdd({ user, level, score, exam_date })
          const fresh = await api.vocabExams()
          body.querySelector('#pv-exam-list').innerHTML = renderExamList(fresh.exams || [])
          body.querySelector('.plan-vex-state').innerHTML =
            `已过：${(fresh.passed_levels || []).map((l) => 'L' + l).join('、') || '无'} · 当前解锁到 <b>L${fresh.unlocked_level}</b>（80 分解锁下一级）`
          toast(score >= 80 ? `🎉 ${score} 分通过！L${level + 1} 已解锁` : `${score} 分登记成功（差 ${80 - score} 分解锁）`)
        } catch (e) { alert('登记失败：' + e.message) }
      })
      body.querySelector('#pv-paper').addEventListener('click', () => {
        const level = body.querySelector('#pv-level').value
        const user = body.querySelector('#pv-user').value
        window.open(`/api/vocab-exams/paper?level=${level}&student=${encodeURIComponent(user)}`, '_blank')
      })
      body.querySelectorAll('[data-del]').forEach((b) =>
        b.addEventListener('click', async (e) => {
          e.stopPropagation()
          if (!confirm('删除这条考试记录？')) return
          try {
            await api.vocabExamDel(b.dataset.del)
            const fresh = await api.vocabExams()
            body.querySelector('#pv-exam-list').innerHTML = renderExamList(fresh.exams || [])
          } catch (err) { alert(err.message) }
        }),
      )
    }

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
      const host = body.querySelector('#plan-weeks')
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




    function openEditor(ws) {
      const w = byStart.get(ws) || { week_start: ws, tasks: {}, notes: '' }
      const t = w.tasks || {}
      const dlg = document.createElement('div')
      dlg.className = 'plan-editor'
      dlg.innerHTML = `
        <div class="plan-editor-mask"></div>
        <div class="plan-editor-body card">
          <h3>编辑 ${weekLabel(ws)} 周计划</h3>
          <label class="plan-field">攻坚讲次（外循环周目标，逗号分隔，≤5 个）
            <input id="pe-focus" value="${(t.focus_kps || []).join(',')}" placeholder="26, 28"/>
          </label>
          <label class="plan-field">本周计划讲次（逗号分隔，如 29, 30）
            <input id="pe-lectures" value="${(t.lectures || []).join(',')}" placeholder="29, 30"/>
          </label>
          <label class="plan-field">词汇目标（累计掌握词数）
            <input id="pe-vocab" type="number" min="0" value="${t.vocab_goal ?? ''}" placeholder="380"/>
          </label>
          <label class="plan-field">FCE 任务（分号分隔）
            <input id="pe-fce" value="${escapeHtml((t.fce || []).join('；'))}" placeholder="Test1 RUE"/>
          </label>
          <label class="plan-field">阅读目标（书名:百分比，分号分隔）
            <input id="pe-reading" value="${escapeHtml(Object.entries(t.reading || {}).map(([k, v]) => `${k}:${v}`).join('；'))}" placeholder="哈利波特1:60"/>
          </label>
          <label class="plan-field">本周重点 / 改善项
            <textarea id="pe-notes" rows="4">${escapeHtml(w.notes || '')}</textarea>
          </label>
          <div class="chip-row">
            <button class="btn-primary" id="pe-save">保存</button>
            <button class="chip" id="pe-cancel">取消</button>
          </div>
        </div>`
      document.body.appendChild(dlg)
      const close = () => dlg.remove()
      dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
      dlg.querySelector('#pe-cancel').addEventListener('click', close)
      dlg.querySelector('#pe-save').addEventListener('click', async () => {
        const tasks = {}
        const focus = (dlg.querySelector('#pe-focus').value.match(/\d+/g) || []).map(Number)
        if (focus.length) tasks.focus_kps = [...new Set(focus)].slice(0, 5)
        const lec = (dlg.querySelector('#pe-lectures').value.match(/\d+/g) || []).map(Number)
        if (lec.length) tasks.lectures = lec
        const vocab = Number(dlg.querySelector('#pe-vocab').value)
        if (vocab > 0) tasks.vocab_goal = vocab
        const fce = dlg.querySelector('#pe-fce').value.split(/[；;]/).map((s) => s.trim()).filter(Boolean)
        if (fce.length) tasks.fce = fce
        const reading = {}
        for (const pair of dlg.querySelector('#pe-reading').value.split(/[；;]/)) {
          const m = pair.match(/^(.+?)[:：]\s*(\d+)\s*%?$/)
          if (m) reading[m[1].trim()] = Number(m[2])
        }
        if (Object.keys(reading).length) tasks.reading = reading
        const notes = dlg.querySelector('#pe-notes').value.trim()
        try {
          await api.planWeekPut(ws, { tasks, notes })
          close()
          const fresh = await api.planWeeks()
          byStart.clear()
          fresh.weeks.forEach((x) => byStart.set(x.week_start, x))
          renderWeeks()
        } catch (e) { alert('保存失败：' + e.message) }
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

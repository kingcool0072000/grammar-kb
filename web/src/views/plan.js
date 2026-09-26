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
    let data, exams
    try {
      ;[data, exams] = await Promise.all([api.planWeeks(), api.vocabExams()])
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    const { weeks = [], current_week: curWeek, last_week_review: review } = data
    const byStart = new Map(weeks.map((w) => [w.week_start, w]))
    const today = new Date().toISOString().slice(0, 10)

    // ---- 词汇考试面板 ----
    const vex = exams || {}
    const passed = vex.passed_levels || []
    const unlocked = vex.unlocked_level ?? 0

    body.innerHTML = `
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
      <div id="plan-review">${reviewHtml(review)}</div>
      <div id="plan-weeks"></div>
    `

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

    function renderWeeks() {
      const host = body.querySelector('#plan-weeks')
      // 从最早已排计划的周开始显示（历史回填的周也要能看到）；无历史则回看 2 周
      const seeded = [...byStart.keys()].sort()
      const fallback = new Date(curWeek + 'T00:00:00')
      fallback.setDate(fallback.getDate() - 14)
      const earliest = seeded.length ? seeded[0] : iso(fallback)
      const starts = []
      let d = new Date(earliest + 'T00:00:00')
      const END = new Date('2027-01-31T23:59:59') // 冲刺线 2027-01-31：其后的周一不再渲染
      while (d <= END) {
        starts.push(iso(d))
        d = new Date(d.getTime() + 7 * 86400000)
      }
      host.innerHTML = starts.map((ws) => weekCard(byStart.get(ws) || { week_start: ws, tasks: {}, notes: '', actuals: null }, ws === curWeek)).join('')
      host.querySelectorAll('[data-edit]').forEach((b) =>
        b.addEventListener('click', () => openEditor(b.dataset.edit)))
    }

    function weekCard(w, isCurrent) {
      const t = w.tasks || {}
      const a = w.actuals
      const past = w.week_start < curWeek
      const isThisWeek = w.week_start <= today && today <= addDays(w.week_start, 6)
      const hasPlan = Boolean(t.lectures?.length || t.vocab_goal != null || t.fce?.length || (t.reading && Object.keys(t.reading).length))

      // 周历条：本周=高亮大卡；过去/未来=紧凑一行（有计划才展开要点）
      const chips = []
      if (t.lectures?.length) chips.push(`📚 ${[...t.lectures].sort((x, y) => x - y).join('/')}讲`)
      if (t.vocab_goal != null) chips.push(`🔤 累计${t.vocab_goal}词`)
      if (t.fce?.length) chips.push(`🎧 ${t.fce.join('+')}`)
      if (t.reading) for (const [k, v] of Object.entries(t.reading)) chips.push(`📖 ${k} ${v}%`)

      if (!isCurrent) {
        // 历史周：notes（周总结）行内展示；✓ 覆盖有课程/词汇完成的周，
        // 无数据的过去周也给「已结束」基底（plan-wk-row.past 降权）
        return `<section class="plan-wk-row ${past ? 'past' : 'future'} ${w.week_start <= today && today <= addDays(w.week_start, 6) ? 'thisweek' : ''}">
          <span class="plan-wk-date">${weekLabel(w.week_start)}</span>
          <span class="plan-wk-chips">${chips.length ? chips.map((c) => `<i>${escapeHtml(c)}</i>`).join('') : '<em class="muted">—</em>'}</span>
          ${a && past && (a.lectures?.length || a.vocab_mastered) ? `<span class="plan-wk-done">✓ ${a.lectures?.length ? a.lectures.length + '讲' : ''}${a.vocab_mastered ? ' ' + a.vocab_mastered + '词' : ''}</span>` : ''}
          <button class="chip" data-edit="${w.week_start}">编辑</button>
          ${past && w.notes ? `<p class="plan-wk-note">${escapeHtml(w.notes)}</p>` : ''}
        </section>`
      }

      // 本周大卡（chips 行同上文；阅读行书名不截断，目标/实况分别标注）
      const act = a ? [
        ['课程', a.lectures?.length ? a.lectures.map((x) => `第${x.lecture}讲 ${x.score}分${x.score < 70 ? ' ⚠️' : ''}`).join('、') : '未开始', Boolean(a.lectures?.length)],
        ['词汇', t.vocab_goal != null ? `本周新掌握 ${a.vocab_mastered || 0} 词 / 目标 +${Math.max(0, t.vocab_goal - (a.vocab_mastered || 0))}` : `新掌握 ${a.vocab_mastered || 0} 词`, Boolean(a.vocab_mastered)],
        ['FCE', a.fce_parts?.length ? a.fce_parts.join('、') : '未开始', Boolean(a.fce_parts?.length)],
        ['阅读', a.reading?.length ? a.reading.map((r) => {
          // 计划书名与实况书名模糊匹配：key 的英文词段（≥3 字母）须全部
          // 出现在实况标题里；纯中文 key 匹配不上时不标目标（key 建议写
          // 英文关键词，如 "Percy" / "Harry Potter" / "Wonder"）
          const goal = t.reading
            ? Object.entries(t.reading).find(([k]) => {
                if (!r.title) return false
                const segs = k.toLowerCase().match(/[a-z]{3,}/g)
                if (!segs) return false
                const target = r.title.toLowerCase()
                return segs.every((s) => target.includes(s))
              })
            : null
          const goalTxt = goal ? `（目标 ${goal[1]}%）` : ''
          return `${r.title} 实况 ${Math.round(r.percent)}%${goalTxt}`
        }).join('；') : '未开始', Boolean(a.reading?.length)],
      ] : []
      const daysLeft = Math.max(0, Math.round((new Date(addDays(w.week_start, 6) + 'T23:59:59') - new Date()) / 86400000))
      return `<section class="plan-wk-current">
        <div class="plan-wk-head">
          <div>
            <h3>本周 · ${weekLabel(w.week_start)}</h3>
            <p class="muted">${chips.length ? chips.join(' · ') : '本周未排计划'} · 剩余 ${daysLeft} 天</p>
          </div>
          <button class="chip" data-edit="${w.week_start}">编辑本周</button>
        </div>
        ${act.length ? `<div class="plan-wk-act">${act.map(([k, v, done]) => `<div class="plan-act-row ${done ? '' : 'muted'}"><span>${k}</span><b>${escapeHtml(v)}</b></div>`).join('')}</div>` : ''}
        ${w.notes ? `<div class="plan-notes"><span>📝 重点</span><div>${escapeHtml(w.notes).replace(/\n/g, '<br/>')}</div></div>` : ''}
      </section>`
    }

    function openEditor(ws) {
      const w = byStart.get(ws) || { week_start: ws, tasks: {}, notes: '' }
      const t = w.tasks || {}
      const dlg = document.createElement('div')
      dlg.className = 'plan-editor'
      dlg.innerHTML = `
        <div class="plan-editor-mask"></div>
        <div class="plan-editor-body card">
          <h3>编辑 ${weekLabel(ws)} 周计划</h3>
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

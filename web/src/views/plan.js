import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 计划表：周维度学习计划。每周一张卡：
//   任务（计划讲次/词汇目标/FCE 部分/阅读目标）+ 实时完成进度 + 上周回顾。
// 完成度由服务端按周窗口实时聚合（GET /plan/weeks 的 actuals），不存快照。
// 「上周要改善的部分」写在 notes，编辑当前周时顶部自动展示上周回顾摘要。
const WEEK_CN = ['一', '二', '三', '四', '五', '六', '日']

function mondayStr(offsetWeeks = 0) {
  const d = new Date()
  const mon = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7))
  mon.setDate(mon.getDate() + offsetWeeks * 7)
  return `${mon.getFullYear()}-${String(mon.getMonth() + 1).padStart(2, '0')}-${String(mon.getDate()).padStart(2, '0')}`
}

function weekLabel(ws) {
  const [y, m, d] = ws.split('-').map(Number)
  const end = new Date(y, m - 1, d + 6)
  return `${m}.${d} – ${end.getMonth() + 1}.${end.getDate()}`
}

export async function mountPlan(el) {
  el.innerHTML = `
    <div class="view-head">
      <h1>计划表</h1>
      <p>按周排学习计划：本周任务、完成进度与上周要改善的部分。</p>
    </div>
    <div id="plan-review"></div>
    <div id="plan-weeks"><p class="muted">加载中…</p></div>
  `

  let data
  try {
    data = await api.planWeeks()
  } catch (e) {
    el.querySelector('#plan-weeks').innerHTML =
      `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
  const { weeks = [], current_week: curWeek, last_week_review: review } = data
  const byStart = new Map(weeks.map((w) => [w.week_start, w]))

  // 上周回顾（自动摘要 + 上周 notes）
  renderReview(review)

  // 从本周起列到 2027 年 2 月初（冲刺 18 周 + 缓冲）；再往前补最近 3 周
  const starts = []
  const cur = new Date(curWeek + 'T00:00:00')
  for (let i = -3; i <= 18; i++) {
    const d = new Date(cur.getTime() + i * 7 * 86400000)
    if (d > new Date('2027-02-08T00:00:00')) break
    starts.push(
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`,
    )
  }

  renderWeeks()

  function renderReview(rv) {
    const host = el.querySelector('#plan-review')
    if (!rv) {
      host.innerHTML = ''
      return
    }
    const s = rv.summary || {}
    const lectures = s.lecture_count
      ? `${s.lecture_count} 讲${s.avg_score != null ? ` · 均分 ${s.avg_score}` : ''}` +
        (s.low_scores?.length ? ` · 低分讲次：${s.low_scores.map((x) => `第${x.lecture}讲 ${x.score}分`).join('、')}` : '')
      : '无作业记录'
    const vocab = s.vocab_mastered ? `新掌握 ${s.vocab_mastered} 词` : '无新掌握词'
    const fce = s.fce_parts?.length ? s.fce_parts.join('、') : '无 FCE 练习'
    host.innerHTML = `
      <section class="fce-group">
        <div class="fce-group-title">👀 上周回顾（${weekLabel(rv.week_start)} 周）</div>
        <div class="plan-review-row"><span>📚 课程</span><b>${escapeHtml(lectures)}</b></div>
        <div class="plan-review-row"><span>🔤 词汇</span><b>${escapeHtml(vocab)}</b></div>
        <div class="plan-review-row"><span>🎧 FCE</span><b>${escapeHtml(fce)}</b></div>
        <div class="plan-review-row"><span>📝 改善项</span><b class="plan-notes-preview">${rv.notes ? escapeHtml(rv.notes).replace(/\n/g, '<br/>') : '无'}</b></div>
      </section>`
  }

  function renderWeeks() {
    const host = el.querySelector('#plan-weeks')
    host.innerHTML = starts
      .map((ws) => {
        const w = byStart.get(ws) || { week_start: ws, tasks: {}, notes: '', actuals: null }
        return weekCard(w, ws === curWeek)
      })
      .join('')
    host.querySelectorAll('[data-edit]').forEach((b) =>
      b.addEventListener('click', () => openEditor(b.dataset.edit)),
    )
  }

  function weekCard(w, isCurrent) {
    const t = w.tasks || {}
    const a = w.actuals
    const today = new Date().toISOString().slice(0, 10)
    const past = w.week_start < curWeek
    const isThisWeek = w.week_start <= today && today <= addDays(w.week_start, 6)
    const hasPlan = Boolean(
      t.lectures?.length || t.vocab_goal != null || t.fce?.length || (t.reading && Object.keys(t.reading).length),
    )

    // 未排计划的未来周：紧凑单行（避免整页同构卡片墙），有计划/过去/本周才展开
    if (!hasPlan && !past && !isCurrent) {
      return `
      <section class="fce-group plan-week-card plan-collapsed">
        <div class="fce-group-title">
          🗓 ${weekLabel(w.week_start)} 周
          <button class="chip" data-edit="${w.week_start}">编辑</button>
        </div>
      </section>`
    }

    const taskRows = []
    if (t.lectures?.length)
      taskRows.push(`<div class="plan-task-row"><span>📚 课程</span><b>第 ${t.lectures.join('、')} 讲</b></div>`)
    if (t.vocab_goal != null)
      taskRows.push(`<div class="plan-task-row"><span>🔤 词汇</span><b>累计掌握 ${t.vocab_goal} 词</b></div>`)
    if (t.fce?.length)
      taskRows.push(`<div class="plan-task-row"><span>🎧 FCE</span><b>${escapeHtml(t.fce.join('、'))}</b></div>`)
    if (t.reading && Object.keys(t.reading).length)
      taskRows.push(`<div class="plan-task-row"><span>📖 阅读</span><b>${Object.entries(t.reading).map(([k, v]) => `${escapeHtml(k)} → ${v}%`).join('；')}</b></div>`)

    const actualRows = []
    if (a) {
      const lecDone = a.lectures?.length
        ? a.lectures.map((x) => `第${x.lecture}讲 ${x.score}分${x.score < 70 ? ' ⚠️' : ''}`).join('、')
        : null
      actualRows.push(`<div class="plan-act-row ${lecDone ? '' : 'muted'}"><span>课程</span><b>${lecDone || '未开始'}</b></div>`)
      const vocabTxt = t.vocab_goal != null
        ? `${a.vocab_mastered || 0} / 目标差 ${(Math.max(0, t.vocab_goal - (a.vocab_mastered || 0)))} 词（当周新掌握）`
        : `新掌握 ${a.vocab_mastered || 0} 词`
      actualRows.push(`<div class="plan-act-row"><span>词汇</span><b>${vocabTxt}</b></div>`)
      actualRows.push(`<div class="plan-act-row ${a.fce_parts?.length ? '' : 'muted'}"><span>FCE</span><b>${a.fce_parts?.length ? escapeHtml(a.fce_parts.join('、')) : '未开始'}</b></div>`)
      const readTxt = a.reading?.length
        ? a.reading.map((r) => `${escapeHtml((r.title || '').slice(0, 18))} ${Math.round(r.percent)}%`).join('；')
        : null
      actualRows.push(`<div class="plan-act-row ${readTxt ? '' : 'muted'}"><span>阅读</span><b>${readTxt || '未开始'}</b></div>`)
    }

    return `
      <section class="fce-group plan-week-card ${isCurrent ? 'plan-current' : ''} ${past && !taskRows.length ? 'plan-empty' : ''}">
        <div class="fce-group-title">
          🗓 ${weekLabel(w.week_start)} 周${isCurrent ? ' · 本周' : ''}
          <button class="chip" data-edit="${w.week_start}">编辑</button>
        </div>
        ${taskRows.length ? taskRows.join('') : '<p class="muted plan-no-task">未排计划</p>'}
        ${actualRows.length && (isThisWeek || past) ? `<div class="plan-act"><div class="plan-act-title">完成进度</div>${actualRows.join('')}</div>` : ''}
        ${w.notes ? `<div class="plan-notes"><span>📝 改善项</span><div>${escapeHtml(w.notes).replace(/\n/g, '<br/>')}</div></div>` : ''}
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
        <label class="plan-field">词汇目标（累计掌握词数，如 380）
          <input id="pe-vocab" type="number" min="0" value="${t.vocab_goal ?? ''}" placeholder="380"/>
        </label>
        <label class="plan-field">FCE 任务（如 Test1 RUE，分号分隔）
          <input id="pe-fce" value="${escapeHtml((t.fce || []).join('；'))}" placeholder="Test1 RUE 全部"/>
        </label>
        <label class="plan-field">阅读目标（书名:百分比，分号分隔，如 波西杰克逊1:100）
          <input id="pe-reading" value="${escapeHtml(Object.entries(t.reading || {}).map(([k, v]) => `${k}:${v}`).join('；'))}" placeholder="波西杰克逊1:100"/>
        </label>
        <label class="plan-field">要改善的部分 / 备注
          <textarea id="pe-notes" rows="4" placeholder="上周哪里需要加强、本周怎么调整…">${escapeHtml(w.notes || '')}</textarea>
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
      } catch (e) {
        alert('保存失败：' + e.message)
      }
    })
  }
}

function addDays(iso, n) {
  const d = new Date(iso + 'T00:00:00')
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}

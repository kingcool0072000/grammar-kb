// 微练习完成度（教师端 #/microProgress）：每生汇总卡（完成天数/答题数/
// 正确率/最近活跃）+ 点开逐天明细（每天题目对错概要，复用学生 history
// 数据形态）。数据 = GET /micro-admin/progress（user 空=全体，指定=明细）。
import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

const pct = (a, b) => (b ? Math.round((a / b) * 100) : null)

export async function mountMicroProgress(el) {
  el.innerHTML = `
    <div class="view-head">
      <h1>✏️ 微练习 · 学生完成度</h1>
      <p>点击学生卡片查看逐天明细（每天的题目与对错）。</p>
    </div>
    <div id="mp-body"><p class="muted">加载中…</p></div>`
  const body = el.querySelector('#mp-body')

  let users = []
  try {
    users = (await api.microAdminProgress()).users || []
  } catch (e) {
    body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
  if (!users.length) {
    body.innerHTML = '<p class="muted">还没有学生做过微练习。</p>'
    return
  }
  renderUsers()

  function renderUsers() {
    body.innerHTML = users.map((u) => {
      const acc = u.accuracy != null ? `${Math.round(u.accuracy * 100)}%` : '—'
      return `
        <button type="button" class="mp-user card" data-user="${escAttr(u.user)}">
          <b class="mp-user-name">${escapeHtml(u.user)}</b>
          <span class="mp-user-stat">完成 <b>${u.days_done ?? 0}</b> 天</span>
          <span class="mp-user-stat">答题 <b>${u.answered_n ?? 0}</b> 次</span>
          <span class="mp-user-stat">正确率 <b>${acc}</b></span>
          <span class="mp-user-stat">最近 <b>${escapeHtml(u.last_day || '—')}</b></span>
          <span class="mp-user-go">明细 →</span>
        </button>`
    }).join('')
    body.querySelectorAll('[data-user]').forEach((btn) => {
      btn.addEventListener('click', () => openDetail(btn.dataset.user))
    })
  }

  async function openDetail(user) {
    let d
    try {
      d = await api.microAdminProgress(user)
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">明细加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    const days = d.days || []
    const acc = pct(d.correct_n, d.answered_n)
    body.innerHTML = `
      <div class="mp-detail-head">
        <button class="reading-btn small" type="button" id="mp-back">← 全部学生</button>
        <b>${escapeHtml(user)}</b> · 完成 ${d.days_done ?? 0} 天 · 答题 ${d.answered_n ?? 0} 次
        · 正确率 ${acc != null ? acc + '%' : '—'}
      </div>
      ${days.length ? days.map((day) => `
        <details class="mc-hist-day"${day.done ? '' : ''}>
          <summary>
            <b>${escapeHtml(day.date)}</b>
            <span class="mc-hist-score">${day.correct_n != null ? `答对 ${day.correct_n}/${day.total}` : `做了 ${day.n}/${day.total}`}</span>
          </summary>
          ${detailQuestionsHtml(day)}
        </details>`).join('')
        : '<p class="muted">该生还没有微练习记录。</p>'}`
    body.querySelector('#mp-back').addEventListener('click', renderUsers)
  }

  function detailQuestionsHtml(day) {
    const qs = Array.isArray(day.questions) ? day.questions : []
    return qs.map((q, i) => {
      const opts = Array.isArray(q.options) ? q.options : []
      const optHtml = opts.map((t, j) => {
        const key = String.fromCharCode(65 + j)
        const cls = ['mc-opt', 'locked']
        if (key === q.answer) cls.push('right')
        if (!q.my_correct && key === q.my_choice) cls.push('wrong')
        return `<div class="${cls.join(' ')}">
          <span class="mc-opt-key">${key}</span>
          <span class="mc-opt-text">${escapeHtml(t)}</span>
          ${key === q.answer ? '<span class="mc-opt-tag">正确答案</span>' : ''}
        </div>`
      }).join('')
      return `
        <section class="mc-card">
          <div class="mc-card-head">
            <span class="mc-qnum">第 ${i + 1} 题</span>
            <span class="mc-verdict ${q.my_correct ? 'ok' : 'no'}">${q.my_correct ? '✅ 对' : '❌ 错'}</span>
            <span class="mc-topic">${escapeHtml(q.my_choice ? `选 ${q.my_choice}` : '未答')}</span>
          </div>
          <p class="mc-stem">${escapeHtml(q.stem)}</p>
          <div class="mc-opts">${optHtml}</div>
          ${q.explain ? `<div class="mc-explain"><b>解析</b>${escapeHtml(q.explain)}</div>` : ''}
        </section>`
    }).join('')
  }
}

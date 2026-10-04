import { api } from '../api.js'
import { escapeHtml, todayIso } from '../render.js'

// 教师版 · 词汇级别考试（原计划表功能块迁入备课中心）：
// 登记线下考试成绩（≥80 分自动解锁下一级词库）+ 打印考卷。
export async function mountVocabExam(el) {
  const today = todayIso()
  el.innerHTML = `
    <div class="view-head">
      <button class="chip" data-back>← 返回备课中心</button>
      <h1>词汇级别考试</h1>
      <p>线下考完登记成绩，≥80 分自动解锁下一级词库（L0 恒开）。</p>
    </div>
    <div id="ve-body"><p class="muted">加载中…</p></div>
  `
  el.querySelector('[data-back]').addEventListener('click', () => {
    location.hash = '/prep'
  })
  const body = el.querySelector('#ve-body')

  let vex
  try {
    vex = await api.vocabExams()
  } catch (e) {
    body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
  render()

  function render() {
    const passed = vex.passed_levels || []
    const unlocked = vex.unlocked_level ?? 0
    body.innerHTML = `
      <section class="fce-group plan-vocab-exam">
        <p class="plan-vex-state">已过：${passed.length ? passed.map((l) => 'L' + l).join('、') : '无'} · 当前解锁到 <b>L${unlocked}</b>（80 分解锁下一级）</p>
        <div class="plan-vex-row">
          <label>级别 <select id="pv-level">${[0, 1, 2, 3, 4].map((l) => `<option value="${l}" ${l === unlocked ? 'selected' : ''}>L${l} → L${l + 1}</option>`).join('')}</select></label>
          <label>学生 <select id="pv-user"><option value="malin">malin</option><option value="mxy">mxy</option></select></label>
          <label>日期 <input id="pv-date" type="date" value="${today}"/></label>
          <label>得分 <input id="pv-score" type="number" min="0" max="100" placeholder="0-100" style="width:90px"/></label>
          <button class="btn-primary" id="pv-record">登记成绩</button>
          <button class="chip" id="pv-paper">🖨 打印考卷</button>
        </div>
        <div id="pv-exam-list">${examList(vex.exams || [])}</div>
      </section>`
    bind()
  }

  function examList(items) {
    if (!items.length) return '<p class="muted" style="margin:6px 0 0">还没有考试记录。</p>'
    return `<div class="plan-vex-history">${items.slice(0, 8).map((x) => `
      <span class="plan-vex-item ${x.score >= 80 ? 'ok' : 'fail'}">L${x.level} · ${x.score} 分 · ${x.exam_date}<button data-del="${x.id}" title="删除">×</button></span>`).join('')}</div>`
  }

  function bind() {
    body.querySelector('#pv-record').addEventListener('click', async () => {
      const user = body.querySelector('#pv-user').value
      const level = Number(body.querySelector('#pv-level').value)
      const score = Number(body.querySelector('#pv-score').value)
      const exam_date = body.querySelector('#pv-date').value
      if (!(score >= 0 && score <= 100) || !exam_date) return alert('得分与日期须填完整')
      try {
        await api.vocabExamAdd({ user, level, score, exam_date })
        vex = await api.vocabExams()
        render()
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
          vex = await api.vocabExams()
          render()
        } catch (err) { alert(err.message) }
      }))
  }

  function toast(msg) {
    const t = document.createElement('div')
    t.className = 'plan-toast'
    t.textContent = msg
    document.body.appendChild(t)
    setTimeout(() => t.remove(), 3200)
  }
}

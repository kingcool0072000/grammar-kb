// FCE 真题 · 教师批改流：待批改作文列表 → 打分/评语 → 提交批改。
// 入口按钮与公共骨架见 ../fcePapers.js，做题流见 ./student.js。
import { api } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { mountFcePapers } from '../fcePapers.js'

// ---------- 教师批改作文 ----------
export async function renderEssayReview(el, pending, role) {
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="fce-back">← 返回</button>
      <h1>待批改作文（${pending.length}）</h1>
    </div>
    <div id="essay-list">${pending.map(essayCard).join('') || '<div class="empty">暂无待批改作文</div>'}</div>
  `
  el.querySelector('#fce-back').addEventListener('click', () => mountFcePapers(el, { role }))

  el.querySelector('#essay-list').addEventListener('click', async (e) => {
    const gradeBtn = e.target.closest('.essay-grade-btn')
    if (!gradeBtn) return
    const card = gradeBtn.closest('.essay-card')
    const id = Number(card.dataset.id)
    const score = card.querySelector('.essay-score-input').value
    const comment = card.querySelector('.essay-comment-input').value
    gradeBtn.disabled = true
    try {
      await api.fceGrade(id, { teacher_score: score === '' ? null : Number(score), teacher_comment: comment })
      card.innerHTML = '<div class="fce-result-box good" style="margin:0"><h3>已批改 ✓</h3></div>'
    } catch (err) {
      alert(`批改失败：${err.message}`)
      gradeBtn.disabled = false
    }
  })
}

function essayCard(s) {
  const essayText = Object.values(s.answers || {}).join('\n\n') || '（未作答）'
  const q = (s.detail || []).length
  return `
    <div class="essay-card" data-id="${s.id}">
      <div class="essay-head">
        <b>${escapeHtml(s.user)}</b> · T${s.test_id} 写作 P${s.part} · ${(s.created_at || '').slice(0, 10)}
      </div>
      <pre class="essay-text">${escapeHtml(essayText)}</pre>
      <div class="essay-grade-row">
        <input type="number" class="essay-score-input" min="0" max="100" placeholder="分数 0-100">
        <input type="text" class="essay-comment-input" placeholder="评语（可选）">
        <button class="essay-grade-btn">批改完成</button>
      </div>
    </div>`
}

// ---------- 内嵌批改包装（备课中心「待批作文」队列挂载用） ----------
// 与 renderEssayReview 同一套卡片与提交逻辑（复用 essayCard），区别只在：
// 不回 mountFcePapers 外跳，返回动作交给 opts.onBack；列表容器用 data 属性
// 而非 id，避免嵌入分区后与页面其它节点撞 id。数据直接用 fceSubmissions
// 列表行（后端 _sub_out 自带 answers/detail，essayCard 需要的字段都在）。
export function mountEssayReviewInline(el, { pending, onBack } = {}) {
  const items = Array.isArray(pending) ? pending : []
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" data-fce-inline-back>← 返回</button>
      <h1>待批改作文（${items.length}）</h1>
    </div>
    <div data-essay-list>${items.map(essayCard).join('') || '<div class="empty">暂无待批改作文</div>'}</div>
  `
  const backBtn = el.querySelector('[data-fce-inline-back]')
  if (backBtn) backBtn.addEventListener('click', () => { if (onBack) onBack() })

  el.querySelector('[data-essay-list]').addEventListener('click', async (e) => {
    const gradeBtn = e.target.closest('.essay-grade-btn')
    if (!gradeBtn) return
    const card = gradeBtn.closest('.essay-card')
    const id = Number(card.dataset.id)
    const score = card.querySelector('.essay-score-input').value
    const comment = card.querySelector('.essay-comment-input').value
    gradeBtn.disabled = true
    try {
      await api.fceGrade(id, { teacher_score: score === '' ? null : Number(score), teacher_comment: comment })
      card.innerHTML = '<div class="fce-result-box good" style="margin:0"><h3>已批改 ✓</h3></div>'
    } catch (err) {
      alert(`批改失败：${err.message}`)
      gradeBtn.disabled = false
    }
  })
}

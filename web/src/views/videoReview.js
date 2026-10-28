import { api, getAuth } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

// 视频复述批改（共享模块，仿 readingReview.js）：试听 + 10 分制打分 +
// 评语 + 删除；音频走二进制端点直连（token 查询参数）。
// 与精读录音批改保持同一套交互，教师无学习成本。

export async function renderVideoReview(el, retells, opts) {
  const onBack = (opts || {}).onBack
  const pending = retells.filter((r) => r.status === 'pending')
  const list = pending.length ? pending : retells
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="vr-back">← 返回批改中心</button>
      <h1>视频复述批改${pending.length ? `（待批 ${pending.length}）` : ''}</h1>
      <p>听学生看完视频后的复述录音，10 分制打分 + 评语。</p>
    </div>
    <div id="vr-list"></div>
  `
  const box = el.querySelector('#vr-list')
  const slice = list.slice(0, 30)
  for (const r of slice) {
    const row = document.createElement('div')
    row.className = 'reading-review-card'
    row.innerHTML = `
      <div class="reading-review-head">
        <b>${escapeHtml(r.user)}</b> · 🎬 ${escapeHtml(r.video_title || r.vid)}
        <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
      </div>
      <audio controls preload="metadata" style="width:100%;margin:8px 0"></audio>
      <div class="reading-review-grade">
        <label>分数 <input type="number" min="0" max="10" value="${r.teacher_score ?? 8}" style="width:56px" data-score></label>
        <input placeholder="评语（内容完整度/表达/用词建议）" value="${escAttr(r.teacher_comment || '')}" data-comment style="flex:1" />
        <button class="reading-btn primary" data-grade>提交批改</button>
        <button class="reading-btn danger" data-del title="删除这条复述录音">删除</button>
      </div>
      <div class="reading-review-msg"></div>
    `
    const audio = row.querySelector('audio')
    const token = getAuth()?.token
    if (token) audio.src = `/api/video-study/retells/${r.id}/audio?token=${encodeURIComponent(token)}`
    row.querySelector('[data-grade]').addEventListener('click', async () => {
      const score = Number(row.querySelector('[data-score]').value)
      const comment = row.querySelector('[data-comment]').value
      if (!(score >= 0 && score <= 10)) {
        row.querySelector('.reading-review-msg').textContent = '分数须为 0-10'
        return
      }
      try {
        await api.videoRetellGrade(r.id, { score, comment })
        row.querySelector('.reading-review-msg').innerHTML = `✅ 已批改：${score}/10`
        row.querySelector('[data-grade]').disabled = true
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `失败：${e.message}`
      }
    })
    row.querySelector('[data-del]').addEventListener('click', async () => {
      if (!confirm(`确定删除 ${r.user} 的这条视频复述录音（${fmtDur(r.duration_sec || 0)}）？删除后不可恢复。`)) return
      try {
        await api.videoRetellDelete(r.id)
        row.remove()
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `删除失败：${e.message}`
      }
    })
    box.append(row)
  }
  if (!slice.length) box.innerHTML = '<p class="reading-hint">暂无视频复述提交</p>'
  el.querySelector('#vr-back').addEventListener('click', () => {
    try {
      if (onBack) {
        const result = onBack()
        if (result !== false) return
      }
    } catch { /* 回退到路由 */ }
    location.hash = '/grading?tab=board&from=videoReview'
  })
}

export function videoRetellRow(r) {
  const score = r.status === 'graded'
    ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
    : '<b class="fce-his-score pend">待批改</b>'
  return `
    <div class="fce-his-row reading-rec-row" data-vretell="${r.id}">
      <span class="fce-his-what">${escapeHtml(r.user)} · 🎬 ${escapeHtml(r.video_title || r.vid)}</span>
      ${score}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

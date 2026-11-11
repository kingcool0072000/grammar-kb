import { api, getAuth } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'
import { fmtDur } from '../fmt.js'

// NCE2 听力跟读批改（共享模块，仿 videoReview.js）：试听 + 10 分制打分 +
// 评语 + 删除；音频走二进制端点直连（token 查询参数）。与精读录音/视频
// 复述批改保持同一套交互，教师无学习成本。
// 行来源两种 kind：shadow=跟读录音（可试听打分）；listen=精听打卡（无
// 录音，仅状态卡，可打分/删除不阻碍队列）。

function kindTag(k) {
  return k === 'listen'
    ? '<span class="tag vr-kind-listen">👂 精听打卡</span>'
    : '<span class="tag vr-kind-shadow">🗣 跟读录音</span>'
}

function subSourceHtml(r) {
  return `${kindTag(r.kind)} Lesson ${r.lesson_id ?? '—'} · ${escapeHtml(r.lesson_title || '')}`
}

export async function renderNce2Review(el, subs, opts) {
  const onBack = (opts || {}).onBack
  const pending = subs.filter((r) => r.status === 'pending')
  const list = pending.length ? pending : subs
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="nce2r-back">← 返回批改中心</button>
      <h1>听力跟读批改${pending.length ? `（待批 ${pending.length}）` : ''}</h1>
      <p>听学生的跟读录音（NCE2），10 分制打分 + 评语；≥7 分显示「过关」。</p>
    </div>
    <div id="nce2r-list"></div>
  `
  const box = el.querySelector('#nce2r-list')
  const slice = list.slice(0, 30)
  for (const r of slice) {
    const row = document.createElement('div')
    row.className = 'reading-review-card'
    row.innerHTML = `
      <div class="reading-review-head">
        <b>${escapeHtml(r.user)}</b> · ${subSourceHtml(r)}
        <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
      </div>
      ${r.kind === 'listen'
        ? '<p class="reading-hint">👂 精听打卡（无录音）——打卡本身无需批改，只可删除。</p>'
        : '<audio controls preload="metadata" style="width:100%;margin:8px 0"></audio>'}
      ${r.kind === 'listen'
        ? `<button class="reading-btn danger" data-del title="删除这条提交">删除</button>`
        : `<div class="reading-review-grade">
        <label>分数 <input type="number" min="0" max="10" value="${r.teacher_score ?? 8}" style="width:56px" data-score></label>
        <input placeholder="评语（听懂程度/发音/语调建议）" value="${escAttr(r.teacher_comment || '')}" data-comment style="flex:1" />
        <button class="reading-btn primary" data-grade>提交批改</button>
        <button class="reading-btn danger" data-del title="删除这条提交">删除</button>
      </div>`}
      <div class="reading-review-msg"></div>
    `
    const audio = row.querySelector('audio')
    if (audio) {
      const token = getAuth()?.token
      if (token) audio.src = `/api/nce2/submissions/${r.id}/audio?token=${encodeURIComponent(token)}`
    }
    const gradeBtn = row.querySelector('[data-grade]')
    if (gradeBtn) gradeBtn.addEventListener('click', async () => {
      const score = Number(row.querySelector('[data-score]').value)
      const comment = row.querySelector('[data-comment]').value
      if (!(score >= 0 && score <= 10)) {
        row.querySelector('.reading-review-msg').textContent = '分数须为 0-10'
        return
      }
      try {
        await api.nce2Grade(r.id, score, comment)
        row.querySelector('.reading-review-msg').innerHTML =
          `✅ 已批改：${score}/10${score >= 7 ? ' <b class="nce2-pass-tag">过关</b>' : ''}`
        row.querySelector('[data-grade]').disabled = true
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `失败：${e.message}`
      }
    })
    row.querySelector('[data-del]').addEventListener('click', async () => {
      if (!confirm(`确定删除 ${r.user} 的这条 NCE2 提交（Lesson ${r.lesson_id} · ${fmtDur(r.duration_sec || 0)}）？删除后不可恢复。`)) return
      try {
        await api.nce2Delete(r.id)
        row.remove()
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `删除失败：${e.message}`
      }
    })
    box.append(row)
  }
  if (!slice.length) box.innerHTML = '<p class="reading-hint">暂无 NCE2 听力提交</p>'
  el.querySelector('#nce2r-back').addEventListener('click', () => {
    try {
      if (onBack) {
        const result = onBack()
        if (result !== false) return
      }
    } catch { /* 回退到路由 */ }
    location.hash = '/prep?from=nce2Review' // 带 query 防同 hash（#/prep）赋值不触发路由
  })
}

// 队列/已批折叠区共用行：学生名 + 课标题 + 类型 + 状态/分数 + 时长日期
export function nce2SubRow(r) {
  const graded = r.status === 'graded'
  const score = graded
    ? `<b class="fce-his-score ${r.teacher_score >= 7 ? 'ok' : ''}">${r.teacher_score}/10${r.teacher_score >= 7 ? ' 过关' : ''}</b>`
    : '<b class="fce-his-score pend">待批改</b>'
  return `
    <div class="fce-his-row" data-nce2="${r.id}">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${subSourceHtml(r)}</span>
      ${score}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

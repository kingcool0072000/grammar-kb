import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

// 精读录音批改（共享模块）：从 readingAdmin 抽出，批改中心承载。
// 含：批改视图（renderReview，试听+10 分制打分+评语+导出）、
// 提交行（recRow）、行内删除/编辑（bindRecRowActions）。

export async function renderReadingReview(el, recs, opts) {
  return renderReview(el, recs, (opts || {}).onBack)
}

// 最近录音提交行的 删除 / 编辑（改分数与评语）
function bindRecRowActions(el, recs) {
  el.querySelectorAll('[data-rec-del]').forEach((b) => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation()
      const id = Number(b.dataset.recDel)
      const r = recs.find((x) => x.id === id)
      if (!confirm(`确定删除 ${r?.user || ''} 的这条录音（${fmtDur(r?.duration_sec || 0)}）？删除后不可恢复。`)) return
      try {
        await api.readingDeleteRecording(id)
        const row = b.closest('.reading-rec-row')
        if (row) row.remove()
      } catch (err) {
        alert(`删除失败：${err.message}`)
      }
    })
  })
  el.querySelectorAll('[data-rec-edit]').forEach((b) => {
    b.addEventListener('click', (e) => {
      e.stopPropagation()
      const id = Number(b.dataset.recEdit)
      const r = recs.find((x) => x.id === id)
      const row = b.closest('.reading-rec-row')
      if (!r || !row) return
      if (row.querySelector('.reading-rec-editor')) return // 已在编辑态
      const editor = document.createElement('div')
      editor.className = 'reading-rec-editor'
      editor.innerHTML = `
        <label>分数 <input type="number" min="0" max="10" value="${r.teacher_score ?? 8}" style="width:56px" data-ed-score></label>
        <input placeholder="评语（流利度/发音/语调建议）" value="${escAttr(r.teacher_comment || '')}" data-ed-comment style="flex:1">
        <button class="reading-btn small primary" data-ed-save>保存</button>
        <button class="reading-btn small" data-ed-cancel>取消</button>
      `
      row.after(editor)
      editor.querySelector('[data-ed-save]').addEventListener('click', async () => {
        const score = Number(editor.querySelector('[data-ed-score]').value)
        const comment = editor.querySelector('[data-ed-comment]').value
        if (!(score >= 0 && score <= 10)) {
          alert('分数须为 0-10')
          return
        }
        try {
          await api.readingGradeRecording(id, { score, comment })
          r.teacher_score = score
          r.teacher_comment = comment
          r.status = 'graded'
          // 就地刷新该行显示
          const fresh = document.createElement('div')
          fresh.innerHTML = recRow(r)
          const newRow = fresh.firstElementChild
          row.replaceWith(newRow)
          bindRecRowActions(el, recs)
        } catch (err) {
          alert(`保存失败：${err.message}`)
        }
        editor.remove()
      })
      editor.querySelector('[data-ed-cancel]').addEventListener('click', () => editor.remove())
    })
  })
}
function recRow(r) {
  const score = r.status === 'graded'
    ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
    : '<b class="fce-his-score pend">待批改</b>'
  return `
    <div class="fce-his-row reading-rec-row" data-rec="${r.id}">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
      ${score}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
      <button class="reading-btn small" data-rec-edit="${r.id}" title="编辑分数与评语">✏️ 编辑</button>
      <button class="reading-btn small danger" data-rec-del="${r.id}" title="删除这条录音">删除</button>
    </div>`
}
async function renderReview(el, recs, onBack) {
  const pending = recs.filter((r) => r.status === 'pending')
  const list = pending.length ? pending : recs
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="rd-back">← 返回批改中心</button>
      <h1>录音批改${pending.length ? `（待批 ${pending.length}）` : ''}</h1>
      <p>试听学生录音，10 分制打分 + 评语。</p>
    </div>
    <div class="reading-review-tools">
      <button class="reading-btn" id="rd-export-all">⬇️ 导出全部（音频 + 朗读原文）</button>
      <span class="reading-pick-info">文件保存到下载目录，可交给其他工具/模型分析</span>
    </div>
    <div id="rd-review-list"></div>
  `
  const box = el.querySelector('#rd-review-list')
  // 并行预取全部录音（每条仅几 KB～几十 KB）与文章，转 Blob URL 一次性挂到
  // <audio>——原生控件直接播放，无懒加载时序/自动播放策略问题
  const slice = list.slice(0, 30)
  const [fulls, arts] = await Promise.all([
    Promise.all(slice.map((r) => api.readingRecording(r.id).catch(() => null))),
    Promise.all(slice.map((r) => api.readingArticle(r.article_id).catch(() => null))),
  ])
  const exportable = [] // {blobUrl, name, textName, text} 供导出
  for (let i = 0; i < slice.length; i++) {
    const r = slice[i]
    const full = fulls[i]
    const art = arts[i]
    const row = document.createElement('div')
    row.className = 'reading-review-card'
    row.innerHTML = `
      <div class="reading-review-head">
        <b>${escapeHtml(r.user)}</b> · ${escapeHtml(r.article_title || '')}
        <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
      </div>
      ${r.selected_text ? `
      <details class="reading-review-text" open><summary>学生朗读的选段</summary>
        <div class="reading-article picked-view">${escapeHtml(r.selected_text).split(/\n+/).map((p) => `<p>${p}</p>`).join('')}</div>
      </details>` : ''}
      <details class="reading-review-text"><summary>查看全文</summary>
        <div class="reading-article">${(art?.text || '').split(/\n+/).map((p) => `<p>${escapeHtml(p).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}</p>`).join('')}</div>
      </details>
      <audio controls preload="metadata" style="width:100%;margin:8px 0"></audio>
      <div class="reading-review-grade">
        <label>分数 <input type="number" min="0" max="10" value="${r.teacher_score ?? 8}" style="width:56px" data-score></label>
        <input placeholder="评语（流利度/发音/语调建议）" value="${escAttr(r.teacher_comment || '')}" data-comment style="flex:1" />
        <button class="reading-btn primary" data-grade>提交批改</button>
        <button class="reading-btn" data-dl title="下载这条录音（音频 + 朗读原文）">⬇️ 下载</button>
        <button class="reading-btn danger" data-del title="删除这条录音提交">删除</button>
      </div>
      <div class="reading-review-msg"></div>
    `
    // 音频：Blob URL（Chrome/Safari 原生播放；m4a 已在入库时转码）
    const audio = row.querySelector('audio')
    let objectUrl = null
    if (full?.audio_b64) {
      try {
        const bin = atob(full.audio_b64)
        const buf = new Uint8Array(bin.length)
        for (let j = 0; j < bin.length; j++) buf[j] = bin.charCodeAt(j)
        objectUrl = URL.createObjectURL(new Blob([buf], { type: full.mime || 'audio/mp4' }))
        audio.src = objectUrl
      } catch { /* 解码失败则留空，播放控件显示无源 */ }
    }
    // 导出命名（与 CLI export-recordings 一致）
    const when = (r.created_at || '').slice(0, 16).replace(/[-:T]/g, '')
    const ext = (full?.mime || 'audio/mp4').includes('webm') ? 'webm' : 'm4a'
    const audioName = `rec${r.id}_${r.user}_${when}.${ext}`
    const textName = `rec${r.id}_selected_text.txt`
    if (objectUrl) exportable.push({ url: objectUrl, name: audioName, textName, text: r.selected_text })
    row.querySelector('[data-dl]').addEventListener('click', () => {
      if (objectUrl) dlFileGlobal(objectUrl, audioName)
      if (r.selected_text) dlTextGlobal(textName, r.selected_text)
    })
    row.querySelector('[data-grade]').addEventListener('click', async () => {
      const score = Number(row.querySelector('[data-score]').value)
      const comment = row.querySelector('[data-comment]').value
      if (!(score >= 0 && score <= 10)) {
        row.querySelector('.reading-review-msg').textContent = '分数须为 0-10'
        return
      }
      try {
        await api.readingGradeRecording(r.id, { score, comment })
        row.querySelector('.reading-review-msg').innerHTML = `✅ 已批改：${score}/10`
        row.querySelector('[data-grade]').disabled = true
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `失败：${e.message}`
      }
    })
    row.querySelector('[data-del]').addEventListener('click', async () => {
      if (!confirm(`确定删除 ${r.user} 的这条录音（${fmtDur(r.duration_sec || 0)}）？删除后不可恢复。`)) return
      try {
        await api.readingDeleteRecording(r.id)
        if (objectUrl) URL.revokeObjectURL(objectUrl)
        const at = exportable.findIndex((x) => x.name === audioName)
        if (at >= 0) exportable.splice(at, 1)
        row.remove()
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `删除失败：${e.message}`
      }
    })
    box.append(row)
  }
  el.querySelector('#rd-export-all').addEventListener('click', () => {
    if (!exportable.length) {
      alert('没有可导出的录音')
      return
    }
    exportable.forEach((x, i) => {
      setTimeout(() => {
        dlFileGlobal(x.url, x.name)
        if (x.text) dlTextGlobal(x.textName, x.text)
      }, i * 350) // 浏览器对连发下载有拦截，错峰触发
    })
  })
  if (!list.length) box.innerHTML = '<p class="reading-hint">暂无录音提交</p>'
  el.querySelector('#rd-back').addEventListener('click', () => {
    if (onBack) onBack()
    else location.hash = '/grading'
  })
}

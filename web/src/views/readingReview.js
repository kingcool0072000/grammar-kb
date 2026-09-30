import { api, getAuth } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

// 精读录音批改（共享模块）：从 readingAdmin 抽出，批改中心承载。v2 缓存刷新。
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
  // 音频走二进制端点直连（<audio src> 浏览器按需流式拉取，不再预取
  // 详情 JSON 里 2MB+ 的 base64——首开全量预取曾是主瓶颈）。
  // 文章全文仍预取（纯文本，量小）；单条最多等 4 秒不阻塞整个列表。
  const slice = list.slice(0, 30)
  const withTimeout = (promise, ms = 4000) => Promise.race([
    promise,
    new Promise((resolve) => setTimeout(() => resolve(null), ms)),
  ]).catch(() => null)
  const arts = await Promise.all(
    slice.map((r) => withTimeout(api.readingArticle(r.article_id))))
  const exportable = [] // {audioUrl, name, textName, text} 供导出
  for (let i = 0; i < slice.length; i++) {
    const r = slice[i]
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
    // 音频：二进制端点直连（token 走查询参数——媒体标签无法带
    // Authorization 头）；浏览器按需流式拉取，列表秒开
    const audio = row.querySelector('audio')
    const audioUrl = getAuth()?.token
      ? `/api/reading/recordings/${r.id}/audio?token=${encodeURIComponent(getAuth().token)}`
      : null
    if (audioUrl) audio.src = audioUrl
    // 导出命名（与 CLI export-recordings 一致）
    const when = (r.created_at || '').slice(0, 16).replace(/[-:T]/g, '')
    const ext = (r.mime || 'audio/mp4').includes('webm') ? 'webm' : 'm4a'
    const audioName = `rec${r.id}_${r.user}_${when}.${ext}`
    const textName = `rec${r.id}_selected_text.txt`
    if (audioUrl) exportable.push({ url: audioUrl, name: audioName, textName, text: r.selected_text })
    row.querySelector('[data-dl]').addEventListener('click', async () => {
      if (audioUrl) await dlAudioGlobal(audioUrl, audioName)
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
      setTimeout(async () => {
        await dlAudioGlobal(x.url, x.name)
        if (x.text) dlTextGlobal(x.textName, x.text)
      }, i * 350) // 浏览器对连发下载有拦截，错峰触发
    })
  })
  if (!list.length) box.innerHTML = '<p class="reading-hint">暂无录音提交</p>'
  el.querySelector('#rd-back').addEventListener('click', () => {
    // 上层看板提供回调；回调返回 false/异常时用带 query 的 hash 强制刷新上层，
    // 避免当前 hash 已是 #/grading 时 location.hash 赋同值不触发路由。
    try {
      if (onBack) {
        const result = onBack()
        if (result !== false) return
      }
    } catch { /* 回退到路由 */ }
    location.hash = '/grading?tab=board&from=review'
  })
}

function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

function dlFileGlobal(blobUrl, name) {
  const a = document.createElement('a')
  a.href = blobUrl
  a.download = name
  document.body.append(a)
  a.click()
  a.remove()
}

// 二进制音频端点 → Blob 下载（音频不再有本地 blob URL，下载时现取）
async function dlAudioGlobal(url, name) {
  try {
    const resp = await fetch(url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const u = URL.createObjectURL(await resp.blob())
    dlFileGlobal(u, name)
    setTimeout(() => URL.revokeObjectURL(u), 5000)
  } catch (e) {
    alert(`音频下载失败：${e.message}`)
  }
}

function dlTextGlobal(name, text) {
  const u = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  dlFileGlobal(u, name)
  setTimeout(() => URL.revokeObjectURL(u), 5000)
}

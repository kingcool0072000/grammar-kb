import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { speak } from '../tts.js'

// ✅ 已攻克题本（学生页 #/conquered）：全部背完掌握的单词——
// 一遍背会（从未进错题本）+ 从错题周期六轮复习全对走出来的。
// UI 复用教师版 vocabLevel 的词表（vl-* 样式）：搜索 + 每页 40 词 + 点词弹窗。
export async function mountConquered(el) {
  el.innerHTML = `
    <button class="fce-back-btn" id="cq-back">← 返回背单词</button>
    <div class="view-head">
      <h1>✅ 已攻克题本</h1>
      <p id="cq-sub">加载中…</p>
    </div>
    <section class="vl-sec card">
      <header class="vl-head">
        <h3>📚 已学过的单词</h3>
        <div class="vl-tools">
          <input id="cq-search" placeholder="搜索单词 / 释义…"/>
        </div>
      </header>
      <div class="vl-words" id="cq-words"></div>
      <div class="vl-pager" id="cq-pager"></div>
    </section>`

  el.querySelector('#cq-back').addEventListener('click', () => {
    location.hash = '/recite'
  })

  let words = []
  try {
    const d = await api.wrongbook()
    words = (d && d.conquered) || []
  } catch (e) {
    el.querySelector('#cq-sub').innerHTML =
      `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }

  const fromReview = words.filter((w) => w.conquered_at || w.wrong_count).length
  el.querySelector('#cq-sub').textContent =
    `共 ${words.length} 词全部背完掌握 · 一遍过 ${words.length - fromReview} · 从错题攻克 ${fromReview}`

  const glossOf = (w) => {
    if (w.gloss) return w.gloss
    const m = w.meanings || []
    return Array.isArray(m) ? m.slice(0, 2).join('；') : ''
  }

  // 分页展示（每页 40 词；搜索按结果分页）——与 vocabLevel 词表一致
  const PAGE = 40
  let page = 0
  const search = el.querySelector('#cq-search')
  const curList = () => {
    const q = search.value.trim().toLowerCase()
    return !q ? words : words.filter((w) =>
      (w.word || '').toLowerCase().includes(q) || glossOf(w).toLowerCase().includes(q))
  }

  function renderPage() {
    const list = curList()
    const pages = Math.max(1, Math.ceil(list.length / PAGE))
    if (page >= pages) page = pages - 1
    const slice = list.slice(page * PAGE, page * PAGE + PAGE)
    el.querySelector('#cq-words').innerHTML = slice.length ? slice.map((w) => `
      <div class="vl-word" data-word="${escapeHtml(w.word || '')}" title="点击查看完整内容">
        <b>${escapeHtml(w.word || '')}${w.wrong_count ? `<u class="cq-flag" title="从错题攻克（曾错 ${w.wrong_count} 次）">✓</u>` : ''}</b>
        <span class="vl-pos">${(w.pos || []).join(' ')}</span>
        <i>${escapeHtml(glossOf(w).slice(0, 36))}</i>
      </div>`).join('') : '<p class="muted">没有匹配的词条。</p>'
    el.querySelector('#cq-pager').innerHTML = `
      <button class="chip" data-pg="prev" ${page === 0 ? 'disabled' : ''}>‹ 上一页</button>
      <span class="vl-page-info">第 ${page + 1} / ${pages} 页 · 共 ${list.length} 词</span>
      <button class="chip" data-pg="next" ${page >= pages - 1 ? 'disabled' : ''}>下一页 ›</button>`
    el.querySelectorAll('[data-pg]').forEach((b) =>
      b.addEventListener('click', () => {
        page += b.dataset.pg === 'next' ? 1 : -1
        if (page < 0) page = 0
        renderPage()
      }))
    el.querySelectorAll('.vl-word[data-word]').forEach((w) =>
      w.addEventListener('click', () => openWordDialog(w.dataset.word)))
  }

  function openWordDialog(wordStr) {
    const w = words.find((x) => x.word === wordStr)
    if (!w) return
    const ex = w.example || {}
    const src = w.conquered_at
      ? `从错题攻克 · ${(w.conquered_at || '').slice(0, 10)}（曾错 ${w.wrong_count || 1} 次）`
      : `一遍过 · ${(w.mastered_at || '').slice(0, 10)} 掌握`
    const dlg = document.createElement('div')
    dlg.className = 'plan-editor'
    dlg.innerHTML = `
      <div class="plan-editor-mask"></div>
      <div class="plan-editor-body card vl-word-dlg">
        <h3>${escapeHtml(w.word || '')} <i class="vl-dlg-pos">${(w.pos || []).join(' ')}</i>
          <button class="chip" id="cq-dlg-say" title="发音">🔊</button></h3>
        <div class="vl-dlg-sec"><b>释义</b><p>${escapeHtml(glossOf(w) || '（暂无）')}</p></div>
        ${ex.en ? `<div class="vl-dlg-sec"><b>例句</b><p class="vl-dlg-en">${escapeHtml(ex.en)}</p>${ex.zh ? `<p class="vl-dlg-zh">${escapeHtml(ex.zh)}</p>` : ''}</div>` : ''}
        <div class="vl-dlg-sec"><b>掌握</b><p>${escapeHtml(src)} · 累计答对 ${w.right ?? '—'} 次 / 答错 ${w.wrong ?? '—'} 次</p></div>
        <div class="chip-row"><button class="chip" id="cq-dlg-close">关闭</button></div>
      </div>`
    document.body.appendChild(dlg)
    const close = () => dlg.remove()
    dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
    dlg.querySelector('#cq-dlg-close').addEventListener('click', close)
    dlg.querySelector('#cq-dlg-say').addEventListener('click', () => speak(w.word))
  }

  search.addEventListener('input', () => { page = 0; renderPage() })
  renderPage()
}

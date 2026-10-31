import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { fillAtlasHost, speakWord } from '../atlas.js'
import { getWordCard } from '../wordcard.js'

// ✅ 已攻克题本（学生页 #/conquered）：全部背完掌握的单词——
// 一遍背会（从未进错题本）+ 从错题周期六轮复习全对走出来的。
// 分页在服务端：每页 40 词，浏览器不装下全集。
const PAGE = 40

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
      <div class="vl-words" id="cq-words"><p class="muted">加载中…</p></div>
      <div class="vl-pager" id="cq-pager"></div>
    </section>`

  el.querySelector('#cq-back').addEventListener('click', () => {
    location.hash = '/recite'
  })

  let page = 1
  let items = []
  let summary = { total: 0, from_review: 0 }
  let seq = 0
  let searchTimer = null
  const search = el.querySelector('#cq-search')

  const glossOf = (w) => {
    if (w.gloss) return w.gloss
    const m = w.meanings || []
    return Array.isArray(m) ? m.slice(0, 2).join('；') : ''
  }

  function paintSub() {
    const passed = Math.max(0, summary.total - summary.from_review)
    el.querySelector('#cq-sub').textContent =
      `共 ${summary.total} 词全部背完掌握 · 一遍过 ${passed} · 从错题攻克 ${summary.from_review}`
  }

  async function loadPage() {
    const mine = ++seq
    const q = search.value.trim()
    el.querySelector('#cq-words').innerHTML = '<p class="muted">加载中…</p>'
    try {
      const d = await api.conqueredPage({ page, limit: PAGE, q })
      if (mine !== seq) return
      items = (d && d.items) || []
      summary = (d && d.summary) || summary
      const total = Number(d && d.total) || 0
      const pages = Math.max(1, Math.ceil(total / PAGE))
      if (page > pages) {
        page = pages
        return loadPage()
      }
      paintSub()
      el.querySelector('#cq-words').innerHTML = items.length ? items.map((w) => `
        <div class="vl-word" data-word="${escapeHtml(w.word || '')}" title="点击查看完整内容">
          <b>${escapeHtml(w.word || '')}${w.wrong_count ? `<u class="cq-flag" title="从错题攻克（曾错 ${w.wrong_count} 次）">✓</u>` : ''}</b>
          <span class="vl-pos">${(w.pos || []).join(' ')}</span>
          <i>${escapeHtml(glossOf(w).slice(0, 36))}</i>
        </div>`).join('') : '<p class="muted">没有匹配的词条。</p>'
      const hasMore = !!(d && d.has_more)
      el.querySelector('#cq-pager').innerHTML = `
        <button class="chip" data-pg="prev" ${page <= 1 ? 'disabled' : ''}>‹ 上一页</button>
        <span class="vl-page-info">第 ${page} / ${pages} 页 · ${q ? '匹配' : '共'} ${total} 词</span>
        <button class="chip" data-pg="next" ${hasMore ? '' : 'disabled'}>下一页 ›</button>`
      el.querySelectorAll('[data-pg]').forEach((b) =>
        b.addEventListener('click', () => {
          if (b.disabled) return
          page += b.dataset.pg === 'next' ? 1 : -1
          if (page < 1) page = 1
          loadPage()
        }))
      el.querySelectorAll('.vl-word[data-word]').forEach((w) =>
        w.addEventListener('click', () => openWordDialog(w.dataset.word)))
    } catch (e) {
      if (mine !== seq) return
      el.querySelector('#cq-sub').innerHTML =
        `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
      el.querySelector('#cq-words').innerHTML = ''
      el.querySelector('#cq-pager').innerHTML = ''
    }
  }

  function openWordDialog(wordStr) {
    const w = items.find((x) => x.word === wordStr)
    if (!w) return
    const card = getWordCard('conquered')
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
        ${card.def ? `<div class="vl-dlg-sec"><b>释义</b><p>${escapeHtml(glossOf(w) || '（暂无）')}</p></div>` : ''}
        ${card.example && ex.en ? `<div class="vl-dlg-sec"><b>例句</b><p class="vl-dlg-en">${escapeHtml(ex.en)}</p>${ex.zh ? `<p class="vl-dlg-zh">${escapeHtml(ex.zh)}</p>` : ''}</div>` : ''}
        <div class="atlas-host"></div>
        <div class="vl-dlg-sec"><b>掌握</b><p>${escapeHtml(src)} · 累计答对 ${w.right ?? '—'} 次 / 答错 ${w.wrong ?? '—'} 次</p></div>
        <div class="chip-row"><button class="chip" id="cq-dlg-close">关闭</button></div>
      </div>`
    document.body.appendChild(dlg)
    fillAtlasHost(dlg.querySelector('.atlas-host'), w.word, w.atlas || null, { feature: 'conquered' })
    const close = () => dlg.remove()
    dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
    dlg.querySelector('#cq-dlg-close').addEventListener('click', close)
    dlg.querySelector('#cq-dlg-say').addEventListener('click', () => speakWord(w.word))
  }

  search.addEventListener('input', () => {
    if (searchTimer) clearTimeout(searchTimer)
    searchTimer = setTimeout(() => {
      page = 1
      loadPage()
    }, 250)
  })
  loadPage()
}

// 泛读馆阅读器 · 划词工具条（SelectionPopup）+ 查词浮层（DictPopover）。
// 直译自 FCEReadingLib client/src/components/reader/SelectionPopup.tsx。
// 两个浮层挂在 .lib-body（position:relative）内，坐标为该容器坐标系。

import { api } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { ICONS } from './icons.js'

/** TTS 发音：先 cancel 再 speak；en-GB 优先，rate 0.9，按 lang 匹配 voice */
export function speak(text) {
  if (!text || !('speechSynthesis' in window)) return
  try {
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = 'en-GB'
    u.rate = 0.9
    const voices = window.speechSynthesis.getVoices()
    const match = voices.find((v) => v.lang.replace('_', '-') === u.lang)
    if (match) u.voice = match
    window.speechSynthesis.speak(u)
  } catch {
    /* 浏览器不支持 TTS 时静默 */
  }
}

/**
 * 创建划词/查词浮层组，挂到容器（.lib-body）。
 * 返回 { showSelection, showDict, hideAll, destroy }。
 */
export function createLookupLayer(bodyEl) {
  let currentSel = null
  let dictTimer = null
  let dictSeq = 0
  let dictWord = ''

  // ---- 划词工具条（深色）----
  const popEl = document.createElement('div')
  popEl.className = 'lib-selpop'
  popEl.style.display = 'none'
  popEl.setAttribute('role', 'toolbar')
  popEl.innerHTML = `
    <span class="word-label"></span>
    <button type="button" data-act="sound" title="发音" aria-label="发音">${ICONS.sound(15)}</button>
    <button type="button" data-act="dict" title="查词" aria-label="查词">${ICONS.search(15)}</button>
    <button type="button" data-act="copy" title="复制" aria-label="复制">${ICONS.copy(15)}</button>
  `
  // 防止按下时清掉正文选区
  popEl.addEventListener('pointerdown', (e) => e.preventDefault())
  popEl.addEventListener('click', (e) => {
    e.stopPropagation()
    const btn = e.target.closest('button')
    if (!btn || !currentSel) return
    const act = btn.dataset.act
    if (act === 'sound') {
      speak(currentSel.word)
    } else if (act === 'dict') {
      showDict(currentSel)
    } else if (act === 'copy') {
      try {
        if (navigator.clipboard) navigator.clipboard.writeText(currentSel.word).catch(() => {})
      } catch {
        /* 剪贴板不可用则忽略 */
      }
      hideSelection()
    }
  })

  // ---- 查词浮层 ----
  const dictEl = document.createElement('div')
  dictEl.className = 'lib-dictpop'
  dictEl.style.display = 'none'
  dictEl.setAttribute('role', 'dialog')
  dictEl.setAttribute('aria-label', '查词结果')
  dictEl.innerHTML = `
    <div class="lib-dict-head">
      <span class="word"></span>
      <span class="ph"></span>
      <button type="button" class="sound" title="发音" aria-label="发音">${ICONS.sound(18)}</button>
      <button type="button" class="close" title="关闭" aria-label="关闭">✕</button>
    </div>
    <div class="lib-dict-source"></div>
    <div class="lib-dict-body"><div class="lib-dict-loading"><span class="lib-spinner"></span></div></div>
  `
  dictEl.addEventListener('click', (e) => e.stopPropagation())
  const dictBody = dictEl.querySelector('.lib-dict-body')
  const dictWordEl = dictEl.querySelector('.lib-dict-head .word')
  const dictPhEl = dictEl.querySelector('.lib-dict-head .ph')
  const dictSourceEl = dictEl.querySelector('.lib-dict-source')
  dictEl.querySelector('.lib-dict-head .sound').addEventListener('click', () => speak(dictWord))
  dictEl.querySelector('.lib-dict-head .close').addEventListener('click', hideDict)

  bodyEl.append(popEl, dictEl)

  // ---- 划词工具条 ----
  function showSelection(payload) {
    hideDict()
    currentSel = payload
    popEl.querySelector('.word-label').textContent = payload.word
    const bodyW = bodyEl.clientWidth || 800
    const HALF = 110
    const left = Math.min(Math.max(payload.x, HALF), Math.max(HALF, bodyW - HALF))
    popEl.style.left = `${left}px`
    popEl.style.top = `${Math.max(8, payload.y - 52)}px`
    popEl.style.display = 'flex'
  }

  function hideSelection() {
    currentSel = null
    popEl.style.display = 'none'
  }

  // ---- 查词浮层 ----
  function showDict(lookup) {
    hideSelection()
    dictSeq += 1
    const seq = dictSeq
    if (dictTimer) clearTimeout(dictTimer)
    dictWord = lookup.word
    dictWordEl.textContent = lookup.word
    dictPhEl.textContent = ''
    dictSourceEl.textContent = ''
    dictBody.innerHTML = '<div class="lib-dict-loading"><span class="lib-spinner"></span></div>'
    // 定位：clamp 在容器内
    const bodyW = bodyEl.clientWidth || 800
    const bodyH = bodyEl.clientHeight || 600
    const W = Math.min(340, bodyW - 32)
    const left = Math.min(Math.max(lookup.x - W / 2, 10), Math.max(10, bodyW - W - 10))
    const top = Math.min(lookup.y + 16, Math.max(10, bodyH - 200))
    dictEl.style.left = `${left}px`
    dictEl.style.top = `${Math.max(10, top)}px`
    dictEl.style.display = 'block'

    // 30ms debounce 后请求
    dictTimer = setTimeout(() => {
      api
        .libraryDict(lookup.word, lookup.context)
        .then((r) => {
          if (seq !== dictSeq) return
          renderEntry(r && r.entry)
        })
        .catch((e) => {
          if (seq !== dictSeq) return
          dictBody.innerHTML = `<div class="lib-dict-empty">${escapeHtml((e && e.message) || '查词失败')}</div>`
        })
    }, 30)
  }

  function hideDict() {
    dictSeq += 1
    if (dictTimer) clearTimeout(dictTimer)
    dictTimer = null
    dictEl.style.display = 'none'
  }

  function renderEntry(entry) {
    const e = entry || {}
    dictPhEl.textContent = e.phonetic || ''
    if (e.source === 'ecdict') {
      dictSourceEl.innerHTML = '<span class="src">内置词典 · ECDICT</span>'
    } else if (e.source === 'ai') {
      dictSourceEl.innerHTML = '<span class="ai">AI 释义</span>'
    } else {
      dictSourceEl.innerHTML = ''
    }

    const senses = Array.isArray(e.senses) ? e.senses : []
    if (e.source === 'not_found' || (!senses.length && !e.meaning)) {
      dictBody.innerHTML = '<div class="lib-dict-empty">词典未收录该词</div>'
      return
    }

    let html = ''
    if (senses.length) {
      // ECDICT / AI 结构化释义：pos 胶囊 + gloss + 斜体例句
      for (const s of senses) {
        const pos = s.pos ? `<span class="pos">${escapeHtml(s.pos)}</span>` : ''
        const defs = escapeHtml(s.gloss || '')
        const exs = (s.examples || [])
          .filter(Boolean)
          .map((ex) => `<div class="example">“${escapeHtml(ex)}”</div>`)
          .join('')
        html += `<div class="lib-dict-sense">${pos}<div class="def">${defs}</div>${exs}</div>`
      }
    } else {
      // AI 扁平条目：pos + meaning + example
      const pos = e.pos ? `<span class="pos">${escapeHtml(e.pos)}</span>` : ''
      const ex = e.example ? `<div class="example">“${escapeHtml(e.example)}”</div>` : ''
      html += `<div class="lib-dict-sense">${pos}<div class="def">${escapeHtml(e.meaning || '')}</div>${ex}</div>`
    }
    dictBody.innerHTML = html
  }

  function hideAll() {
    hideSelection()
    hideDict()
  }

  function destroy() {
    hideAll()
    popEl.remove()
    dictEl.remove()
  }

  return { showSelection, showDict, hideSelection, hideDict, hideAll, destroy }
}

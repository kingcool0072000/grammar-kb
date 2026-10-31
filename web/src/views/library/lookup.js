// 泛读馆阅读器 · 划词工具条（SelectionPopup）+ 查词浮层（DictPopover）。
// 直译自 FCEReadingLib client/src/components/reader/SelectionPopup.tsx。
// 浮层 position:fixed，锚在单词的视口矩形上（epub iframe 内的选区要加上
// frameElement 偏移）。靠近屏幕边缘时翻到词的另一侧，并限制高度可滚动。
// 阅读练习模块（reading.js）也复用本层（container 参数化）。

import { api } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { getWordCard } from '../../wordcard.js'
import { ICONS } from './icons.js'

// TTS 发音的唯一实现在 ../../tts.js（#/ttsconf 配置默认音色/语速）。
// import + 再 export：纯 re-export（export {x} from）不把名字引入本模块
// 作用域，本文件内部（划词条/查词浮层的 🔊）也要调 speak，曾因此
// ReferenceError: speak is not defined。
import { speak } from '../../tts.js'
import { fillAtlasHost, loadAtlas, speakWord } from '../../atlas.js'
export { speak }

/**
 * 创建划词/查词浮层组，挂到容器（需 position:relative）。
 * opts.onLookup(word)：查词漏斗（showDict 入口）触发；
 * opts.onSpeak(word)：工具条/浮层发音钮触发。
 * 不传回调时行为完全不变（阅读练习 reading.js 复用本层不受影响）。
 * 返回 { showSelection, showDict, hideAll, destroy }。
 */
/** 选区 / 锚点 mark 的视口矩形。iframe 里的坐标要加上 frame 在父页面的位置。 */
function viewportBox(nodeOrRange) {
  if (!nodeOrRange || !nodeOrRange.getBoundingClientRect) return null
  const r = nodeOrRange.getBoundingClientRect()
  if (!r || (!r.width && !r.height)) return null
  let left = r.left
  let top = r.top
  const doc = nodeOrRange.ownerDocument
    || (nodeOrRange.startContainer && nodeOrRange.startContainer.ownerDocument)
  const frame = doc && doc.defaultView && doc.defaultView.frameElement
  if (frame && frame.getBoundingClientRect) {
    const f = frame.getBoundingClientRect()
    left += f.left
    top += f.top
  }
  return { left, top, width: r.width, height: r.height }
}

function boxOnScreen(box) {
  if (!box) return false
  const vw = window.innerWidth || 0
  const vh = window.innerHeight || 0
  return box.top + box.height > 0 && box.top < vh
    && box.left + box.width > 0 && box.left < vw
}

/**
 * 把浮层放在词旁边。preferAbove：工具条默认在词上方。
 * limitHeight：查词卡过高时翻面，并限制 max-height 让关闭钮留在屏内。
 */
function placeNear(el, box, { preferAbove = false, limitHeight = false } = {}) {
  if (!el || !box) return
  const margin = 8
  const gap = 8
  const vw = window.innerWidth || 320
  const vh = window.innerHeight || 640
  el.style.position = 'fixed'
  el.style.right = 'auto'
  el.style.bottom = 'auto'
  el.style.transform = 'none'
  if (!limitHeight) el.style.maxHeight = ''
  const prev = el.style.visibility
  el.style.visibility = 'hidden'
  el.style.display = el.classList.contains('lib-selpop') ? 'flex' : 'block'
  const popW = el.offsetWidth || Math.min(340, vw - margin * 2)
  const popH = el.offsetHeight || 48
  let left = box.left + box.width / 2 - popW / 2
  left = Math.max(margin, Math.min(left, Math.max(margin, vw - popW - margin)))
  const wordBottom = box.top + box.height
  const spaceAbove = box.top - gap - margin
  const spaceBelow = vh - wordBottom - gap - margin
  let above = preferAbove
  if (limitHeight) {
    above = spaceBelow < 140 && spaceAbove > spaceBelow
  } else if (spaceAbove < popH + gap) {
    above = false
  }
  let top = above ? box.top - gap - popH : wordBottom + gap
  if (limitHeight) {
    const room = above ? spaceAbove : Math.max(spaceBelow, 96)
    const maxH = Math.max(96, Math.min(room, Math.round(vh * 0.62)))
    el.style.maxHeight = `${Math.round(maxH)}px`
    const h = Math.min(popH, maxH)
    top = above ? box.top - gap - h : wordBottom + gap
  }
  if (top < margin) top = margin
  if (top > vh - margin - 48) top = Math.max(margin, vh - margin - 48)
  el.style.left = `${Math.round(left)}px`
  el.style.top = `${Math.round(top)}px`
  el.style.visibility = prev || ''
}

export function createLookupLayer(bodyEl, opts = {}) {
  const onLookupCb = typeof opts.onLookup === 'function' ? opts.onLookup : null
  const onSelectCb = typeof opts.onSelect === 'function' ? opts.onSelect : null
  const onSpeakCb = typeof opts.onSpeak === 'function' ? opts.onSpeak : null
  const feature = opts.feature || 'library'
  // 双击单词：不弹工具条，直接开查词浮层并自动发音一次。
  // 拖选多词仍走工具条（发音/查词两钮），泛读馆与阅读练习行为一致。
  // 实现挂 dblclick（主文档 + iframe 文档）：selectionchange 防抖后
  // showSelection 只触发一次，靠「重复选择」检测双击不可行。
  const directLookupOnDouble = opts.directLookupOnDouble === true
  let suppressSelectionUntil = 0
  let currentSel = null
  let dictTimer = null
  let dictSeq = 0
  let dictWord = ''

  // 选区完成 → 立即塌缩选区：Chrome/安卓 WebView 的系统文本菜单
  // （复制/分享/全选…）锚定在活动选区上，塌缩后不会弹出，只剩我们的工具条。
  // 字词已提取进 currentSel，后续发音/查词不再依赖选区本身。
  let selectionWindow = opts.selectionWindow || null
  function collapseSelection() {
    try {
      const w = selectionWindow || window
      const s = w.getSelection && w.getSelection()
      if (s && s.rangeCount) s.removeAllRanges()
    } catch { /* 跨 iframe 失败可忽略 */ }
  }
  /** 泛读馆阅读器：把 epub iframe 的 window 传进来，选区塌缩才能作用到正文选区。 */
  function setSelectionWindow(w) {
    if (w) {
      selectionWindow = w
      // iframe 内双击不冒泡到主文档：把 dblclick 也挂进 iframe 文档
      try {
        w.document.addEventListener('dblclick', dblclickHandler)
        iframeCleanups.push(() => {
          try { w.document.removeEventListener('dblclick', dblclickHandler) } catch { /* ignore */ }
        })
      } catch { /* 跨域或已卸载，忽略 */ }
    }
  }
  const iframeCleanups = []

  /** 双击直达查词：读当前选区（双击已选中单词）→ 塌缩 → 查词浮层 + 发音一次。 */
  function dblclickHandler(e) {
    if (!directLookupOnDouble) return
    // 浮层自身内的双击不处理
    if (e && (popEl.contains(e.target) || dictEl.contains(e.target))) return
    try {
      const w = selectionWindow || window
      const sel = w.getSelection && w.getSelection()
      const text = (sel && sel.toString().trim()) || ''
      if (!sel || !sel.rangeCount || !text || text.length > 80) return
      const word = text.replace(/[^A-Za-z'’-]/g, '')
      if (!word) return
      const range = sel.getRangeAt(0).cloneRange()
      suppressSelectionUntil = Date.now() + 900 // 压掉随后的 selectionchange 工具条
      collapseSelection()
      showDict({
        word,
        context: text.replace(/\s+/g, ' ').slice(0, 200),
        range,
      })
      speakWord(word)
      if (onSpeakCb) onSpeakCb(word)
    } catch { /* ignore */ }
  }
  if (directLookupOnDouble) {
    bodyEl.addEventListener('dblclick', dblclickHandler)
  }

  // ---- 划词工具条（深色；仅发音 + 查词）----
  const popEl = document.createElement('div')
  popEl.className = 'lib-selpop'
  popEl.style.display = 'none'
  popEl.setAttribute('role', 'toolbar')
  popEl.innerHTML = `
    <span class="word-label"></span>
    <button type="button" data-act="sound" title="发音" aria-label="发音">${ICONS.sound(15)}</button>
    <button type="button" data-act="dict" title="查词" aria-label="查词">${ICONS.search(15)}</button>
  `
  // 防止按下时清掉正文选区
  popEl.addEventListener('pointerdown', (e) => e.preventDefault())
  popEl.addEventListener('click', (e) => {
    e.stopPropagation()
    const btn = e.target.closest('button')
    if (!btn || !currentSel) return
    const act = btn.dataset.act
    if (act === 'sound') {
      speakWord(currentSel.word)
      if (onSpeakCb) onSpeakCb(currentSel.word)
    } else if (act === 'dict') {
      showDict(currentSel)
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
    dictEl.querySelector('.lib-dict-head .sound').addEventListener('click', () => {
    speakWord(dictWord)
    if (onSpeakCb) onSpeakCb(dictWord)
  })
  dictEl.querySelector('.lib-dict-head .close').addEventListener('click', hideDict)

  bodyEl.append(popEl, dictEl)

  // ---- 划词工具条 ----
  // 选区塌缩后 ::selection 高亮会消失（走查 M1）：payload.range 存在时
  // 给选中文字包一层装饰性 <mark>，工具条/浮层关闭时撤除，保住词位反馈
  function anchorMark(payload) {
    clearAnchorMark()
    try {
      const range = payload && payload.range
      if (!range || !range.surroundContents) return
      const doc = range.startContainer && range.startContainer.ownerDocument
      if (!doc) return
      const mark = doc.createElement('mark')
      mark.setAttribute('data-lib-anchor', '')
      range.surroundContents(mark)
      anchorEl = mark
    } catch { /* 跨节点包裹失败则放弃锚点 */ }
  }
  let anchorEl = null
  let anchorRect = null
  function clearAnchorMark() {
    if (!anchorEl) return
    try {
      const parent = anchorEl.parentNode
      while (anchorEl.firstChild) parent.insertBefore(anchorEl.firstChild, anchorEl)
      parent.removeChild(anchorEl)
      parent.normalize()
    } catch { /* ignore */ }
    anchorEl = null
  }
  function currentBox() {
    const marked = anchorEl && anchorEl.isConnected ? viewportBox(anchorEl) : null
    return marked || anchorRect
  }
  function rememberAnchor(payload) {
    const pre = (payload && payload.range && viewportBox(payload.range))
      || (payload && payload.rect)
      || null
    anchorMark(payload)
    anchorRect = (anchorEl && viewportBox(anchorEl)) || pre
  }
  function relayoutPop() {
    if (popEl.style.display === 'none') return
    const box = currentBox()
    if (!boxOnScreen(box)) {
      hideSelection()
      return
    }
    placeNear(popEl, box, { preferAbove: true })
  }
  function relayoutDict() {
    if (dictEl.style.display === 'none') return
    const box = currentBox()
    if (!boxOnScreen(box)) {
      hideDict()
      return
    }
    placeNear(dictEl, box, { limitHeight: true })
  }
  function onReflow() {
    relayoutPop()
    relayoutDict()
  }
  document.addEventListener('scroll', onReflow, true)
  window.addEventListener('resize', onReflow)
  dictEl.addEventListener('load', (e) => {
    if (e.target && e.target.tagName === 'IMG') relayoutDict()
  }, true)

  function showSelection(payload) {
    // 换词时关掉上一次查词。不用 hideDict：那时工具条可能还开着，锚点会被留下。
    dictSeq += 1
    if (dictTimer) clearTimeout(dictTimer)
    dictTimer = null
    dictEl.style.display = 'none'
    clearAnchorMark()
    anchorRect = null
    // 双击直达查词后 900ms 内：selectionchange 的余波不再弹工具条
    if (directLookupOnDouble && Date.now() < suppressSelectionUntil) return
    currentSel = payload
    if (onSelectCb && payload && payload.word) onSelectCb(payload.word)
    collapseSelection()
    rememberAnchor(payload)
    popEl.querySelector('.word-label').textContent = payload.word
    const box = currentBox()
    if (!box) return
    placeNear(popEl, box, { preferAbove: true })
  }

  function hideSelection() {
    currentSel = null
    popEl.style.display = 'none'
    if (dictEl.style.display === 'none') {
      clearAnchorMark()
      anchorRect = null
    }
  }

  // ---- 查词浮层 ----
  function showDict(lookup) {
    const payload = lookup || currentSel
    popEl.style.display = 'none'
    currentSel = null
    if (!anchorEl) rememberAnchor(payload)
    else anchorRect = viewportBox(anchorEl) || anchorRect
    if (onLookupCb) onLookupCb(payload && payload.word)
    dictSeq += 1
    const seq = dictSeq
    if (dictTimer) clearTimeout(dictTimer)
    dictWord = (payload && payload.word) || ''
    const card = getWordCard(feature)
    if (card.image || card.def || card.example) loadAtlas(dictWord)
    dictWordEl.textContent = dictWord
    dictPhEl.textContent = ''
    dictSourceEl.textContent = ''
    dictBody.innerHTML = '<div class="lib-dict-loading"><span class="lib-spinner"></span></div>'
    const box = currentBox()
    if (box) placeNear(dictEl, box, { limitHeight: true })
    else dictEl.style.display = 'block'

    // 30ms debounce 后请求
    dictTimer = setTimeout(() => {
      api
        .libraryDict(dictWord, payload && payload.context)
        .then((r) => {
          if (seq !== dictSeq) return
          renderEntry(r && r.entry)
        })
        .catch((e) => {
          if (seq !== dictSeq) return
          dictBody.innerHTML = `<div class="lib-dict-empty">${escapeHtml((e && e.message) || '查词失败')}</div>`
          relayoutDict()
        })
    }, 30)
  }

  function hideDict() {
    dictSeq += 1
    if (dictTimer) clearTimeout(dictTimer)
    dictTimer = null
    dictEl.style.display = 'none'
    if (popEl.style.display === 'none') {
      clearAnchorMark()
      anchorRect = null
    }
  }

  function renderEntry(entry) {
    const e = entry || {}
    const card = getWordCard(feature)
    dictPhEl.textContent = e.phonetic || ''
    if (e.source === 'ecdict') {
      dictSourceEl.innerHTML = '<span class="src">内置词典 · ECDICT</span>'
    } else if (e.source === 'ai') {
      dictSourceEl.innerHTML = '<span class="ai">AI 释义</span>'
    } else {
      dictSourceEl.innerHTML = ''
    }

    const senses = Array.isArray(e.senses) ? e.senses : []
    const showAtlas = card.image || card.def || card.example
    if (e.source === 'not_found' || (!senses.length && !e.meaning)) {
      dictBody.innerHTML = showAtlas
        ? '<div class="atlas-host"></div><div class="lib-dict-empty">词典未收录该词</div>'
        : '<div class="lib-dict-empty">词典未收录该词</div>'
      if (showAtlas) {
        fillAtlasHost(dictBody.querySelector('.atlas-host'), dictWord, e.atlas || null, { feature })
        loadAtlas(dictWord).then((full) => {
          const empty = dictBody.querySelector('.lib-dict-empty')
          if (full && empty) empty.remove()
          relayoutDict()
        })
      }
      relayoutDict()
      return
    }

    let html = showAtlas ? '<div class="atlas-host"></div>' : ''
    if (senses.length) {
      for (const s of senses) {
        const pos = card.def && s.pos ? `<span class="pos">${escapeHtml(s.pos)}</span>` : ''
        const defs = card.def ? `<div class="def">${escapeHtml(s.gloss || '')}</div>` : ''
        const exs = card.example
          ? (s.examples || []).filter(Boolean)
            .map((ex) => `<div class="example">“${escapeHtml(ex)}”</div>`).join('')
          : ''
        if (pos || defs || exs) html += `<div class="lib-dict-sense">${pos}${defs}${exs}</div>`
      }
    } else if (card.def || (card.example && e.example)) {
      const pos = card.def && e.pos ? `<span class="pos">${escapeHtml(e.pos)}</span>` : ''
      const defs = card.def ? `<div class="def">${escapeHtml(e.meaning || '')}</div>` : ''
      const ex = card.example && e.example ? `<div class="example">“${escapeHtml(e.example)}”</div>` : ''
      html += `<div class="lib-dict-sense">${pos}${defs}${ex}</div>`
    }
    dictBody.innerHTML = html || '<div class="lib-dict-empty">已按设置隐藏释义、插图和例句</div>'
    if (showAtlas) {
      fillAtlasHost(dictBody.querySelector('.atlas-host'), dictWord, e.atlas || null, { feature })
    }
    relayoutDict()
  }

  function hideAll() {
    // 双击直达查词后的保护窗内不响应「选区塌缩→hideAll」余波
    //（否则刚开的查词浮层会被 250ms 后的 selectionchange 处理器关掉）
    if (directLookupOnDouble && Date.now() < suppressSelectionUntil) return
    hideSelection()
    hideDict()
  }

  let dead = false
  // 阅读练习换页时不会显式 destroy：正文节点被卸掉后自己摘掉 document 监听。
  const detachObserver = new MutationObserver(() => {
    if (!bodyEl.isConnected) destroy()
  })
  detachObserver.observe(document.body, { childList: true, subtree: true })

  function destroy() {
    if (dead) return
    dead = true
    hideAll()
    document.removeEventListener('scroll', onReflow, true)
    window.removeEventListener('resize', onReflow)
    detachObserver.disconnect()
    if (directLookupOnDouble) bodyEl.removeEventListener('dblclick', dblclickHandler)
    iframeCleanups.forEach((fn) => fn())
    popEl.remove()
    dictEl.remove()
  }

  return { showSelection, showDict, hideSelection, hideDict, hideAll, destroy, collapseSelection, setSelectionWindow }
}

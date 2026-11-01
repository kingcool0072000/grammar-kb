import { escapeHtml } from '../../render.js'
import { POS_CN, FORM_CN } from '../../fmt.js'
import {
  atlasFigure, bindAtlas, fillAtlasHost, getAccent, phoneticHtml,
  playPronunciation, stopAtlasAudio, wordAudioUrl,
} from '../../atlas.js'
import { getWordCard } from '../../wordcard.js'
import { ttsOn, setTtsOn } from './shared.js'

// 背单词翻面卡片（自 views/recite.js 原样搬移）：卡片 HTML 拼装（三种题型
// 各自的正面版式 + 背面词卡三档细节渲染）、flip 翻面、翻面揭晓答案时
// 自动 TTS 发音一次、三键自评（不认识/模糊/认识）与键盘翻面。

function entryPos(e) {
  return (e.pos || []).filter((p) => POS_CN[p]).slice(0, 3)
}

// ---- 单张卡片 -------------------------------------------------------------- //

export function renderCard(overlay, { q, idx, total, mode, onAnswer, onQuit }) {
  stopAtlasAudio()
  const e = q.entry
  const posTags = entryPos(e).map((p) => `<span class="vocab-pos">${POS_CN[p]}</span>`).join('')
  const specials = (e.special_spellings || [])
    .map((s) => `<span class="rc-special">${escapeHtml(s)}</span>`)
    .join('')
  const example = (e.examples || [])[0] || (e.example && e.example.en ? e.example : null)
  const card = getWordCard('recite')
  // 英译中正面已经露出单词，插画和录音按钮可以一起放。
  // 中译英 / 挖空正面不放插画和音标，避免把答案画出来。
  // 题干释义是题目本身，不跟「词卡释义」开关走；插画/例句/词光释义才受开关控制。
  const promptHtml =
    q.type === 'en2zh'
      ? `${card.image ? atlasFigure(e.atlas) : ''}
         <div class="rc-word">${escapeHtml(e.display || e.word)}</div>
         <div class="rc-phonetic">${phoneticHtml(e)} ${posTags}</div>`
      : q.type === 'cloze'
        ? `<div class="rc-word rc-cloze">${escapeHtml(q.masked)}</div>
           <div class="rc-phonetic">${posTags} ${escapeHtml(q.meaning)}</div>`
        : `<div class="rc-meaning">${escapeHtml(q.meaning)}</div>
           <div class="rc-phonetic">${posTags}${e.phonetic ? ' 音标稍后揭晓' : ''}</div>`

  overlay.innerHTML = `
    <div class="rc-top">
      <button class="rc-quit" title="退出">✕ 退出</button>
      <div class="rc-progress">${idx} / ${total}</div>
      <button class="rc-tts" title="${ttsOn() ? '翻面自动发音中，点击静音' : '已静音，点击开启自动发音'}">${ttsOn() ? '🔊' : '🔇'}</button>
    </div>
    <div class="rc-card" data-state="front">
      <div class="rc-prompt">${promptHtml}</div>
      <div class="rc-answer-area"></div>
      <div class="rc-extra"></div>
    </div>
  `

  const $card = overlay.querySelector('.rc-card')
  const $area = overlay.querySelector('.rc-answer-area')
  const $extra = overlay.querySelector('.rc-extra')

  bindAtlas($card)
  overlay.querySelector('.rc-quit').addEventListener('click', onQuit)
  const $tts = overlay.querySelector('.rc-tts')
  $tts.addEventListener('click', () => {
    const on = !ttsOn()
    setTtsOn(on)
    $tts.textContent = on ? '🔊' : '🔇'
    $tts.title = on ? '翻面自动发音中，点击静音' : '已静音，点击开启自动发音'
    if (on && $card.dataset.state === 'back') {
      playPronunciation(wordAudioUrl(e.atlas, getAccent()), e.display || e.word)
    }
  })

  // 释义区：gloss 逗号长串拆成前 6 条分行（每行一条义项，可读）；
  // meanings 是语料例句中文，不混进释义。
  const glossLines = () => {
    const g = e.gloss || ''
    if (!g) return []
    return g.split(/[,，/]+/).map((s) => s.trim()).filter(Boolean).slice(0, 6)
  }

  const revealDetail = () => {
    const gl = glossLines()
    const forms = Object.entries(e.forms || {}).filter(([k]) => FORM_CN[k])
    const ex = example
    const backMedia = q.type === 'en2zh' || !card.image
      ? ''
      : `${atlasFigure(e.atlas)}${e.atlas ? `<div class="rc-phonetic">${phoneticHtml(e)}</div>` : ''}`
    const glossBlock = card.def && q.type === 'en2zh' && gl.length
      ? `<div class="rc-gloss">${gl.map((l) => `<p>${escapeHtml(l)}</p>`).join('')}</div>`
      : ''
    const exBlock = card.example && ex
      ? `<div class="rc-example">${escapeHtml(ex.en)}<br/><span class="muted">${escapeHtml(ex.zh || '')}</span></div>`
      : ''
    $extra.innerHTML = `
      <div class="rc-detail">
        ${backMedia}
        ${glossBlock}
        ${specials ? `<div class="rc-special-row">${specials}</div>` : ''}
        ${forms.length ? `<div class="rc-forms">${forms.map(
          ([k, v]) => `<span class="rc-form"><i>${FORM_CN[k]}</i>${escapeHtml(v)}</span>`,
        ).join('')}</div>` : ''}
        ${exBlock}
        <div class="atlas-host"></div>
      </div>
    `
    bindAtlas($extra)
    fillAtlasHost($extra.querySelector('.atlas-host'), e.word, e.atlas, {
      feature: 'recite', image: false,
    })
  }

  {
    // 统一翻面自评：正面看提示（释义/词/挖空），翻面看答案 + 自动发音 + 三键自评
    $card.dataset.state = 'front'
    $area.innerHTML = '<div class="rc-hint">点击卡片或按空格键翻面</div>'
    const flip = () => {
      if ($card.dataset.state !== 'front') return
      $card.dataset.state = 'back'
      revealDetail()
      // 翻面揭晓答案：自动 TTS 发音一次（🔇 可关）
      if (ttsOn()) playPronunciation(wordAudioUrl(e.atlas, getAccent()), e.display || e.word)
      $area.innerHTML = `
        ${q.type === 'en2zh'
          ? `<div class="rc-answer-gloss">${glossLines().slice(0, 3).map((l) => escapeHtml(l)).join('；') || '—'}</div>`
          : `<div class="rc-word">${escapeHtml(q.answer)}${q.type === 'cloze' ? `<span class="rc-cloze-key">${escapeHtml(q.masked)} → ${escapeHtml(q.answer)}</span>` : ''}</div>`}
        <div class="rc-self">
          <button class="rc-btn wrong" data-r="0">不认识</button>
          <button class="rc-btn fuzzy" data-r="1">模糊</button>
          <button class="rc-btn right" data-r="2">认识</button>
        </div>`
      $area.querySelectorAll('.rc-btn').forEach((b) =>
        b.addEventListener('click', () => onAnswer(b.dataset.r === '2')),
      )
    }
    $card.addEventListener('click', flip)
    const onKey = (ev) => {
      if ($card.dataset.state !== 'front') {
        if (ev.key === '1') onAnswer(false)
        if (ev.key === '3') onAnswer(true)
        return
      }
      if (ev.key === ' ' || ev.key === 'Enter') {
        ev.preventDefault()
        flip()
      }
    }
    document.addEventListener('keydown', onKey)
    // 会话结束后移除监听：卡片被替换前挂到 overlay 生命周期
    overlay.__removeKey = () => document.removeEventListener('keydown', onKey)
  }
}

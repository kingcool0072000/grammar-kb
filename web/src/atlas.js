// 词光 Word Atlas：插画、英/美录音、例句朗读。
// 媒体 URL 走 /api/atlas-media（部署期由后端剥掉 /api）。文件 404 或播放失败时
// 退回 tts.js 的浏览器语音。署名见 aboutHtml()，登录页和设置中心也会展示。
import { api } from './api.js'
import { escapeHtml, escAttr } from './render.js'
import { speak } from './tts.js'

const ACCENT_KEY = 'gkb-atlas-accent'
export const MEDIA = { img: 1, uk: 2, us: 4, ex: 8, ex2: 16 }

const audioHint = new Map()
const fullResolved = new Map()
const fullPending = new Map()

let current = null
let token = 0

export function getAccent() {
  try {
    return localStorage.getItem(ACCENT_KEY) === 'us' ? 'us' : 'uk'
  } catch {
    return 'uk'
  }
}

export function setAccent(accent) {
  try {
    localStorage.setItem(ACCENT_KEY, accent === 'us' ? 'us' : 'uk')
  } catch { /* 隐私模式忽略 */ }
}

function norm(word) {
  return String(word || '').trim().toLowerCase()
}

export function hasBit(atlas, bit) {
  return !!(atlas && (Number(atlas.media) & bit))
}

export function mediaUrl(id, kind) {
  const enc = encodeURIComponent(id)
  if (kind === 'img' || kind === 'thumb') return `/api/atlas-media/${kind}/${enc}.webp`
  return `/api/atlas-media/audio/${kind}/${enc}.mp3`
}

export function wordAudioUrl(atlas, accent) {
  if (!atlas || !atlas.id) return null
  const prefer = accent === 'us' ? 'us' : 'uk'
  const other = prefer === 'us' ? 'uk' : 'us'
  if (hasBit(atlas, MEDIA[prefer])) return mediaUrl(atlas.id, prefer)
  if (hasBit(atlas, MEDIA[other])) return mediaUrl(atlas.id, other)
  return null
}

/** 背单词列表里的精简词条，让发音不必再等一次请求。 */
export function rememberAtlas(word, compact) {
  const key = norm(word)
  if (key && compact && compact.id) audioHint.set(key, compact)
}

export function peekAudio(word) {
  const key = norm(word)
  if (!key) return undefined
  if (fullResolved.has(key)) return fullResolved.get(key)
  if (audioHint.has(key)) return audioHint.get(key)
  return undefined
}

export function loadAtlas(word) {
  const key = norm(word)
  if (!key) return Promise.resolve(null)
  if (fullResolved.has(key)) return Promise.resolve(fullResolved.get(key))
  if (!fullPending.has(key)) {
    const pending = api.atlasEntry(key).then((entry) => {
      fullResolved.set(key, entry || null)
      return entry || null
    }).catch(() => {
      fullResolved.set(key, null)
      return null
    })
    fullPending.set(key, pending)
  }
  return fullPending.get(key)
}

export function stopAtlasAudio() {
  token += 1
  if (current) {
    try {
      current.pause()
    } catch { /* 已经停了 */ }
    current = null
  }
  try {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  } catch { /* 无 TTS */ }
}

/** 有录音就播；没有、404 或自动播放被拦时退回浏览器 TTS。 */
export function playPronunciation(url, text) {
  stopAtlasAudio()
  const mine = token
  const say = String(text || '').trim()
  if (!say && !url) return
  let fell = false
  const fallback = () => {
    if (fell || mine !== token) return
    fell = true
    current = null
    if (say) speak(say)
  }
  if (!url) {
    fallback()
    return
  }
  const audio = new Audio(url)
  current = audio
  audio.preload = 'none'
  audio.onerror = fallback
  audio.play().catch(fallback)
}

export function playAtlas(atlas, kind, text) {
  const accent = kind === 'us' ? 'us' : getAccent()
  let url = null
  if (atlas && atlas.id && MEDIA[kind] && hasBit(atlas, MEDIA[kind])) {
    url = mediaUrl(atlas.id, kind)
  } else if (!kind || kind === 'uk' || kind === 'us') {
    url = wordAudioUrl(atlas, kind === 'us' ? 'us' : kind === 'uk' ? 'uk' : accent)
  }
  playPronunciation(url, text)
}

/** 先用已缓存的录音；没有缓存就查 /atlas，再不行用 TTS。 */
export function speakWord(word, text) {
  const say = String(text || word || '').trim()
  if (!say) return
  const known = peekAudio(word)
  if (known !== undefined) {
    playPronunciation(known && wordAudioUrl(known, getAccent()), say)
    return
  }
  loadAtlas(word).then((entry) => {
    playPronunciation(entry && wordAudioUrl(entry, getAccent()), say)
  })
}

export function atlasFigure(atlas, { size = 'thumb' } = {}) {
  if (!atlas || !atlas.id || !hasBit(atlas, MEDIA.img)) return ''
  const kind = size === 'full' ? 'img' : 'thumb'
  return `
    <figure class="atlas-illo">
      <img src="${escAttr(mediaUrl(atlas.id, kind))}" alt="" loading="lazy" decoding="async"
           data-atlas-img data-atlas-kind="${kind}" data-atlas-id="${escAttr(atlas.id)}" />
      <figcaption class="atlas-credit">插画：词光 Word Atlas · CC BY 4.0</figcaption>
    </figure>`
}

function splitCol(raw) {
  const text = String(raw || '')
  const mark = text.includes('｜') ? '｜' : text.includes('|') ? '|' : ''
  if (!mark) return { en: text, zh: '' }
  const i = text.indexOf(mark)
  return { en: text.slice(0, i).trim(), zh: text.slice(i + mark.length).trim() }
}

function playButton(atlas, kind, label, text) {
  if (!atlas || !atlas.id || !hasBit(atlas, MEDIA[kind])) return ''
  return `<button type="button" class="atlas-play" data-atlas-play="${kind}"
    data-atlas-id="${escAttr(atlas.id)}" data-atlas-media="${Number(atlas.media) || 0}"
    data-atlas-text="${escAttr(text)}">${escapeHtml(label)}</button>`
}

export function phoneticHtml(entry) {
  const atlas = entry && entry.atlas
  const word = (entry && (entry.display || entry.word)) || ''
  if (!atlas || !atlas.id || (!atlas.uk && !atlas.us)) {
    return escapeHtml((entry && entry.phonetic) || '')
  }
  const accent = getAccent()
  const button = (kind, ipa, label) => {
    if (!ipa) return ''
    const on = accent === kind ? ' on' : ''
    return `<button type="button" class="atlas-accent${on}" data-atlas-accent="${kind}"
      data-atlas-id="${escAttr(atlas.id)}" data-atlas-media="${Number(atlas.media) || 0}"
      data-atlas-text="${escAttr(word)}">${label} ${escapeHtml(ipa)}</button>`
  }
  return `<span class="atlas-accents">${button('uk', atlas.uk, '英')}${button('us', atlas.us, '美')}</span>`
}

export function atlasDetailHtml(entry) {
  if (!entry || !entry.id) return ''
  const senses = Array.isArray(entry.senses) ? entry.senses : []
  const examples = Array.isArray(entry.examples) ? entry.examples : []
  const cols = Array.isArray(entry.collocations) ? entry.collocations.slice(0, 4) : []
  const family = Array.isArray(entry.family) ? entry.family.slice(0, 6) : []
  const syn = Array.isArray(entry.synonyms) ? entry.synonyms.slice(0, 4) : []
  const ant = Array.isArray(entry.antonyms) ? entry.antonyms.slice(0, 4) : []
  const head = [
    entry.cefr ? `<span class="atlas-cefr">${escapeHtml(entry.cefr)}</span>` : '',
    entry.topic_zh || entry.topic
      ? `<span class="atlas-topic">${escapeHtml(entry.topic_zh || entry.topic)}</span>`
      : '',
  ].filter(Boolean).join('')
  const senseHtml = senses.map((sense) => {
    const pos = sense.pos ? `<i>${escapeHtml(sense.pos)}</i> ` : ''
    const zh = escapeHtml(sense.zh || '')
    const en = sense.en ? `<span class="muted">${escapeHtml(sense.en)}</span>` : ''
    return `<p>${pos}${zh}${en ? `<br/>${en}` : ''}</p>`
  }).join('')
  const exampleHtml = examples.map((ex, i) => {
    const kind = i === 0 ? 'ex' : 'ex2'
    const en = ex.en || ''
    const play = playButton(entry, kind, '朗读', en)
    return `<p class="atlas-ex">${play} ${escapeHtml(en)}${
      ex.zh ? `<br/><span class="muted">${escapeHtml(ex.zh)}</span>` : ''
    }</p>`
  }).join('')
  const colHtml = cols.map((item) => {
    const pair = splitCol(item)
    return `<li>${escapeHtml(pair.en)}${pair.zh ? ` <span class="muted">${escapeHtml(pair.zh)}</span>` : ''}</li>`
  }).join('')
  const see = entry.queried && entry.alias_of && entry.queried !== entry.word
    ? `<p class="atlas-see">${escapeHtml(entry.queried)} 见 ${escapeHtml(entry.word || entry.alias_of)}</p>`
    : ''
  return `
    <div class="atlas-detail">
      ${see}
      ${head ? `<p class="atlas-meta">${head}</p>` : ''}
      ${entry.tip ? `<p class="atlas-tip">${escapeHtml(entry.tip)}</p>` : ''}
      ${entry.note ? `<p class="atlas-note">${escapeHtml(entry.note)}</p>` : ''}
      ${senseHtml ? `<h4>词光释义</h4>${senseHtml}` : ''}
      ${exampleHtml ? `<h4>词光例句</h4>${exampleHtml}` : ''}
      ${colHtml ? `<h4>搭配</h4><ul class="atlas-col">${colHtml}</ul>` : ''}
      ${family.length ? `<h4>词族</h4><p>${family.map((item) => escapeHtml(item)).join(' · ')}</p>` : ''}
      ${syn.length ? `<h4>近义</h4><p>${syn.map((item) => escapeHtml(item)).join(' · ')}</p>` : ''}
      ${ant.length ? `<h4>反义</h4><p>${ant.map((item) => escapeHtml(item)).join(' · ')}</p>` : ''}
    </div>`
}

/** 插画立刻用精简字段画；释义/例句等完整字段再请求一次。 */
export function fillAtlasHost(host, word, compact, { image = true, size = 'thumb' } = {}) {
  if (!host) return
  const known = compact || peekAudio(word)
  host.innerHTML = image ? atlasFigure(known, { size }) : ''
  host.insertAdjacentHTML('beforeend', '<div class="atlas-more"></div>')
  bindAtlas(host)
  const more = host.querySelector('.atlas-more')
  loadAtlas(word).then((full) => {
    if (!more || !more.isConnected) return
    if (!full) {
      if (!known) host.innerHTML = ''
      return
    }
    if (image && !host.querySelector('.atlas-illo')) {
      more.insertAdjacentHTML('beforebegin', atlasFigure(full, { size }))
    }
    more.innerHTML = atlasDetailHtml(full)
    bindAtlas(host)
  })
}

export function bindAtlas(root) {
  if (!root) return
  root.querySelectorAll('[data-atlas-img]').forEach((img) => {
    if (img.dataset.bound) return
    img.dataset.bound = '1'
    img.addEventListener('error', () => {
      if (img.dataset.atlasKind === 'thumb' && !img.dataset.retried && img.dataset.atlasId) {
        img.dataset.retried = '1'
        img.dataset.atlasKind = 'img'
        img.src = mediaUrl(img.dataset.atlasId, 'img')
        return
      }
      const fig = img.closest('.atlas-illo')
      if (fig) fig.remove()
    })
  })
  root.querySelectorAll('[data-atlas-play], [data-atlas-accent]').forEach((btn) => {
    if (btn.dataset.bound) return
    btn.dataset.bound = '1'
    btn.addEventListener('click', (event) => {
      event.stopPropagation()
      event.preventDefault()
      const accent = btn.dataset.atlasAccent
      if (accent) {
        setAccent(accent)
        const scope = btn.closest('.atlas-accents') || btn.parentElement
        if (scope) {
          scope.querySelectorAll('[data-atlas-accent]').forEach((other) => {
            other.classList.toggle('on', other === btn)
          })
        }
      }
      const kind = accent || btn.dataset.atlasPlay
      const atlas = btn.dataset.atlasId
        ? { id: btn.dataset.atlasId, media: Number(btn.dataset.atlasMedia || 0) }
        : null
      playAtlas(atlas, kind, btn.dataset.atlasText || '')
    })
  })
}

export function aboutHtml() {
  return `
    <p class="atlas-about">
      插画、例句和单词录音来自开源项目
      <a href="https://github.com/designfuchen/word-atlas" target="_blank" rel="noopener">词光 Word Atlas</a>，
      以 <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener">CC BY 4.0</a>
      授权。署名：Word Atlas contributors（词光）。和爸学未修改这些素材。
      录音是合成语音（英音 ElevenLabs Alice，美音 Sarah），只授权成品音频。
      界面音效没有引入。这不是官方雅思词表。
      没有对应录音时，发音仍使用本机浏览器语音。
    </p>`
}

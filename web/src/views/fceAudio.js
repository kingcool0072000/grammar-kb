// FCE 听力播放器：长进度条（点击/拖拽跳转）+ 完整静态频谱。
// 频谱 = 整段音频逐桶能量分布一次画出（平坦段 = 空白时间，扫一眼即可
// 定位停顿/读题位置）；不用实时 Analyser——按需点开看不到后面的空白。
// 音源 <audio> 流式播放；频谱数据 fetch 全量解码一次（模块级缓存，
// 播放台反复切换不重复解码）。
// 注意：<audio> 标签无法带 Authorization 头，token 走查询参数（后端
// 对 /audio 路径开放 query token 通道）。

import { getAuth } from '../api.js'

const FCE_PREF_KEY = 'gkb-fce-pref-v1'

/** 学生练习页是否显示听力播放器（默认隐藏；#/config 配置）。 */
export function getFceAudioPref() {
  try {
    return JSON.parse(localStorage.getItem(FCE_PREF_KEY) || '{}').audioPlayer === true
  } catch { return false }
}

export function setFceAudioPref(on) {
  try {
    localStorage.setItem(FCE_PREF_KEY, JSON.stringify({ audioPlayer: !!on }))
  } catch { /* ignore */ }
}

function withToken(src) {
  try {
    const a = getAuth()
    if (a && a.token) {
      const u = new URL(src, window.location.origin)
      u.searchParams.set('token', a.token)
      return u.pathname + u.search
    }
  } catch { /* ignore */ }
  return src
}

// 频谱缓存：src → Float32Array 桶能量（0-1）。cap 24 段（LRU 简化为清最早）。
const _specCache = new Map()
function cacheGet(k) { return _specCache.get(k) }
function cachePut(k, v) {
  if (_specCache.size >= 24) _specCache.delete(_specCache.keys().next().value)
  _specCache.set(k, v)
}

const SPEC_BARS = 160 // 频谱柱数（固定数量，画布横向自适应拉伸）

async function computeSpectrum(url) {
  const cached = cacheGet(url)
  if (cached) return cached
  const r = await fetch(url)
  if (!r.ok) throw new Error(`音频拉取失败 ${r.status}`)
  const ab = await r.arrayBuffer()
  const ctx = new (window.AudioContext || window.webkitAudioContext)()
  let buf
  try {
    buf = await ctx.decodeAudioData(ab)
  } finally {
    ctx.close().catch(() => {})
  }
  const ch = buf.getChannelData(0)
  const per = Math.max(1, Math.floor(ch.length / SPEC_BARS))
  const out = new Float32Array(SPEC_BARS)
  let max = 0
  for (let i = 0; i < SPEC_BARS; i++) {
    let sum = 0
    let n = 0
    const start = i * per
    const end = Math.min(ch.length, start + per)
    for (let j = start; j < end; j += 4) { // 1/4 采样足够刻画能量包络
      sum += ch[j] * ch[j]
      n++
    }
    const rms = n ? Math.sqrt(sum / n) : 0
    out[i] = rms
    if (rms > max) max = rms
  }
  if (max > 0) for (let i = 0; i < SPEC_BARS; i++) out[i] /= max
  cachePut(url, out)
  return out
}

/**
 * 挂一个播放器到容器。src 为音频 URL（带 /api 前缀的同源流）。
 * 返回 { destroy }。
 */
export function createSpectrumPlayer(hostEl, src, opts = {}) {
  const compact = opts.compact === true
  const wrap = document.createElement('div')
  wrap.className = 'fsp' + (compact ? ' fsp-compact' : '')
  wrap.innerHTML = `
    <div class="fsp-top">
      <button class="fsp-btn" data-act="play" title="播放/暂停">▶</button>
      <button class="fsp-btn" data-act="back" title="后退 5 秒">«5</button>
      <button class="fsp-btn" data-act="fwd" title="前进 5 秒">5»</button>
      <span class="fsp-time"><b class="fsp-cur">0:00</b> / <span class="fsp-dur">--:--</span></span>
      <span class="fsp-rate">
        <button class="fsp-btn tiny" data-rate="0.75">0.75×</button>
        <button class="fsp-btn tiny on" data-rate="1">1×</button>
        <button class="fsp-btn tiny" data-rate="1.25">1.25×</button>
        <button class="fsp-btn tiny" data-rate="1.5">1.5×</button>
      </span>
    </div>
    <div class="fsp-stage">
      <canvas class="fsp-canvas" height="${compact ? 36 : 56}"></canvas>
      <div class="fsp-progress" role="slider" aria-label="播放进度">
        <div class="fsp-fill"><i class="fsp-knob"></i></div>
      </div>
      <div class="fsp-hint">完整频谱 · 平坦段 = 空白时间 · 点击/拖动进度条跳转</div>
    </div>
  `
  hostEl.appendChild(wrap)

  const audio = new Audio()
  audio.preload = 'metadata'
  audio.src = withToken(src)

  const canvas = wrap.querySelector('.fsp-canvas')
  const ctx2d = canvas.getContext('2d')
  const progress = wrap.querySelector('.fsp-progress')
  const fill = wrap.querySelector('.fsp-fill')
  const curEl = wrap.querySelector('.fsp-cur')
  const durEl = wrap.querySelector('.fsp-dur')
  const playBtn = wrap.querySelector('[data-act="play"]')

  let destroyed = false
  let specData = null

  // ---- 静态完整频谱 ----
  function drawSpectrum() {
    const dpr = window.devicePixelRatio || 1
    const w = (canvas.width = Math.max(1, Math.floor(canvas.clientWidth * dpr)))
    const h = canvas.height
    ctx2d.clearRect(0, 0, w, h)
    if (!specData) {
      ctx2d.fillStyle = '#9aa8b8'
      ctx2d.font = `${11 * dpr}px system-ui`
      ctx2d.textAlign = 'center'
      ctx2d.fillText('生成完整频谱…', w / 2, h / 2 + 4 * dpr)
      return
    }
    const bw = w / SPEC_BARS
    for (let i = 0; i < SPEC_BARS; i++) {
      const v = specData[i]
      const barH = Math.max(1.5 * dpr, v * (h - 4 * dpr))
      ctx2d.fillStyle = v > 0.035 ? '#0e7490' : '#d7dee7'
      ctx2d.fillRect(i * bw + bw * 0.15, h - barH, Math.max(1, bw * 0.7), barH)
    }
  }
  drawSpectrum()

  const specUrl = withToken(src)
  computeSpectrum(specUrl)
    .then((data) => {
      if (destroyed) return
      specData = data
      drawSpectrum()
    })
    .catch(() => {
      if (destroyed) return
      ctx2d.clearRect(0, 0, canvas.width, canvas.height)
      ctx2d.fillStyle = '#b42318'
      ctx2d.font = `${11 * (window.devicePixelRatio || 1)}px system-ui`
      ctx2d.textAlign = 'center'
      ctx2d.fillText('频谱生成失败（音频仍可播放）', canvas.width / 2, canvas.height / 2 + 4)
    })

  // 窗口宽度变化：重画（桶数据不变，只是柱宽拉伸）
  let resizeTimer = null
  const onResize = () => {
    if (resizeTimer) clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => { if (!destroyed) drawSpectrum() }, 200)
  }
  window.addEventListener('resize', onResize)

  const fmt = (s) => {
    if (!isFinite(s)) return '--:--'
    s = Math.max(0, Math.round(s))
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  }

  function renderTime() {
    curEl.textContent = fmt(audio.currentTime)
    durEl.textContent = fmt(audio.duration)
    const p = audio.duration ? audio.currentTime / audio.duration : 0
    fill.style.width = `${Math.min(100, p * 100)}%`
  }

  audio.addEventListener('timeupdate', renderTime)
  audio.addEventListener('durationchange', renderTime)
  audio.addEventListener('loadedmetadata', renderTime)
  audio.addEventListener('play', () => { playBtn.textContent = '⏸' })
  audio.addEventListener('pause', () => { playBtn.textContent = '▶' })
  audio.addEventListener('ended', () => { playBtn.textContent = '▶' })

  // ---- 控制 ----
  wrap.querySelector('.fsp-top').addEventListener('click', (e) => {
    const btn = e.target.closest('button')
    if (!btn) return
    const act = btn.dataset.act
    if (act === 'play') {
      audio.paused ? audio.play().catch(() => {}) : audio.pause()
    } else if (act === 'back') {
      audio.currentTime = Math.max(0, audio.currentTime - 5)
    } else if (act === 'fwd') {
      audio.currentTime = Math.min(audio.duration || 1e9, audio.currentTime + 5)
    } else if (btn.dataset.rate) {
      audio.playbackRate = Number(btn.dataset.rate)
      wrap.querySelectorAll('[data-rate]').forEach((b) =>
        b.classList.toggle('on', b === btn))
    }
  })

  // ---- 进度条：点击 + 拖拽（pointer 事件统一处理） ----
  let scrubbing = false
  function seekAt(clientX) {
    const r = progress.getBoundingClientRect()
    const p = Math.min(1, Math.max(0, (clientX - r.left) / r.width))
    if (audio.duration) audio.currentTime = p * audio.duration
    fill.style.width = `${p * 100}%` // 即时反馈（timeupdate 未到时）
  }
  progress.addEventListener('pointerdown', (e) => {
    scrubbing = true
    progress.setPointerCapture(e.pointerId)
    seekAt(e.clientX)
  })
  progress.addEventListener('pointermove', (e) => { if (scrubbing) seekAt(e.clientX) })
  progress.addEventListener('pointerup', () => { scrubbing = false })
  progress.addEventListener('pointercancel', () => { scrubbing = false })

  function destroy() {
    destroyed = true
    if (resizeTimer) clearTimeout(resizeTimer)
    window.removeEventListener('resize', onResize)
    try { audio.pause(); audio.src = '' } catch { /* ignore */ }
    wrap.remove()
  }
  renderTime()
  return { destroy, audio }
}

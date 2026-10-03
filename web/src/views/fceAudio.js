// FCE 听力播放器：长进度条（点击/拖拽跳转）+ 实时频谱（空白段一眼可见）。
// WebAudio AnalyserSource 驱动 canvas 频谱；教师练习页与学生听力题共用。
// 音源走 <audio> + MediaElementSource（流式加载，不做全量解码）。
// 注意：<audio> 标签无法带 Authorization 头，token 走查询参数（后端
// 对 /audio 结尾端点开放 query token 通道）。

import { getAuth } from '../api.js'

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
      <div class="fsp-hint">点击/拖动进度条跳转 · 频谱平坦段 = 空白时间</div>
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

  // ---- WebAudio 频谱 ----
  let actx = null
  let analyser = null
  let freqData = null
  let raf = 0
  let destroyed = false

  function ensureGraph() {
    if (actx || destroyed) return
    try {
      actx = new (window.AudioContext || window.webkitAudioContext)()
      const source = actx.createMediaElementSource(audio)
      analyser = actx.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.75
      source.connect(analyser)
      analyser.connect(actx.destination)
      freqData = new Uint8Array(analyser.frequencyBinCount)
      drawLoop()
    } catch {
      analyser = null // 跨域/autoplay 限制下降级：无频谱只放进度
    }
  }

  function drawLoop() {
    if (destroyed) return
    raf = requestAnimationFrame(drawLoop)
    const w = (canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1))
    const h = canvas.height
    ctx2d.clearRect(0, 0, w, h)
    if (!analyser) return
    analyser.getByteFrequencyData(freqData)
    const BARS = Math.floor(w / 6)
    const step = Math.floor(freqData.length / BARS) || 1
    for (let i = 0; i < BARS; i++) {
      let v = 0
      for (let j = 0; j < step; j++) v = Math.max(v, freqData[i * step + j] || 0)
      const barH = Math.max(2, (v / 255) * (h - 4))
      ctx2d.fillStyle = v > 12 ? '#0e7490' : '#d7dee7'
      ctx2d.fillRect(i * 6, h - barH, 4, barH)
    }
  }

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
      ensureGraph()
      if (actx && actx.state === 'suspended') actx.resume()
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
    cancelAnimationFrame(raf)
    try { audio.pause(); audio.src = '' } catch { /* ignore */ }
    if (actx) actx.close().catch(() => {})
    wrap.remove()
  }
  renderTime()
  return { destroy, audio }
}

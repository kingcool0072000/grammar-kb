import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { fmtDur as fmtSec } from '../fmt.js'

// 提升档 · 句型复述面板 + 通用复述录音机核心。
// 录音核心从 videoStudy.js 的 setupRetellRecorder 抽出（MediaRecorder →
// 静音检查 → base64 → 提交回调），视频复述与本面板共用同一套交互：
// 音量条 / 静音拦截 / 到时自动停 / 提交消息文案。

export function createRetellRecorder({
  btn, timeEl, msgEl, levelEl, maxSec = 600,
  startLabel = '开始录音', stopLabel = '停止录音', doneLabel,
  silentHtml, onSubmit,
}) {
  if (!btn) return { destroy() {} }
  let recorder = null
  let chunks = []
  let timer = null
  let startedAt = 0
  let recording = false
  let meterStop = null

  const startMeter = (stream) => {
    if (!levelEl) return null
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const src = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      src.connect(analyser)
      const buf = new Uint8Array(analyser.fftSize)
      let raf = 0
      const tick = () => {
        analyser.getByteTimeDomainData(buf)
        let peak = 0
        for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128))
        const level = Math.min(1, peak / 60)
        levelEl.style.width = `${Math.round(level * 100)}%`
        levelEl.classList.toggle('silent', level < 0.03)
        raf = requestAnimationFrame(tick)
      }
      tick()
      return () => {
        cancelAnimationFrame(raf)
        src.disconnect()
        ctx.close().catch(() => {})
        levelEl.style.width = '0%'
      }
    } catch {
      return null
    }
  }

  const blobIsSilent = async (blob) => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const ab = await blob.arrayBuffer()
      const audio = await ctx.decodeAudioData(ab)
      let peak = 0
      for (let c = 0; c < audio.numberOfChannels; c++) {
        const d = audio.getChannelData(c)
        for (let i = 0; i < d.length; i += 16) peak = Math.max(peak, Math.abs(d[i]))
      }
      ctx.close().catch(() => {})
      return peak < 0.01
    } catch {
      return false
    }
  }

  btn.addEventListener('click', async () => {
    if (!recording) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        recorder = new MediaRecorder(stream)
        chunks = []
        recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data)
        recorder.onstop = () => {
          stream.getTracks().forEach((t) => t.stop())
          if (meterStop) { meterStop(); meterStop = null }
          submit()
        }
        recorder.start()
        recording = true
        btn.dataset.recording = '1'
        startedAt = Date.now()
        btn.textContent = stopLabel
        btn.classList.add('danger')
        if (msgEl) msgEl.textContent = ''
        meterStop = startMeter(stream)
        timer = setInterval(() => {
          const sec = Math.floor((Date.now() - startedAt) / 1000)
          if (timeEl) timeEl.textContent = `⏱ ${fmtSec(sec)} / ${fmtSec(maxSec)}`
          if (sec >= maxSec) recorder.stop()
        }, 500)
      } catch (e) {
        if (msgEl) msgEl.textContent = `无法访问麦克风：${e.message}`
      }
    } else {
      recorder.stop()
    }
  })

  async function submit() {
    recording = false
    delete btn.dataset.recording
    clearInterval(timer)
    btn.textContent = startLabel
    btn.classList.remove('danger')
    const duration = Math.round((Date.now() - startedAt) / 1000)
    if (timeEl) timeEl.textContent = ''
    const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' })
    if (blob.size < 200) {
      if (msgEl) msgEl.textContent = '录音太短，请重试'
      return
    }
    if (msgEl) msgEl.textContent = '检查录音…'
    if (await blobIsSilent(blob)) {
      if (msgEl) {
        msgEl.innerHTML = silentHtml
          || '⚠️ 录音里没有任何声音，已取消提交。请检查麦克风后重录。'
      }
      return
    }
    if (msgEl) msgEl.textContent = '提交中…'
    const b64 = await blobToB64(blob)
    try {
      await onSubmit({ blob, b64, duration })
      btn.textContent = doneLabel || startLabel
    } catch (e) {
      if (msgEl) msgEl.textContent = `提交失败：${e.message}`
    }
  }

  return {
    destroy() {
      clearInterval(timer)
      try { if (recording && recorder && recorder.state !== 'inactive') recorder.stop() } catch { /* 已停 */ }
      if (meterStop) { meterStop(); meterStop = null }
    },
  }
}

function blobToB64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result.split(',')[1])
    r.onerror = reject
    r.readAsDataURL(blob)
  })
}

// ---------- 提升档 · 句型复述面板（模态，仿批改中心详情弹层） ----------
// 只读展示句型原文/翻译/提示 + 录音提交（POST /video-study/retells，
// ref_kind:'pattern'、vid=patternId）。重录 = 再提交一次（后端刷新）。
const MAX_RETELL_SEC = 180

export async function openBoostRetell({ patternId, label, onSubmitted }) {
  let p = null
  let loadErr = ''
  try {
    const list = await api.patternsList()
    p = (Array.isArray(list) ? list : []).find((x) => Number(x.id) === Number(patternId)) || null
    if (!p) loadErr = '没有找到这条句型（可能已被删除），请找老师确认。'
  } catch (e) {
    loadErr = `句型内容加载失败：${e.message}`
  }

  const root = document.createElement('div')
  root.className = 'boost-retell'
  let recorder = { destroy() {} }  // 句型加载失败时也允许安全 close
  const hints = Array.isArray(p?.hints) ? p.hints.filter(Boolean) : []
  root.innerHTML = `
    <div class="boost-retell-mask" data-close></div>
    <div class="boost-retell-card">
      <div class="boost-retell-head">
        <b>⭐ 句型复述</b>
        <button class="reading-btn small" data-close>关闭</button>
      </div>
      ${label ? `<p class="boost-retell-label">${escapeHtml(label)}</p>` : ''}
      ${p ? `
        <div class="boost-retell-sentence">${escapeHtml(p.sentence || '')}</div>
        ${p.translation ? `<p class="boost-retell-trans">${escapeHtml(p.translation)}</p>` : ''}
        ${hints.length ? `
          <div class="boost-retell-hints">
            <b>💡 提示</b>
            ${hints.map((h) => `<span>${escapeHtml(h)}</span>`).join('')}
          </div>` : ''}
        <div class="boost-retell-rec">
          <button class="reading-btn primary" data-rec>🎙 开始录音</button>
          <span class="boost-retell-time" data-time></span>
          <span class="reading-level-wrap" title="录音音量：说话时绿色条应跳动；不动=麦克风没声音">
            <span class="reading-level-bar"><i data-level></i></span>
          </span>
          <span class="boost-retell-msg" data-msg></span>
        </div>
        <p class="reading-hint">看清句型，用自己的话大声读出来（≤3 分钟）。提交后等老师批改，7 分以上算过关；不满意可重录刷新。</p>
      ` : `
        <p class="boost-retell-miss">${escapeHtml(loadErr || '句型内容不可用。')}</p>
      `}
    </div>`
  document.body.appendChild(root)
  document.body.style.overflow = 'hidden'

  const close = () => {
    recorder.destroy()
    document.removeEventListener('keydown', onKey)
    root.remove()
    document.body.style.overflow = ''
  }
  const onKey = (e) => { if (e.key === 'Escape') close() }
  document.addEventListener('keydown', onKey)
  root.querySelectorAll('[data-close]').forEach((x) => x.addEventListener('click', close))

  if (!p) return { close }

  const msgEl = root.querySelector('[data-msg]')
  recorder = createRetellRecorder({
    btn: root.querySelector('[data-rec]'),
    timeEl: root.querySelector('[data-time]'),
    msgEl,
    levelEl: root.querySelector('[data-level]'),
    maxSec: MAX_RETELL_SEC,
    startLabel: '🎙 开始录音',
    stopLabel: '⏹ 停止录音',
    doneLabel: '↻ 重录',
    onSubmit: async ({ blob, b64, duration }) => {
      await api.videoRetellSubmit({
        vid: p.id,
        ref_kind: 'pattern',
        audio_b64: b64,
        mime: blob.type || 'audio/webm',
        duration_sec: duration,
      })
      msgEl.innerHTML = `✅ 已提交（${fmtSec(duration)}），等老师批改`
      if (onSubmitted) onSubmitted()
    },
  })
  return { close }
}

// ---------- item_ref 解析（提升档任务行 → 直达动作） ----------
// memory: `memory:{topic_id}/{node_key}`（分隔符兼容 : / /）→ {topicId, nodeKey}
// retell: `retell:{id}` → {patternId}
export function parseBoostRef(itemRef, kind) {
  const s = String(itemRef || '').trim()
  if (!s) return null
  if (kind === 'retell') {
    const m = /retell[:/]?(\d+)/.exec(s) || /(\d+)/.exec(s)
    return m ? { patternId: Number(m[1]) } : null
  }
  const body = s.replace(/^memory[:/]?/, '')
  const parts = body.split(/[:/]/).map((x) => x.trim()).filter(Boolean)
  if (!parts.length) return null
  return { topicId: parts[0], nodeKey: parts.slice(1).join('/') || '' }
}

import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { fmtDur as fmtSec } from '../fmt.js'
import { retellLoadState, retellMyRecords, retellRecordsHtml } from './boostNav.js'

export { parseBoostRef } from './boostNav.js'

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

export async function openBoostRetell({ patternId, label, onSubmitted } = {}) {
  if (typeof document === 'undefined' || !document.body) {
    throw new Error('页面还没准备好，请刷新后再试。')
  }
  const root = document.createElement('div')
  root.className = 'boost-retell'
  let recorder = { destroy() {} }  // 句型加载失败时也允许安全 close
  let closed = false
  // 先把面板挂上，再等接口。慢请求或失败时点击也有可见反馈，不再像没点到。
  root.innerHTML = `
    <div class="boost-retell-mask" data-close></div>
    <div class="boost-retell-card">
      <div class="boost-retell-head">
        <b>⭐ 句型复述</b>
        <button class="reading-btn small" type="button" data-close>关闭</button>
      </div>
      ${label ? `<p class="boost-retell-label">${escapeHtml(label)}</p>` : ''}
      <div data-body>
        <p class="boost-retell-loading">正在加载句型…</p>
      </div>
    </div>`
  document.body.appendChild(root)
  document.body.style.overflow = 'hidden'

  const close = () => {
    closed = true
    recorder.destroy()
    document.removeEventListener('keydown', onKey)
    root.remove()
    document.body.style.overflow = ''
  }
  const onKey = (e) => { if (e.key === 'Escape') close() }
  document.addEventListener('keydown', onKey)
  root.querySelectorAll('[data-close]').forEach((x) => x.addEventListener('click', close))

  const body = root.querySelector('[data-body]')
  const showMiss = (text) => {
    body.innerHTML = `<p class="boost-retell-miss">${escapeHtml(text || '句型内容不可用。')}</p>`
  }
  let list = null
  let error = ''
  let myRetells = []
  const idBad = patternId == null || patternId === '' || !Number.isFinite(Number(patternId))
  if (!idBad) {
    // 句型原文与「我的提交记录」并行拉取；记录拉失败不拦面板（只是没有成绩区）
    const jobs = [api.patternsList()
      .then((r) => { list = r })
      .catch((e) => { error = e && e.message ? e.message : String(e) })]
    if (getAuth()?.role !== 'teacher') {
      jobs.push(api.videoRetells().then((r) => { myRetells = Array.isArray(r) ? r : [] }).catch(() => {}))
    }
    await Promise.all(jobs)
  }
  if (closed) return { close }
  const state = retellLoadState({ patternId, list, error })
  const p = state.pattern
  if (!p) {
    showMiss(state.error)
    return { close }
  }
  const hints = Array.isArray(p.hints) ? p.hints.filter(Boolean) : []
  // 我的提交记录（学生端成绩反馈）：老师评分/评语/回放。首开在句型下方，
  // 有成绩后挪到最顶——孩子重开面板第一眼先看到上次的成绩。
  const token = getAuth()?.token || ''
  const audioUrl = (id) => `/api/video-study/retells/${id}/audio?token=${encodeURIComponent(token)}`
  let recState = retellMyRecords(myRetells, patternId, audioUrl)
  const recHtml = () => retellRecordsHtml(recState, { escapeHtml, fmtDur: fmtSec })
  body.innerHTML = `
    ${recState.records.length ? recHtml() : ''}
    <div class="boost-retell-sentence">${escapeHtml(p.sentence || '')}</div>
    ${p.translation ? `<p class="boost-retell-trans">${escapeHtml(p.translation)}</p>` : ''}
    ${hints.length ? `
      <div class="boost-retell-hints">
        <b>💡 提示</b>
        ${hints.map((h) => `<span>${escapeHtml(h)}</span>`).join('')}
      </div>` : ''}
    <div class="boost-retell-rec">
      <button class="reading-btn primary" type="button" data-rec>🎙 开始录音</button>
      <span class="boost-retell-time" data-time></span>
      <span class="reading-level-wrap" title="录音音量：说话时绿色条应跳动；不动=麦克风没声音">
        <span class="reading-level-bar"><i data-level></i></span>
      </span>
      <span class="boost-retell-msg" data-msg></span>
    </div>
    <p class="reading-hint">看清句型，用自己的话大声读出来（≤3 分钟）。提交后等老师批改，7 分以上算过关；不满意可重录刷新。</p>
    <div data-hist>${recState.records.length ? '' : ''}</div>`

  const msgEl = body.querySelector('[data-msg]')
  recorder = createRetellRecorder({
    btn: body.querySelector('[data-rec]'),
    timeEl: body.querySelector('[data-time]'),
    msgEl,
    levelEl: body.querySelector('[data-level]'),
    maxSec: MAX_RETELL_SEC,
    startLabel: '🎙 开始录音',
    stopLabel: '⏹ 停止录音',
    doneLabel: '↻ 重录',
    onSubmit: async ({ blob, b64, duration }) => {
      await api.videoRetellSubmit({
        vid: String(p.id),  // patterns API 返回 int id，schema 要字符串
        ref_kind: 'pattern',
        audio_b64: b64,
        mime: blob.type || 'audio/webm',
        duration_sec: duration,
      })
      msgEl.innerHTML = `✅ 已提交（${fmtSec(duration)}），等老师批改`
      // 刷新记录区：新提交即刻出现在「我的提交记录」里（待批改态）
      myRetells = await api.videoRetells().then((r) => (Array.isArray(r) ? r : [])).catch(() => myRetells)
      recState = retellMyRecords(myRetells, patternId, audioUrl)
      const hist = body.querySelector('[data-hist]')
      if (hist) hist.innerHTML = recHtml()
      const top = body.querySelector('.boost-retell-hist')
      if (!top && hist && hist.innerHTML) body.insertBefore(hist.firstElementChild, body.firstChild)
      if (onSubmitted) onSubmitted()
    },
  })
  return { close }
}

// 浏览器 TTS 偏好与发音（唯一实现，lookup.js / ttsconf 页共用）。
// 默认音色/语速存 localStorage（本设备生效——音色列表是设备相关的，
// 不随账号走）；#/ttsconf 配置页写入，各调用点读。
const LS_KEY = 'gkb-tts-pref-v1'

export function getTtsPref() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY)) || {}
  } catch {
    return {}
  }
}

export function setTtsPref(patch) {
  const next = { ...getTtsPref(), ...patch }
  localStorage.setItem(LS_KEY, JSON.stringify(next))
  return next
}

/** 按 voiceURI 现查音色对象：绝不缓存——Safari/部分 WebView 会整体重建
 * 音色数组，旧引用赋给 utterance 会触发 "synthetic voice not found"
 * 报错并静默无声（本次修复的核心）。查不到返回 null。 */
export function findVoiceByURI(uri) {
  if (!uri || !('speechSynthesis' in window)) return null
  try {
    return (
      window.speechSynthesis.getVoices().find((v) => v.voiceURI === uri) || null
    )
  } catch {
    return null
  }
}

/** TTS 发音：先 cancel 再 speak；已配置音色优先，未配置回退 en-GB 匹配。
 * 音色失效（引擎刷新/被卸载）时自动降级系统默认，绝不静默失败成无声。 */
export function speak(text) {
  if (!text || !('speechSynthesis' in window)) return
  try {
    const synth = window.speechSynthesis
    synth.cancel()
    const u = new SpeechSynthesisUtterance(text)
    const pref = getTtsPref()
    const chosen = findVoiceByURI(pref.voiceURI)
    if (chosen) {
      u.voice = chosen
      u.lang = chosen.lang
    } else {
      u.lang = 'en-GB'
      const match = synth
        .getVoices()
        .find((v) => v.lang.replace('_', '-') === 'en-GB')
      if (match) u.voice = match
    }
    u.rate = pref.rate || 0.9
    // 音色失效兜底：报错（如 not-found / interrupted 之外的失败）则去掉
    // voice 用系统默认重试一次，避免「换了音色就再也不出声」
    u.onerror = (e) => {
      if (!e || e.error === 'interrupted' || e.error === 'canceled') return
      try {
        synth.cancel()
        const u2 = new SpeechSynthesisUtterance(text)
        u2.lang = 'en-GB'
        u2.rate = pref.rate || 0.9
        synth.speak(u2)
      } catch { /* 引擎彻底不可用时静默 */ }
    }
    synth.speak(u)
  } catch {
    /* 浏览器不支持 TTS 时静默 */
  }
}

/**
 * 音色列表异步就绪：getVoices() 在部分内核（尤其安卓 WebView）首次返回空，
 * 需等 voiceschanged 或轮询兜底（有的 WebView 根本不派发该事件）。
 * 只用于「列表展示」；发音路径不得缓存本函数的结果对象。
 */
export function waitForVoices(timeoutMs = 4000) {
  return new Promise((resolve) => {
    if (!('speechSynthesis' in window)) return resolve([])
    const synth = window.speechSynthesis
    if (synth.getVoices().length) return resolve(synth.getVoices().slice())
    let done = false
    const finish = () => {
      if (done) return
      done = true
      clearInterval(timer)
      resolve(synth.getVoices().slice())
    }
    try {
      synth.addEventListener('voiceschanged', finish, { once: true })
    } catch { /* 老内核无 EventTarget 接口，靠轮询兜底 */ }
    const t0 = Date.now()
    const timer = setInterval(() => {
      if (synth.getVoices().length || Date.now() - t0 > timeoutMs) finish()
    }, 250)
  })
}

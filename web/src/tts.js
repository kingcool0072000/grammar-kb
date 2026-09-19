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

/** TTS 发音：先 cancel 再 speak；已配置音色优先，未配置回退 en-GB 匹配。 */
export function speak(text) {
  if (!text || !('speechSynthesis' in window)) return
  try {
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    const pref = getTtsPref()
    const voices = window.speechSynthesis.getVoices()
    const chosen = voices.find((v) => v.voiceURI === pref.voiceURI)
    if (chosen) {
      u.voice = chosen
      u.lang = chosen.lang
    } else {
      u.lang = 'en-GB'
      const match = voices.find((v) => v.lang.replace('_', '-') === 'en-GB')
      if (match) u.voice = match
    }
    u.rate = pref.rate || 0.9
    window.speechSynthesis.speak(u)
  } catch {
    /* 浏览器不支持 TTS 时静默 */
  }
}

/**
 * 音色列表异步就绪：getVoices() 在部分内核（尤其安卓 WebView）首次返回空，
 * 需等 voiceschanged 或轮询兜底（有的 WebView 根本不派发该事件）。
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
    synth.addEventListener('voiceschanged', finish, { once: true })
    const t0 = Date.now()
    const timer = setInterval(() => {
      if (synth.getVoices().length || Date.now() - t0 > timeoutMs) finish()
    }, 250)
  })
}

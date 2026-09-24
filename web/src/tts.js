// 浏览器 TTS 偏好与发音（唯一实现，lookup.js / ttsconf 页共用）。
// 默认音色/语速存 localStorage（本设备生效——音色列表是设备相关的，
// 不随账号走）；#/ttsconf 配置页写入，各调用点读。
//
// 可靠性设计（多层降级，保证「永远尽量出声」）：
// L1 配置音色（按 voiceURI 现查对象，绝不缓存引用——Safari 会整体重建
//    音色数组，旧引用触发 "synthetic voice not found" 静默失败）
// L2 语言匹配（en-GB 优先的系统音色）
// L3 裸文本（不设 voice/lang，交给系统默认）
// 每层失败自动落下一层，并在控制台打 [TTS] 前缀诊断日志，方便用户回传。
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

/** 按 voiceURI 现查音色对象：查不到返回 null。 */
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

function ttsLog(...args) {
  // 生产诊断口：用户报「不发音」时，让 ta 打开控制台把 [TTS] 行截图/复制回来
  try {
    console.info('[TTS]', ...args)
  } catch { /* 极端环境 console 被禁 */ }
}

/** 内部：构造并播报一条 utterance。voice/lang 二选一传入构成降级链。
 * 返回 true 表示已提交引擎（不代表一定出声，出声失败走 onerror 回调）。 */
function _emit(text, { voice, lang, rate, onFail, onDone }) {
  const synth = window.speechSynthesis
  synth.cancel()
  const u = new SpeechSynthesisUtterance(text)
  if (voice) {
    u.voice = voice
    u.lang = voice.lang || lang || 'en-GB'
  } else if (lang) {
    u.lang = lang
  }
  u.rate = rate || 0.9
  let settled = false
  u.onstart = () => {
    settled = true
    if (onDone) onDone()
  }
  u.onerror = (e) => {
    const err = (e && e.error) || 'unknown'
    ttsLog('utterance error:', err, {
      voice: voice && voice.voiceURI, lang: u.lang,
    })
    // interrupted/canceled 是「被新发音顶掉」的正常事件，不是故障
    if (err === 'interrupted' || err === 'canceled') return
    if (!settled && onFail) onFail(err)
  }
  try {
    synth.speak(u)
  } catch (err) {
    ttsLog('speak() threw:', err && err.message)
    if (onFail) onFail(String(err))
    return false
  }
  return true
}

/** 发音核心：三层降级链。opts.freshURI 存在时优先按它现查（试听路径）。 */
function _speakChain(text, rate, freshURI) {
  if (!text || !('speechSynthesis' in window)) return
  const pref = getTtsPref()
  const effRate = rate || pref.rate || 0.9
  const uri = freshURI || pref.voiceURI

  // L1：配置音色（现查对象）
  const chosen = findVoiceByURI(uri)
  if (chosen) {
    _emit(text, { voice: chosen, rate: effRate, onFail: () => {
      // L2：语言匹配
      const m = window.speechSynthesis
        .getVoices()
        .find((v) => v.lang.replace('_', '-') === 'en-GB')
      if (m) {
        ttsLog('降级 L2（语言匹配）', m.voiceURI)
        _emit(text, { voice: m, rate: effRate, onFail: () => {
          // L3：裸文本
          ttsLog('降级 L3（系统默认）')
          _emit(text, { rate: effRate })
        }})
      } else {
        ttsLog('降级 L3（系统默认，无 en-GB 音色）')
        _emit(text, { rate: effRate })
      }
    }})
    return
  }

  // 未配置音色（或已失效）：直接 L2
  const m = window.speechSynthesis
    .getVoices()
    .find((v) => v.lang.replace('_', '-') === 'en-GB')
  if (m) {
    _emit(text, { voice: m, rate: effRate, onFail: () => {
      ttsLog('降级 L3（系统默认）')
      _emit(text, { rate: effRate })
    }})
  } else {
    _emit(text, { lang: 'en-GB', rate: effRate })
  }
}

/** TTS 发音（对外）：查词、朗读等业务入口。 */
export function speak(text) {
  try {
    _speakChain(text)
  } catch (err) {
    ttsLog('speak 外层异常:', err && err.message)
  }
}

/** 试听指定音色（设置中心 🔊 与选中行）：按 URI 现查，失败沿降级链出声。 */
export function speakWithVoice(uri, rate) {
  try {
    _speakChain(
      'Hello! This is how I sound when I read for you.',
      rate, uri,
    )
  } catch (err) {
    ttsLog('speakWithVoice 外层异常:', err && err.message)
  }
}

/** 引擎自检（设置中心加载时调用）：回报音色数与可疑状态。 */
export function ttsDiagnostics() {
  if (!('speechSynthesis' in window)) {
    return { ok: false, reason: 'no-speechSynthesis' }
  }
  let n = 0
  try {
    n = window.speechSynthesis.getVoices().length
  } catch { /* ignore */ }
  return { ok: n > 0, voices: n }
}

/**
 * 音色列表异步就绪：getVoices() 在部分内核首次返回空，需等 voiceschanged
 * 或轮询兜底（有的 WebView 根本不派发该事件）。只用于「列表展示」；
 * 发音路径不得缓存本函数的结果对象。
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

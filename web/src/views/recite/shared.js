// 背单词子模块（cards / engine）间共用的小常量与开关（自 views/recite.js 原样搬移）。

// POS_CN / FORM_CN 从共享模块 ../fmt.js 导入（全称口径）
const FORM_KEYS = ['past', 'past_participle', 'present_participle', 'third_singular', 'comparative', 'superlative', 'plural']
const TTS_KEY = 'gkb-rc-tts-v1' // 翻面自动发音开关（默认开）

export function ttsOn() {
  try {
    return localStorage.getItem(TTS_KEY) !== 'off'
  } catch {
    return true
  }
}

export function setTtsOn(on) {
  try {
    if (on) localStorage.removeItem(TTS_KEY)
    else localStorage.setItem(TTS_KEY, 'off')
  } catch { /* 忽略 */ }
}

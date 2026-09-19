// 阅读练习排版偏好（字号/正文栏宽）：localStorage 本设备生效，
// #/readconf 隐藏配置页写入；reading.js 渲染正文时通过 CSS 变量应用到
// .reading-article。默认值与 styles.css 内置值一致（19px / 620px）。
const LS_KEY = 'gkb-read-pref-v1'

const DEFAULTS = { fontSize: 19, width: 620 }
const LIMITS = { fontSize: [15, 28], width: [480, 980] }

function clamp(v, [lo, hi], dft) {
  const n = Number(v)
  if (!Number.isFinite(n)) return dft
  return Math.min(hi, Math.max(lo, Math.round(n)))
}

export function getReadPref() {
  let saved = {}
  try {
    saved = JSON.parse(localStorage.getItem(LS_KEY)) || {}
  } catch {
    saved = {}
  }
  return {
    fontSize: clamp(saved.fontSize, LIMITS.fontSize, DEFAULTS.fontSize),
    width: clamp(saved.width, LIMITS.width, DEFAULTS.width),
  }
}

export function setReadPref(patch) {
  const cur = getReadPref()
  const next = {
    fontSize: clamp(patch.fontSize ?? cur.fontSize, LIMITS.fontSize, cur.fontSize),
    width: clamp(patch.width ?? cur.width, LIMITS.width, cur.width),
  }
  localStorage.setItem(LS_KEY, JSON.stringify(next))
  return next
}

export function resetReadPref() {
  localStorage.removeItem(LS_KEY)
  return { ...DEFAULTS }
}

/** 把偏好写到容器 style（--rd-fs / --rd-w），供 .reading-article 消费。 */
export function applyReadPref(el) {
  if (!el) return getReadPref()
  const p = getReadPref()
  el.style.setProperty('--rd-fs', `${p.fontSize}px`)
  el.style.setProperty('--rd-w', `${p.width}px`)
  return p
}

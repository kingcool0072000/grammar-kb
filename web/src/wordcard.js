// 词卡三档（释义 / 插图 / 例句）按功能开关。
// 老师在 #/config 写入 /library/settings 的 student.wordCards，师生各端读同一份。
// 缺省与服务端 WORD_CARD_DEFAULTS 对齐：泛读馆、阅读练习只开释义。
import { api } from './api.js'

export const WORD_CARD_FEATURES = [
  { id: 'library', label: '泛读馆', hint: '阅读时查词浮层' },
  { id: 'reading', label: '阅读练习', hint: '精读划词查词' },
  { id: 'recite', label: '背单词', hint: '翻面后的插画、释义和例句' },
  { id: 'conquered', label: '已攻克词本', hint: '点词弹窗' },
  { id: 'vocab', label: '词表', hint: '单词级别与词条抽屉' },
]

export const WORD_CARD_FIELDS = [
  { id: 'def', label: '释义' },
  { id: 'image', label: '插图' },
  { id: 'example', label: '例句' },
]

export const WORD_CARD_DEFAULTS = {
  library: { def: true, image: false, example: false },
  reading: { def: true, image: false, example: false },
  recite: { def: true, image: true, example: true },
  conquered: { def: true, image: true, example: true },
  vocab: { def: true, image: true, example: true },
}

let cache = null
let pending = null

export function getWordCard(feature) {
  const dft = WORD_CARD_DEFAULTS[feature] || WORD_CARD_DEFAULTS.library
  const row = cache && cache[feature]
  return {
    def: row && typeof row.def === 'boolean' ? row.def : dft.def,
    image: row && typeof row.image === 'boolean' ? row.image : dft.image,
    example: row && typeof row.example === 'boolean' ? row.example : dft.example,
  }
}

export function wordCardSnapshot() {
  const out = {}
  for (const f of WORD_CARD_FEATURES) out[f.id] = getWordCard(f.id)
  return out
}

export function setWordCardCache(cards) {
  cache = cards && typeof cards === 'object' ? cards : {}
}

export function loadWordCards() {
  if (pending) return pending
  pending = api.librarySettings().then((s) => {
    const cards = s && s.student && s.student.wordCards
    cache = cards && typeof cards === 'object' ? cards : {}
    return cache
  }).catch(() => {
    cache = cache || {}
    return cache
  })
  return pending
}

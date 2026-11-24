import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PLAN_ADD_TYPES, boostDetailsToggle,
  boostReady, tasksForSave,
  defaultVocabLevel, vocabLevelWarning,
} from '../src/views/planAdd.js'
import { vocabPaperShort } from '../src/fmt.js'

test('提升档 details 重绘回声不重新加载', () => {
  assert.equal(boostDetailsToggle(false, true), 'load')
  assert.equal(boostDetailsToggle(true, false), 'close')
  // paint() 把已经打开的 <details open> 再写一遍，Chrome 会再发 toggle
  assert.equal(boostDetailsToggle(true, true), 'ignore')
  assert.equal(boostDetailsToggle(false, false), 'ignore')
})

test('添加提升任务：口诀卡和句型都要选齐才可点', () => {
  assert.equal(boostReady({ type: 'memory', topic: '', node: '' }), false)
  assert.equal(boostReady({ type: 'memory', topic: 't', node: '' }), false)
  assert.equal(boostReady({ type: 'memory', topic: 't', node: 'k' }), true)
  assert.equal(boostReady({ type: 'retell', pattern: '' }), false)
  assert.equal(boostReady({ type: 'retell', pattern: '12' }), true)
  assert.equal(boostReady({ type: 'retell', pattern: 0 }), false)
})

test('添加项不再提供精读，泛读和提升素材还在', () => {
  const ids = PLAN_ADD_TYPES.map((t) => t.id)
  assert.equal(ids.includes('article'), false)
  assert.equal(ids.includes('speak'), false)
  assert.equal(ids.includes('reading'), true)
  assert.equal(ids.filter((id) => id === 'video').length, 1)
})

test('保存时保留历史精读分组，丢掉单独的朗读篇数，提升档条目还在', () => {
  const saved = tasksForSave({
    speak: 3,
    article_units: [{ ids: [7], mode: 'daily', legacy: true }],
    boost_days: [2, 4],
    boost_items: [{ type: 'retell', pattern_id: 12 }],
  })
  assert.equal(saved.speak, undefined)
  assert.deepEqual(saved.article_units, [{ ids: [7], mode: 'daily' }])
  assert.deepEqual(saved.boost_items, [{ type: 'retell', pattern_id: 12 }])
  assert.deepEqual(saved.boost_days, [2, 4])
})

test('单词试卷短名不把完整卷号放进一行', () => {
  const id = 'L3-20261023010101-2222'
  const lab = vocabPaperShort(id, 3)
  assert.equal(lab.full, id)
  assert.equal(lab.short, 'L3 · 10-23 01:01 · 尾号2222')
  assert.equal(lab.short.includes(id), false)
  const mix = vocabPaperShort({ paper_id: 'MIX-20261023010101-1111', level: -1 })
  assert.equal(mix.short, '已学混合卷 · 10-23 01:01 · 尾号1111')
  const odd = vocabPaperShort('paper-with-a-very-long-custom-name')
  assert.equal(odd.full, 'paper-with-a-very-long-custom-name')
  assert.equal(odd.short.endsWith('m-name'), true)
  assert.equal(odd.short.length < odd.full.length, true)
})

// 取自 2026-11-23 那周的真实形态：0 级已经背完，孩子在背 1 级
const VOCAB_WEEK = {
  practicing_level: null,
  levels: [
    { level: 0, words: 499, base: 499, gained: 0 },
    { level: 1, words: 1128, base: 938, gained: 99 },
    { level: 2, words: 2171, base: 5, gained: 0 },
  ],
}

test('词汇默认级别：选过不动；其次孩子最近在背的；再其次挑还有词没背完的', () => {
  assert.equal(defaultVocabLevel({ vocab_lv: 2 }, { ...VOCAB_WEEK, practicing_level: 1 }), 2)
  assert.equal(defaultVocabLevel({}, { ...VOCAB_WEEK, practicing_level: 1 }), 1)
  // 不知道孩子在背哪一级：不能落到已经背完的 0 级（2026-11-23 那周进度一直是 0 的原因）
  assert.equal(defaultVocabLevel({}, VOCAB_WEEK), 1)
  // 这一级还有词没背完：照旧取第一个有词的级别
  assert.equal(defaultVocabLevel({}, { levels: [{ level: 0, words: 10, base: 10 }] }), 0)
  assert.equal(defaultVocabLevel({}, { levels: [] }, [{ level: 0, words: 499 }]), 0)
})

test('词汇目标选了孩子已背完的级别要报出来，别的级别不报', () => {
  const msg = vocabLevelWarning({ vocab_lv: 0, vocab_new: 120 }, VOCAB_WEEK)
  assert.match(msg, /L0/)
  assert.match(msg, /499/)
  assert.match(msg, /一直是 0/)
  // 知道孩子在背哪一级时顺带指出来
  assert.match(
    vocabLevelWarning({ vocab_lv: 0, vocab_new: 120 }, { ...VOCAB_WEEK, practicing_level: 1 }),
    /孩子最近在背 L1/,
  )
  assert.equal(vocabLevelWarning({ vocab_lv: 1, vocab_new: 120 }, VOCAB_WEEK), '')
  // 不要求「本周新掌握」时不报
  assert.equal(vocabLevelWarning({ vocab_lv: 0, vocab_new: 0 }, VOCAB_WEEK), '')
  assert.equal(vocabLevelWarning({}, VOCAB_WEEK), '')
})

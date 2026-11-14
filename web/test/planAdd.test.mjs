import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PLAN_ADD_TYPES, boostDetailsToggle,
  boostReady, tasksForSave,
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

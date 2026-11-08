import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PLAN_ADD_TYPES, appendArticleUnit, articleUnitLine, boostDetailsToggle,
  boostReady, completionLine, tasksForSave,
} from '../src/views/planAdd.js'

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

test('添加项只有一个精读+朗读，不再单列朗读篇数', () => {
  const ids = PLAN_ADD_TYPES.map((t) => t.id)
  assert.equal(ids.filter((id) => id === 'article').length, 1)
  assert.equal(ids.includes('speak'), false)
  const article = PLAN_ADD_TYPES.find((t) => t.id === 'article')
  assert.match(article.label, /精读\+朗读/)
  assert.match(article.hint, /1–2 篇/)
})

test('精读+朗读一组可以是 1 篇，也可以是 2 篇', () => {
  const one = appendArticleUnit({}, [81], 'daily')
  assert.deepEqual(one.article_units, [{ ids: [81], mode: 'daily' }])
  const two = appendArticleUnit({}, [82, 81], 'weekend')
  assert.deepEqual(two.article_units, [{ ids: [81, 82], mode: 'weekend' }])
  assert.equal(appendArticleUnit({}, [], 'daily').article_units, undefined)
  assert.equal(appendArticleUnit({}, [1, 2, 3], 'daily').article_units, undefined)
  const dup = appendArticleUnit(one, [81], 'daily')
  assert.equal(dup.article_units.length, 1)
  const line = articleUnitLine(one.article_units[0], new Map([['81', '短文']]))
  assert.match(line.head, /精读\+朗读 · 1 篇 · 工作日摊开/)
  assert.match(completionLine('article', { mode: 'daily', count: 1 }), /1 篇/)
  assert.match(completionLine('article', { mode: 'weekend', count: 2 }), /周六一起/)
  assert.equal(completionLine('speak'), '')
})

test('保存时丢掉单独的朗读篇数，提升档条目还在', () => {
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

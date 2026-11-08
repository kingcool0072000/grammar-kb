import assert from 'node:assert/strict'
import { test } from 'node:test'
import { studentTopicText, topicDoneCount } from '../src/views/topicText.js'

const REAL = '哈1 · 第22～28讲 动词时态专题'

test('学生标题去掉「谁的专属专题」，保留真正的专题名', () => {
  assert.equal(studentTopicText('malin,mxy 的专属专题'), '')
  assert.equal(studentTopicText('malin、mxy 的专属专题'), '')
  assert.equal(studentTopicText('malin，mxy的专属专题'), '')
  assert.equal(studentTopicText('malin 的专属专题'), '')
  assert.equal(studentTopicText(REAL), REAL)
  assert.equal(studentTopicText(`${REAL} · malin,mxy 的专属专题`), REAL)
  assert.equal(studentTopicText(`${REAL} malin,mxy 的专属专题`), REAL)
  assert.equal(
    studentTopicText('和爸学 · 专题学习 · malin,mxy 的专属专题'),
    '和爸学 · 专题学习',
  )
  const sub = '八种时态手册：总览 → 一般现在。绑定马林；热身题全部自含。'
  assert.equal(studentTopicText(sub), sub)
  assert.equal(studentTopicText('爸爸给你准备的专属手册'), '爸爸给你准备的专属手册')
  assert.equal(studentTopicText(''), '')
})

test('列表和详情用同一口径数完成节点', () => {
  const nodes = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l']
  assert.deepEqual(topicDoneCount(['c', 'a', 'b', 'stale'], nodes), { done: 3, total: 12 })
  assert.deepEqual(topicDoneCount([], nodes), { done: 0, total: 12 })
  assert.deepEqual(topicDoneCount(['a', 'a', ''], nodes), { done: 1, total: 12 })
  assert.deepEqual(topicDoneCount(['a', 'b'], null), { done: 2, total: null })
})

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { routeSegs } from '../src/shell/dom.js'
import {
  boostClickFromRows, boostTasksForDate, focusMemoryCard, memoryTopicHash,
  nodeKeyFromHash, openBoostFromClick, parseBoostRef, planBoostOpen,
  retellLoadState, topicRouteFromHash,
} from '../src/views/boostNav.js'

const MEM = 'memory:lecture-30-passive-2:k2'
const RETELL = 'retell:1'

test('parseBoostRef：口诀卡和句型引用', () => {
  assert.deepEqual(parseBoostRef(MEM, 'memory'), {
    topicId: 'lecture-30-passive-2', nodeKey: 'k2',
  })
  assert.deepEqual(parseBoostRef('memory/lecture-30-passive-2/k2', 'memory'), {
    topicId: 'lecture-30-passive-2', nodeKey: 'k2',
  })
  // 节点 key 里再带分隔符时，topic 取第一段，其余拼回去
  assert.deepEqual(parseBoostRef('memory:topic:a:b', 'memory'), {
    topicId: 'topic', nodeKey: 'a/b',
  })
  assert.equal(parseBoostRef('', 'memory'), null)
  assert.equal(parseBoostRef('memory:', 'memory'), null)
  assert.deepEqual(parseBoostRef(RETELL, 'retell'), { patternId: 1 })
  assert.deepEqual(parseBoostRef('retell/12', 'retell'), { patternId: 12 })
  assert.equal(parseBoostRef('retell:', 'retell'), null)
  // 没传 kind 时按前缀认，避免 retell:1 被当成专题
  assert.deepEqual(parseBoostRef(RETELL), { patternId: 1 })
})

test('口诀卡深链带 node，刷新后 routeSegs 仍停在专题', () => {
  const hash = memoryTopicHash('lecture-30-passive-2', 'k2')
  assert.equal(hash, '/topics/lecture-30-passive-2?node=k2')
  globalThis.location = { hash: `#${hash}` }
  assert.deepEqual(routeSegs(), ['topics', 'lecture-30-passive-2'])
  assert.deepEqual(topicRouteFromHash(location.hash), {
    topicId: 'lecture-30-passive-2', nodeKey: 'k2',
  })
  const fancy = memoryTopicHash('t/专题', '口诀&卡')
  assert.equal(nodeKeyFromHash(`#${fancy}`), '口诀&卡')
  assert.equal(topicRouteFromHash(`#${fancy}`).topicId, 't/专题')
  assert.equal(nodeKeyFromHash('#/topics/only'), '')
  assert.deepEqual(topicRouteFromHash('#/topics'), { topicId: '', nodeKey: '' })
})

test('课程表打开口诀卡：落到指定节点，坏引用要出文案', () => {
  const hashes = []
  const notes = []
  const plan = openBoostFromClick({
    itemRef: MEM,
    kind: 'memory',
    rows: [{ boost: { item_ref: MEM, kind: 'memory' }, text: '背诵〈被动〉' }],
    notify: (m) => notes.push(m),
    navigate: (h) => hashes.push(h),
    openRetell: () => { throw new Error('口诀卡不该开复述') },
  })
  assert.equal(plan.ok, true)
  assert.deepEqual(hashes, ['/topics/lecture-30-passive-2?node=k2'])
  assert.deepEqual(notes, [])
  assert.equal(topicRouteFromHash(`#${hashes[0]}`).nodeKey, 'k2')

  notes.length = 0
  hashes.length = 0
  const bad = openBoostFromClick({
    itemRef: '',
    kind: 'memory',
    notify: (m) => notes.push(m),
    navigate: (h) => hashes.push(h),
    openRetell: () => {},
  })
  assert.equal(bad.ok, false)
  assert.deepEqual(hashes, [])
  assert.match(notes[0], /打不开/)

  notes.length = 0
  const noNode = planBoostOpen('memory:only-topic', 'memory')
  assert.equal(noNode.ok, true)
  assert.equal(noNode.hash, '/topics/only-topic')
  assert.match(noNode.message, /没有指定节点/)
})

test('句型复述 retell:1 打开面板；失败不能静默', async () => {
  const notes = []
  const retells = []
  const hashes = []
  const plan = openBoostFromClick({
    itemRef: RETELL,
    kind: 'retell',
    rows: [{ text: '⭐ 提升：句型复述', boost: { item_ref: RETELL, kind: 'retell' } }],
    notify: (m) => notes.push(m),
    navigate: (h) => hashes.push(h),
    openRetell: (opts) => {
      retells.push(opts)
      return Promise.reject(new Error('网络断开'))
    },
  })
  assert.equal(plan.kind, 'retell')
  assert.equal(plan.patternId, 1)
  assert.deepEqual(hashes, [])
  assert.equal(retells.length, 1)
  assert.equal(retells[0].patternId, 1)
  assert.match(retells[0].label, /句型复述/)
  await new Promise((r) => setTimeout(r, 0))
  assert.match(notes[0], /句型复述打不开/)
  assert.match(notes[0], /网络断开/)

  // 生产本周 malin：kind 若丢了，仍按 retell:1 开面板，不跳进专题
  const inferred = planBoostOpen(RETELL, '')
  assert.equal(inferred.kind, 'retell')
  assert.equal(inferred.patternId, 1)
})

test('句型缺失、接口失败、格式不对都有可见文案', () => {
  const missing = retellLoadState({ patternId: 1, list: [{ id: 2, sentence: 'x' }] })
  assert.equal(missing.pattern, null)
  assert.match(missing.error, /没有找到这条句型/)
  const http = retellLoadState({ patternId: 1, error: 'HTTP 500' })
  assert.match(http.error, /加载失败/)
  assert.match(http.error, /HTTP 500/)
  const weird = retellLoadState({ patternId: 1, list: { nope: true } })
  assert.match(weird.error, /格式不对/)
  const noId = retellLoadState({ patternId: null, list: [] })
  assert.match(noId.error, /缺少句型编号/)
  const ok = retellLoadState({
    patternId: '1',
    list: { items: [{ id: 1, sentence: 'There is no point doing…' }] },
  })
  assert.equal(ok.error, '')
  assert.equal(ok.pattern.sentence, 'There is no point doing…')
})

test('今日闭环去完成：用服务端那天的未完成行，没有行要提示', () => {
  const days = [
    { date: '2026-11-10', tasks: [{ boost: { item_ref: MEM, kind: 'memory', configured: true }, done: false, text: '周二卡' }] },
    { date: '2026-11-12', tasks: [{ boost: { item_ref: RETELL, kind: 'retell', configured: true }, done: false, text: '周四句型' }] },
  ]
  // 浏览器本地若还停在周二，闭环按北京周四算，应打开周四的句型
  const rows = boostTasksForDate(days, '2026-11-12')
  assert.equal(rows.length, 1)
  assert.equal(rows[0].boost.item_ref, RETELL)
  const picked = boostClickFromRows([
    { done: true, text: '已做', boost: { item_ref: MEM, kind: 'memory', configured: true } },
    { done: false, text: '还没做', boost: { item_ref: RETELL, kind: 'retell', configured: true } },
    { done: false, text: '没配', boost: { item_ref: '', kind: '', configured: false } },
  ])
  assert.equal(picked.missing, false)
  assert.equal(picked.itemRef, RETELL)
  assert.equal(picked.kind, 'retell')
  const none = boostClickFromRows([
    { done: false, boost: { configured: false, item_ref: '', kind: '' } },
  ])
  assert.equal(none.missing, true)
  assert.match(none.message, /没有可打开的提升任务/)
  assert.equal(boostTasksForDate(days, '2026-11-09').length, 0)
})

function el(attrs = {}, children = []) {
  const node = {
    attrs: { ...attrs },
    children,
    hidden: Boolean(attrs.hidden),
    scrolled: 0,
    parentNode: null,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null
    },
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null
    },
    querySelectorAll(sel) {
      const parts = sel.split(',').map((s) => s.trim())
      const out = []
      const walk = (n) => {
        for (const c of n.children || []) {
          if (parts.some((p) => classMatch(c, p))) out.push(c)
          walk(c)
        }
      }
      walk(this)
      return out
    },
    scrollIntoView() { this.scrolled += 1 },
  }
  node.classList = {
    add(...xs) {
      const set = classSet(node)
      xs.forEach((x) => set.add(x))
      node.attrs.class = [...set].join(' ')
    },
    remove(...xs) {
      const set = classSet(node)
      xs.forEach((x) => set.delete(x))
      node.attrs.class = [...set].join(' ')
    },
    contains(x) { return classSet(node).has(x) },
  }
  for (const c of children) c.parentNode = node
  return node
}

function classSet(node) {
  return new Set(String(node.attrs.class || '').split(/\s+/).filter(Boolean))
}

function classMatch(node, sel) {
  if (!sel.startsWith('.')) return false
  const names = sel.slice(1).split('.')
  return names.every((name) => node.classList.contains(name))
}

test('专题深链聚焦指定背诵卡：滚入、高亮、打开提示面', () => {
  const front = el({ class: 'topic-memory-front', hidden: true })
  const back = el({ class: 'topic-memory-back' })
  const card = el({ class: 'topic-memory', 'data-mem': 'k2' }, [front, back])
  const section = el({ class: 'topic-theme', 'data-key': 'k2' }, [card])
  const otherFront = el({ class: 'topic-memory-front' })
  const other = el({ class: 'topic-memory focus', 'data-mem': 'k1' }, [otherFront])
  const root = el({}, [
    el({ class: 'topic-theme', 'data-key': 'k1' }, [other]),
    section,
  ])
  const hit = focusMemoryCard(root, nodeKeyFromHash('#/topics/lecture-30-passive-2?node=k2'))
  assert.equal(hit.ok, true)
  assert.equal(hit.anchor, card)
  assert.equal(card.classList.contains('focus'), true)
  assert.equal(other.classList.contains('focus'), false)
  assert.equal(front.hidden, false)
  assert.equal(back.hidden, true)
  assert.equal(card.scrolled, 1)
  assert.equal(hit.message, '')

  const bare = el({}, [el({ class: 'topic-theme', 'data-key': 'k9' })])
  const noCard = focusMemoryCard(bare, 'k9')
  assert.equal(noCard.ok, false)
  assert.match(noCard.message, /还没有背诵卡/)
  assert.equal(bare.children[0].classList.contains('focus-node'), true)

  const gone = focusMemoryCard(root, 'missing')
  assert.match(gone.message, /没有找到这张口诀卡/)
  assert.match(gone.message, /missing/)
  assert.equal(gone.anchor, null)
})

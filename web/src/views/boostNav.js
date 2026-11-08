// 提升档「点进去」的纯逻辑：课程表点击和专题页深链共用。
// 不碰 DOM / 接口，方便单测。口诀卡落点用 hash 查询
// `#/topics/{topicId}?node={nodeKey}`：routeSegs 只切 `?` 前面的路径，
// 专题路由仍是 topics，刷新后节点还在。

export function parseBoostRef(itemRef, kind) {
  const s = String(itemRef || '').trim()
  if (!s) return null
  // 按钮漏传 kind 时按前缀认，避免 retell:1 被当成专题 id「retell」
  const k = kind || ((s.startsWith('retell:') || s.startsWith('retell/')) ? 'retell' : 'memory')
  if (k === 'retell') {
    const m = /retell[:/]?(\d+)/.exec(s) || /(\d+)/.exec(s)
    return m ? { patternId: Number(m[1]) } : null
  }
  const body = s.replace(/^memory[:/]?/, '')
  const parts = body.split(/[:/]/).map((x) => x.trim()).filter(Boolean)
  if (!parts.length) return null
  return { topicId: parts[0], nodeKey: parts.slice(1).join('/') || '' }
}

export function memoryTopicHash(topicId, nodeKey) {
  const id = String(topicId || '').trim()
  if (!id) return ''
  const path = `/topics/${encodeURIComponent(id)}`
  const node = String(nodeKey || '').trim()
  if (!node) return path
  return `${path}?node=${encodeURIComponent(node)}`
}

export function nodeKeyFromHash(hash) {
  const raw = String(hash || '')
  const q = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
  if (!q) return ''
  return new URLSearchParams(q).get('node') || ''
}

/** `#/topics/{id}?node=` → 专题 id + 口诀卡节点。列表页 topicId 为空。 */
export function topicRouteFromHash(hash) {
  const raw = String(hash || '')
  const segs = raw.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean)
  if (segs[0] !== 'topics' || !segs[1]) return { topicId: '', nodeKey: '' }
  let topicId = segs[1]
  try { topicId = decodeURIComponent(segs[1]) } catch { /* 保留原样 */ }
  return { topicId, nodeKey: nodeKeyFromHash(raw) }
}

function inferKind(itemRef, kind) {
  const k = String(kind || '').trim()
  if (k === 'retell' || k === 'memory') return k
  const s = String(itemRef || '').trim()
  if (s.startsWith('retell:') || s.startsWith('retell/')) return 'retell'
  return 'memory'
}

/** 一次点击要去哪。失败带中文 message，调用方必须展示，不能吞掉。 */
export function planBoostOpen(itemRef, kind) {
  const k = inferKind(itemRef, kind)
  const ref = parseBoostRef(itemRef, k)
  if (!ref) {
    return {
      ok: false,
      kind: k,
      message: k === 'retell'
        ? '这条句型复述打不开：缺少句型编号。'
        : '这条提升任务打不开：任务引用无效，请找老师确认。',
    }
  }
  if (k === 'retell') {
    if (ref.patternId == null || Number.isNaN(ref.patternId)) {
      return { ok: false, kind: 'retell', message: '这条句型复述打不开：缺少句型编号。' }
    }
    return { ok: true, kind: 'retell', patternId: ref.patternId }
  }
  const hash = memoryTopicHash(ref.topicId, ref.nodeKey)
  if (!hash) return { ok: false, kind: 'memory', message: '这条口诀卡打不开：缺少专题。' }
  return {
    ok: true,
    kind: 'memory',
    hash,
    topicId: ref.topicId,
    nodeKey: ref.nodeKey || '',
    message: ref.nodeKey ? '' : '这条口诀卡没有指定节点，已打开专题。请在页内找到背诵卡。',
  }
}

/**
 * 课程表提升档点击（日行 / 今日闭环「去完成」）。
 * navigate / openRetell / notify 由页面注入；失败一律 notify。
 */
export function openBoostFromClick({
  itemRef, kind, label, rows, notify, navigate, openRetell,
}) {
  const plan = planBoostOpen(itemRef, kind)
  const say = typeof notify === 'function' ? notify : () => {}
  if (!plan.ok) {
    say(plan.message)
    return plan
  }
  if (plan.kind === 'retell') {
    const row = (rows || []).find((t) => t && t.boost && t.boost.item_ref === itemRef)
    const text = label || (row && (row.text || row.label)) || ''
    // 同步调用：面板在点击这一拍就挂上（内部再等接口）。抛错或拒绝都要提示。
    try {
      Promise.resolve(openRetell({ patternId: plan.patternId, label: text }))
        .catch((e) => say(`句型复述打不开：${e && e.message ? e.message : e}`))
    } catch (e) {
      say(`句型复述打不开：${e && e.message ? e.message : e}`)
    }
    return plan
  }
  if (typeof navigate === 'function') navigate(plan.hash)
  if (plan.message) say(plan.message)
  return plan
}

/** 今日闭环「去完成」：跳过未配置行，优先还没做的那条。 */
export function boostClickFromRows(rows) {
  const actionable = (rows || []).filter((t) => t && t.boost && t.boost.configured !== false)
  const row = actionable.find((t) => !t.done) || actionable[0] || null
  if (!row) {
    return {
      missing: true,
      itemRef: '',
      kind: '',
      label: '',
      message: '今天没有可打开的提升任务，请刷新课程表或找老师确认。',
    }
  }
  return {
    missing: false,
    itemRef: row.boost.item_ref || '',
    kind: row.boost.kind || '',
    label: row.text || row.label || '',
    message: '',
  }
}

/** 闭环卡对应那一天的提升行。date 用服务端 today.date，和「今日闭环」同一天。 */
export function boostTasksForDate(days, date) {
  const day = (days || []).find((d) => d && d.date === date)
  return ((day && day.tasks) || []).filter((t) => t && t.boost)
}

/**
 * 句型复述加载结果。pattern 为空时 error 是要写进面板的可见文案。
 * list 兼容数组，或 `{ items: [] }`。
 */
export function retellLoadState({ patternId, list, error } = {}) {
  if (patternId == null || patternId === '' || !Number.isFinite(Number(patternId))) {
    return { pattern: null, error: '这条句型复述缺少句型编号，请找老师确认。' }
  }
  if (error) return { pattern: null, error: `句型内容加载失败：${error}` }
  const rows = Array.isArray(list) ? list
    : (list && Array.isArray(list.items) ? list.items : null)
  if (!rows) return { pattern: null, error: '句型内容加载失败：返回格式不对，请稍后重试。' }
  const pattern = rows.find((x) => x && Number(x.id) === Number(patternId)) || null
  if (!pattern) return { pattern: null, error: '没有找到这条句型（可能已被删除），请找老师确认。' }
  return { pattern, error: '' }
}

function scrollTo(el) {
  if (!el || typeof el.scrollIntoView !== 'function') return
  try { el.scrollIntoView({ behavior: 'smooth', block: 'center' }) }
  catch { el.scrollIntoView() }
}

/**
 * 专题详情里定位指定节点的背诵卡：滚到视口、高亮、打开提示面
 * （front 可见、back 收起），学生接着点「翻开」「记住了」。
 * 找不到卡时返回 message，由页面就地展示。
 */
export function focusMemoryCard(root, nodeKey) {
  const key = String(nodeKey || '').trim()
  if (!root || !key || typeof root.querySelectorAll !== 'function') {
    return { ok: false, anchor: null, message: '' }
  }
  root.querySelectorAll('.topic-memory.focus, .topic-theme.focus-node').forEach((el) => {
    el.classList.remove('focus', 'focus-node')
  })
  const card = [...root.querySelectorAll('.topic-memory')]
    .find((el) => (el.getAttribute('data-mem') || '') === key) || null
  const section = [...root.querySelectorAll('.topic-theme')]
    .find((el) => (el.getAttribute('data-key') || '') === key) || null
  if (card) {
    card.classList.add('focus')
    const front = card.querySelector('.topic-memory-front')
    const back = card.querySelector('.topic-memory-back')
    // 打开提示面，不直接翻到答案：先想，再点「翻开」，记住了再打卡
    if (front) front.hidden = false
    if (back) back.hidden = true
    if (typeof card.setAttribute === 'function') card.setAttribute('tabindex', '-1')
    if (typeof card.focus === 'function') {
      try { card.focus({ preventScroll: true }) } catch { /* 老浏览器无选项 */ }
    }
    scrollTo(card)
    return { ok: true, anchor: card, message: '' }
  }
  if (section) {
    section.classList.add('focus-node')
    scrollTo(section)
    return { ok: false, anchor: section, message: '这个知识点还没有背诵卡，请找老师确认。' }
  }
  return {
    ok: false,
    anchor: null,
    message: `没有找到这张口诀卡（节点 ${key}）。请找老师确认。`,
  }
}

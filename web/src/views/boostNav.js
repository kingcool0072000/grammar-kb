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

/** 把一个绝对时刻的年月日时分按北京时间读出，不跟机器本地时区走。 */
function beijingWall(d) {
  const cn = new Date(d.getTime() + 8 * 60 * 60 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return {
    year: cn.getUTCFullYear(),
    md: `${p(cn.getUTCMonth() + 1)}-${p(cn.getUTCDate())}`,
    hm: `${p(cn.getUTCHours())}:${p(cn.getUTCMinutes())}`,
  }
}

/** ISO 时间（UTC 带 Z / 本地串）→ 北京时间「MM-DD HH:mm」。
 *  created_at 由后端存 UTC，直接 slice 会早 8 小时。
 *  解析之后必须按东八区读钟面：用本地 getHours() 时，
 *  人在中国是对的，CI 在 UTC 上会少 8 小时。
 *  无 Z 后缀的裸串（历史数据）按已是中国时间直解。 */
export function retellTimeCn(iso) {
  if (!iso) return '—'
  const raw = String(iso)
  const d = new Date(/[Z+]/.test(raw.slice(-6)) ? raw : raw + '+08:00')
  if (isNaN(d.getTime())) return raw.slice(0, 16).replace('T', ' ')
  const wall = beijingWall(d)
  return `${wall.md} ${wall.hm}`
}

/** 句型复述「我的提交记录」区：从 videoRetells() 结果筛出该句型的提交，
 *  按时间正序，供面板展示成绩反馈（老师 X/10 或 待批改 + 评语 + 回放地址）。
 *  rows 里视频复述（ref_kind=video / vid 不匹配）全部过滤掉。 */
export function retellMyRecords(rows, patternId, audioUrl) {
  const pid = String(patternId == null ? '' : patternId)
  const mine = (Array.isArray(rows) ? rows : [])
    .filter((r) => r && r.ref_kind === 'pattern' && String(r.vid) === pid)
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1))
  const graded = mine.filter((r) => r.status === 'graded')
  const best = graded.length
    ? Math.max(...graded.map((r) => Number(r.teacher_score) || 0)) : null
  return {
    records: mine,
    best,
    passed: graded.some((r) => (Number(r.teacher_score) || 0) >= 7),
    /** 回放地址；audioUrl(id) 由调用方注入（拼 token），缺省空串不渲染 */
    audioOf: (r) => (typeof audioUrl === 'function' && r ? audioUrl(r.id) : ''),
  }
}

/** 记录区 HTML（面板内联渲染，样式复刻视频复述的成绩行）。
 *  传 escapeHtml 保持纯函数可测。 */
export function retellRecordsHtml(state, { escapeHtml: esc = String, fmtDur = (s) => `${s || 0}s`, timeCn = retellTimeCn } = {}) {
  const rs = state.records || []
  if (!rs.length) return ''
  const rows = rs.slice().reverse().map((r) => `
    <div class="reading-graded-row" data-rid="${esc(String(r.id))}">
      <div class="fce-his-row">
        <span class="fce-his-date">${esc(timeCn(r.created_at))} · ${fmtDur(r.duration_sec || 0)}</span>
        ${r.status === 'graded'
          ? `<b class="fce-his-score ${(Number(r.teacher_score) || 0) >= 8 ? 'ok' : ''}">老师 ${esc(String(r.teacher_score))}/10</b>`
          : '<b class="fce-his-score pend">待批改</b>'}
      </div>
      ${r.teacher_comment ? `<div class="reading-graded-comment">💬 ${esc(r.teacher_comment)}</div>` : ''}
      ${state.audioOf(r) ? `<audio controls preload="metadata" src="${esc(state.audioOf(r))}"></audio>` : ''}
    </div>`).join('')
  const head = state.passed
    ? '✅ 已过关（最高 ' + state.best + '/10）'
    : (state.best != null ? '最高 ' + state.best + '/10 · 7 分以上算过关' : '已提交 · 等老师批改')
  return `
    <div class="boost-retell-hist">
      <b>📝 我的提交记录（${rs.length} 次）· ${esc(head)}</b>
      ${rows}
    </div>`
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

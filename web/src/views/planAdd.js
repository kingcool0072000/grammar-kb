/**
 * 周计划清单的增删。tasks 仍是 plan_weeks 那份 JSON。
 * 精读+朗读按组存：article_units（每组 1–2 篇，带摊法 mode）。朗读不另开一项，
 * 课文勾上就含朗读。视频按组存 video_units（每组 2 个）。
 * NCE2 听力按组存 nce2_units（每组 1–5 课，带摊法 mode 和完成方式 task_mode：
 * shadow=听力跟读（缺省）、listen=仅精听）。
 * 微练习用 tasks.micro = 1 表示本周启用；旧扁平键 articles/videos 不再写。
 * 旧的 tasks.speak（单独「朗读篇数」）保存时丢掉，课表只跟课文走。
 */

function copyTasks(tasks) {
  const t = { ...(tasks || {}) }
  if (Array.isArray(t.lectures)) t.lectures = [...t.lectures]
  if (Array.isArray(t.hw_papers)) t.hw_papers = [...t.hw_papers]
  if (Array.isArray(t.focus_kps)) t.focus_kps = [...t.focus_kps]
  if (Array.isArray(t.fce)) t.fce = [...t.fce]
  if (Array.isArray(t.vocab_papers)) t.vocab_papers = [...t.vocab_papers]
  if (Array.isArray(t.article_units)) {
    t.article_units = t.article_units.map((u) => {
      const row = { ids: (u?.ids || []).map(Number), mode: u?.mode === 'weekend' ? 'weekend' : 'daily' }
      if (u?.legacy) row.legacy = true
      return row
    })
  }
  if (Array.isArray(t.video_units)) {
    t.video_units = t.video_units.map((u) => {
      const row = { vids: (u?.vids || []).map(String), mode: u?.mode === 'weekend' ? 'weekend' : 'daily' }
      if (u?.legacy) row.legacy = true
      return row
    })
  }
  if (Array.isArray(t.nce2_units)) {
    t.nce2_units = t.nce2_units.map((u) => ({
      lessons: (u?.lessons || []).map(Number),
      mode: u?.mode === 'weekend' ? 'weekend' : 'daily',
      task_mode: u?.task_mode === 'listen' ? 'listen' : 'shadow',
    }))
  }
  if (t.micro != null) t.micro = 1
  if (t.reading && typeof t.reading === 'object') t.reading = { ...t.reading }
  // 旧扁平键不进编辑流程：装载前先过 migrateLegacyUnits，这里兜底删掉，保存就不会带回旧键。
  delete t.articles
  delete t.videos
  return t
}

export { copyTasks }

/**
 * 旧周数据兼容：只有扁平 articles/videos 的老周，先转成 *_units
 * （精读一组 mode=weekend 周末集中、视频一组 mode=daily 工作日摊开，带 legacy 小标），
 * 旧键就地删掉，之后编辑与保存都只见新结构。不改入参。
 */
export function migrateLegacyUnits(tasks) {
  const t = { ...(tasks || {}) }
  const arts = [...new Set((Array.isArray(t.articles) ? t.articles : []).map(Number).filter((n) => Number.isFinite(n)))]
  const vids = [...new Set((Array.isArray(t.videos) ? t.videos : []).map(String).filter(Boolean))]
  if (arts.length) {
    t.article_units = [
      ...(Array.isArray(t.article_units) ? t.article_units : []),
      { ids: arts.sort((a, b) => a - b), mode: 'weekend', legacy: true },
    ]
  }
  if (vids.length) {
    t.video_units = [
      ...(Array.isArray(t.video_units) ? t.video_units : []),
      { vids, mode: 'daily', legacy: true },
    ]
  }
  delete t.articles
  delete t.videos
  return t
}

/** 保存/预览用的形状：只留契约字段，编辑期的 legacy 小标不落库。
 *  朗读已并进精读课文，不再单独落 speak。 */
export function tasksForSave(tasks) {
  const t = copyTasks(tasks)
  if (Array.isArray(t.article_units)) {
    t.article_units = t.article_units.map((u) => ({ ids: u.ids, mode: u.mode }))
  }
  if (Array.isArray(t.video_units)) {
    t.video_units = t.video_units.map((u) => ({ vids: u.vids, mode: u.mode }))
  }
  if (Array.isArray(t.nce2_units)) {
    t.nce2_units = t.nce2_units.map((u) => ({
      lessons: u.lessons, mode: u.mode, task_mode: u.task_mode === 'listen' ? 'listen' : 'shadow',
    }))
  }
  delete t.speak
  return t
}

/**
 * `<details open>` 重绘时，Chrome 会再发一次 toggle（open 仍是 true）。
 * 状态没变必须忽略：否则「打开 → 拉数据 → paint → toggle」死循环，
 * 下拉和「添加提升任务」在下一帧就被拆掉，点不中。
 * 返回 'load'（刚打开，要拉素材）、'close'（刚关上）、'ignore'（回声）。
 */
export function boostDetailsToggle(wasOpen, isOpen) {
  if (Boolean(wasOpen) === Boolean(isOpen)) return 'ignore'
  return isOpen ? 'load' : 'close'
}

/** 口诀卡要专题+节点；句型复述要 pattern_id。没选齐时按钮保持禁用。 */
export function boostReady({ type, topic, node, pattern } = {}) {
  return type === 'memory'
    ? Boolean(topic && node)
    : Number(pattern) > 0
}

function pushUnique(arr, value, sortNum) {
  const next = [...(arr || [])]
  if (!next.includes(value)) next.push(value)
  if (sortNum) next.sort((a, b) => a - b)
  return next
}

function dropNum(t, key, value) {
  const n = Number(value)
  t[key] = (t[key] || []).filter((x) => Number(x) !== n)
  if (!t[key].length) delete t[key]
}

function dropStr(t, key, value) {
  t[key] = (t[key] || []).filter((x) => x !== value)
  if (!t[key].length) delete t[key]
}

// ---- 精读/视频组（*_units）与微练习（micro）----

function normMode(mode) {
  return mode === 'weekend' ? 'weekend' : 'daily'
}

/** 本周所有组里的精读篇目 id（扫全部组，供「已在本周」判断）。 */
export function articleUnitIds(tasks) {
  const out = []
  for (const u of (tasks || {}).article_units || []) {
    for (const id of u?.ids || []) {
      const n = Number(id)
      if (!out.includes(n)) out.push(n)
    }
  }
  return out
}

/** 本周所有组里的视频 vid（扫全部组）。 */
export function videoUnitVids(tasks) {
  const out = []
  for (const u of (tasks || {}).video_units || []) {
    for (const v of u?.vids || []) {
      const s = String(v)
      if (!out.includes(s)) out.push(s)
    }
  }
  return out
}

/** 本周所有组里的 NCE2 课号（扫全部组，供「已在本周」判断）。 */
export function nce2UnitLessons(tasks) {
  const out = []
  for (const u of (tasks || {}).nce2_units || []) {
    for (const n of u?.lessons || []) {
      const num = Number(n)
      if (!out.includes(num)) out.push(num)
    }
  }
  return out
}

/** 精读+朗读加一组：1 篇或 2 篇都可以；0 篇、超过 2 篇、或与本周重复的篇目整组不加。 */
export function appendArticleUnit(tasks, ids, mode) {
  const t = copyTasks(tasks)
  const list = [...new Set((ids || []).map(Number).filter((n) => Number.isFinite(n)))]
  if (list.length < 1 || list.length > 2) return t
  const taken = articleUnitIds(t)
  if (list.some((id) => taken.includes(id))) return t
  t.article_units = [
    ...(t.article_units || []),
    { ids: [...list].sort((a, b) => a - b), mode: normMode(mode) },
  ]
  return t
}

/** 视频加一组：vids 必须正好 2 个；防重同精读组。 */
export function appendVideoUnit(tasks, vids, mode) {
  const t = copyTasks(tasks)
  const list = [...new Set((vids || []).map(String))]
  if (list.length !== 2) return t
  const taken = videoUnitVids(t)
  if (list.some((v) => taken.includes(v))) return t
  t.video_units = [...(t.video_units || []), { vids: list, mode: normMode(mode) }]
  return t
}

/** NCE2 听力加一组：1–5 课；防重跨组同课；lessons 升序；task_mode 缺省 shadow（跟读）。 */
export function appendNce2Unit(tasks, lessonIds, mode, taskMode) {
  const t = copyTasks(tasks)
  const list = [...new Set((lessonIds || []).map(Number).filter((n) => Number.isFinite(n)))]
    .sort((a, b) => a - b)
  if (list.length < 1 || list.length > 5) return t
  const taken = nce2UnitLessons(t)
  if (list.some((n) => taken.includes(n))) return t
  t.nce2_units = [
    ...(t.nce2_units || []),
    { lessons: list, mode: normMode(mode), task_mode: taskMode === 'listen' ? 'listen' : 'shadow' },
  ]
  return t
}

/** 按下标删整组精读。 */
export function removeArticleUnit(tasks, idx) {
  const t = copyTasks(tasks)
  const arr = [...(t.article_units || [])]
  arr.splice(Number(idx), 1)
  if (arr.length) t.article_units = arr
  else delete t.article_units
  return t
}

/** 按下标删整组视频。 */
export function removeVideoUnit(tasks, idx) {
  const t = copyTasks(tasks)
  const arr = [...(t.video_units || [])]
  arr.splice(Number(idx), 1)
  if (arr.length) t.video_units = arr
  else delete t.video_units
  return t
}

/** 按下标删整组 NCE2 听力。 */
export function removeNce2Unit(tasks, idx) {
  const t = copyTasks(tasks)
  const arr = [...(t.nce2_units || [])]
  arr.splice(Number(idx), 1)
  if (arr.length) t.nce2_units = arr
  else delete t.nce2_units
  return t
}

/** 微练习：有则删无则加（tasks.micro = 1 即本周启用，天然去重）。 */
export function toggleMicroGroup(tasks) {
  const t = copyTasks(tasks)
  if (t.micro != null) delete t.micro
  else t.micro = 1
  return t
}

const MODE_TEXT = { daily: '工作日摊开', weekend: '周末集中' }
const NCE2_TASK_TEXT = { shadow: '跟读', listen: '精听' }

function unitTitle(titles, key) {
  if (!titles) return ''
  const v = typeof titles.get === 'function' ? titles.get(String(key)) : titles[String(key)]
  return v == null ? '' : String(v)
}

/** 清单行文案：主行「📄 精读+朗读 · 1 篇 · 工作日摊开」，小字行是组内文章名（legacy 组加「旧格式」）。 */
export function articleUnitLine(unit, titles) {
  const u = unit || {}
  const ids = (u.ids || []).map(Number)
  const names = ids.map((id) => (unitTitle(titles, id) || `#${id}`).slice(0, 20))
  const head = ['📄 精读+朗读', `${ids.length} 篇`, MODE_TEXT[normMode(u.mode)]].join(' · ')
  return { head, sub: capSub((u.legacy ? '旧格式 · ' : '') + names.join('、')) }
}

/** 同上，视频版：「🎬 视频一组 · 2 个 · 摊法」。 */
export function videoUnitLine(unit, titles) {
  const u = unit || {}
  const vids = (u.vids || []).map(String)
  const names = vids.map((v) => (unitTitle(titles, v) || v).slice(0, 20))
  const head = ['🎬 视频一组', `${vids.length} 个`, MODE_TEXT[normMode(u.mode)]].join(' · ')
  return { head, sub: capSub((u.legacy ? '旧格式 · ' : '') + names.join('、')) }
}

/** 同上，NCE2 听力版：「🎧 听力一组 · N 课 · 摊法 · 跟读/精听」，小字=课号+课名。 */
export function nce2UnitLine(unit, titles) {
  const u = unit || {}
  const lessons = (u.lessons || []).map(Number)
  const names = lessons.map((n) => {
    const t = unitTitle(titles, n)
    return t ? `第${n}课 ${String(t).slice(0, 16)}` : `第${n}课`
  })
  const head = [
    '🎧 听力一组',
    `${lessons.length} 课`,
    MODE_TEXT[normMode(u.mode)],
    NCE2_TASK_TEXT[u.task_mode === 'listen' ? 'listen' : 'shadow'],
  ].join(' · ')
  return { head, sub: capSub(names.join('、')) }
}

function capSub(s) {
  return s.length > 56 ? s.slice(0, 56) + '…' : s
}

/**
 * 本周新掌握 N 个，写成进度条认得的字段。
 * 周进度分母 = vocab_goal − 周初该级已会，所以 vocab_goal = base + N。
 * vocab_pct 只留给旧数据回看，界面不再让老师填百分比。
 */
export function applyVocabTarget(tasks, { level, count, base, words }) {
  const t = copyTasks(tasks)
  const n = Math.max(0, Math.round(Number(count) || 0))
  if (!n) {
    delete t.vocab_lv
    delete t.vocab_pct
    delete t.vocab_goal
    delete t.vocab_new
    return t
  }
  const b = Math.max(0, Math.round(Number(base) || 0))
  const w = Number(words) || 0
  const goal = b + n
  const pct = w > 0 ? Math.min(100, Math.max(1, Math.round((goal / w) * 100))) : 100
  t.vocab_lv = Number(level) || 0
  t.vocab_pct = pct
  t.vocab_goal = goal
  t.vocab_new = n
  return t
}

/** 周初该级已会词数（进度条分母的基数）。 */
function vocabBaseFor(tasks, vocabWeek) {
  if (tasks?.vocab_lv == null) return Number(vocabWeek?.base_all) || 0
  const row = (vocabWeek?.levels || []).find((x) => x.level === Number(tasks.vocab_lv))
  return Number(row?.base) || 0
}

export function vocabRowTitle(tasks, vocabWeek) {
  if (tasks?.vocab_goal == null) return ''
  const base = vocabBaseFor(tasks, vocabWeek)
  const goal = Number(tasks.vocab_goal)
  const lv = tasks.vocab_lv
  if (base >= goal) {
    return lv == null
      ? '词汇：周初已经达到目标，这周进度算满格'
      : `词汇：L${lv} 周初已经达到目标，这周进度算满格`
  }
  const n = goal - base
  return lv == null ? `本周新掌握 ${n} 个词` : `本周新掌握 L${lv} 词 ${n} 个`
}

export function completionLine(type, { topicCounts = true, mode = '', count = 0, taskMode = '' } = {}) {
  if (type === 'lecture') {
    return topicCounts
      ? '怎么算完成：这讲有「课程测验」成绩，或这讲专题的知识点都学完。'
      : '怎么算完成：这讲有「课程测验」成绩。专题学完目前不算（规则关着）。'
  }
  if (type === 'hw_paper') {
    return '怎么算完成：这讲单独有一条「作业卷」成绩。上了课、做了课程测验，都不算作业做完。'
  }
  if (type === 'focus_kp') return '攻坚标注：不参与完成判定，供周验收对照，微练习优先出这些讲的题。'
  if (type === 'vocab') {
    return '怎么算完成：本周在这一级新掌握的词达到这个个数。周初已经会的不算进这周。工作日「背词 n 词」也按当天新掌握打勾，不按练过多少词。这里选的级别只决定新词记到哪一级，不管学生能不能看见那一级。'
  }
  if (type === 'fce') return '怎么算完成：本周交了这一道 FCE。'
  if (type === 'article') {
    const one = Number(count) === 1
    if (mode === 'weekend') {
      return one
        ? '怎么算完成：这一篇交了朗读录音。周六精读+朗读 1 篇。'
        : '怎么算完成：本组每篇都交了朗读录音。周六一起精读+朗读。'
    }
    return one
      ? '怎么算完成：这一篇交了朗读录音。工作日精读+朗读 1 篇。'
      : '怎么算完成：每篇交了朗读录音。工作日每天精读+朗读 1 篇。'
  }
  if (type === 'video') {
    return mode === 'weekend'
      ? '怎么算完成：看完并交了复述录音。周六一次看 2 个。'
      : '怎么算完成：看完并交了复述录音。工作日每天摊 1 个。'
  }
  if (type === 'nce2') {
    const how = taskMode === 'listen' ? '交了精听记录' : '交了跟读录音'
    return mode === 'weekend'
      ? `怎么算完成：这组每课都${how}，周末一次做完。`
      : `怎么算完成：这组每课都${how}，工作日每天摊 1 课。`
  }
  if (type === 'reading') return '怎么算完成：泛读进度读完选中的章节。'
  if (type === 'paper') return '怎么算完成：词汇考试里登记了这一卷的成绩。'
  if (type === 'micro') return '怎么算完成：本周每个工作日做 3 题，不做不扣分。'
  return ''
}

function numSet(arr) {
  return new Set((arr || []).map((x) => Number(x)))
}

function sameNums(a, b) {
  const left = numSet(a)
  const right = numSet(b)
  return left.size === right.size && [...left].every((n) => right.has(n))
}

/**
 * 课和作业讲次对不上时给黄条，不拦保存。
 * 最近几周若总是同一讲，补一句习惯，方便老师发现手滑（课是 30、卷是 28）。
 */
export function hwMismatchWarning(tasks, recentWeeks, weekStart) {
  const lecs = numSet(tasks?.lectures)
  const hws = numSet(tasks?.hw_papers)
  if (!lecs.size || !hws.size) return ''
  const lecOnly = [...lecs].filter((n) => !hws.has(n)).sort((a, b) => a - b)
  const hwOnly = [...hws].filter((n) => !lecs.has(n)).sort((a, b) => a - b)
  if (!lecOnly.length && !hwOnly.length) return ''
  const prior = (recentWeeks || [])
    .filter((w) => w && w.week_start && w.week_start !== weekStart && w.week_start < weekStart)
    .filter((w) => (w.tasks?.lectures || []).length && (w.tasks?.hw_papers || []).length)
    .slice(-4)
  let habit = ''
  if (prior.length >= 2) {
    const aligned = prior.filter((w) => sameNums(w.tasks.lectures, w.tasks.hw_papers)).length
    if (aligned >= Math.ceil(prior.length * 0.6)) {
      habit = '最近几周你都是语法课和作业卷同一讲。'
    }
  }
  const bits = []
  if (lecOnly.length) bits.push(`语法课有第 ${lecOnly.join('、')} 讲，作业卷里没有`)
  if (hwOnly.length) bits.push(`作业卷有第 ${hwOnly.join('、')} 讲，这周语法课里没有`)
  return `${habit}${bits.join('；')}。课程看「课程测验」或专题学完，作业卷要单独登记「作业卷」成绩，两边不是一回事。可以保存，建议再对一下。`
}

export function appendPlanItem(tasks, item) {
  const t = copyTasks(tasks)
  switch (item.type) {
    case 'lecture':
      t.lectures = pushUnique(t.lectures, item.lecture, true)
      break
    case 'hw_paper':
      t.hw_papers = pushUnique(t.hw_papers, item.lecture, true)
      break
    case 'focus_kp':
      t.focus_kps = pushUnique(t.focus_kps, item.lecture, true)
      break
    case 'fce':
      t.fce = pushUnique(t.fce, item.label, false)
      break
    case 'paper':
      t.vocab_papers = pushUnique(t.vocab_papers, item.paper_id, false)
      break
    case 'vocab':
      if (item.newCount != null) {
        return applyVocabTarget(t, {
          level: item.level,
          count: item.newCount,
          base: item.base,
          words: item.words,
        })
      }
      t.vocab_lv = item.level
      t.vocab_pct = item.pct
      t.vocab_goal = item.goal
      delete t.vocab_new
      break
    case 'speak':
      // 旧入口：编辑器已不再单独加朗读。保存时 tasksForSave 会丢掉 speak。
      t.speak = item.n
      break
    case 'reading': {
      const reading = { ...(t.reading || {}) }
      reading[String(item.book_id)] = [...item.chapters]
      t.reading = reading
      break
    }
    default:
      // 精读/视频走 appendArticleUnit / appendVideoUnit（2 篇/个一组），不再走单项。
      throw new Error(`未知计划项：${item.type}`)
  }
  return t
}

export function removePlanItem(tasks, item) {
  const t = copyTasks(tasks)
  switch (item.type) {
    case 'lecture':
      dropNum(t, 'lectures', item.lecture)
      break
    case 'hw_paper':
      dropNum(t, 'hw_papers', item.lecture)
      break
    case 'focus_kp':
      dropNum(t, 'focus_kps', item.lecture)
      break
    case 'fce':
      dropStr(t, 'fce', item.label)
      break
    case 'paper':
      dropStr(t, 'vocab_papers', item.paper_id)
      break
    case 'reading': {
      const reading = { ...(t.reading || {}) }
      delete reading[String(item.book_id)]
      if (Object.keys(reading).length) t.reading = reading
      else delete t.reading
      break
    }
    case 'vocab':
      delete t.vocab_lv
      delete t.vocab_pct
      delete t.vocab_goal
      delete t.vocab_new
      break
    case 'speak':
      delete t.speak
      break
    default:
      // 精读/视频组走 removeArticleUnit / removeVideoUnit（整组删）。
      throw new Error(`未知计划项：${item.type}`)
  }
  return t
}

export const PLAN_ADD_TYPES = [
  { id: 'lecture', label: '语法课程', hint: '本周要上的讲，工作日摊开' },
  { id: 'hw_paper', label: '作业卷', hint: '按讲次，登记作业卷成绩才算完成' },
  { id: 'vocab', label: '词汇目标', hint: '本周在某一级新掌握多少个词' },
  { id: 'video', label: '视频学习', hint: '2 个一组，选摊法' },
  { id: 'nce2', label: '🎧 NCE2 听力', hint: '1–5 课一组，选摊法与跟读/精听' },
  { id: 'reading', label: '泛读书目', hint: '按章加入' },
  { id: 'fce', label: 'FCE 大题', hint: '周六做' },
  { id: 'article', label: '精读+朗读', hint: '1–2 篇一组，勾上就含朗读；工作日摊开或周末集中' },
  { id: 'paper', label: '单词试卷', hint: '周日做卷' },
  { id: 'micro', label: '微练习', hint: '一组=本周每日 3 题，不做不扣分' },
  { id: 'focus_kp', label: '攻坚讲次', hint: '标注本周攻坚讲次：周验收对照，微练习优先出这些讲的题' },
]

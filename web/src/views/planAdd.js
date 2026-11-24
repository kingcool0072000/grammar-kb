/**
 * 周计划清单的增删。tasks 仍是 plan_weeks 那份 JSON。
 * 视频按组存 video_units（每组 2 个）。
 * NCE2 听力按组存 nce2_units（每组 1–5 课，完成方式 task_mode：
 * shadow=听力跟读（缺省）、listen=仅精听）。旧的 mode 若还在 JSON 里就原样留下，不再用来区分工作日和周末。
 * 微练习用 tasks.micro = 1 表示本周启用；旧扁平键 articles/videos 不再写。
 * 历史精读 article_units 保存时原样留下，编辑器不再新增。
 * 旧的 tasks.speak（单独「朗读篇数」）保存时丢掉。
 */

function keptMode(row, src, keepLegacy = false) {
  // 旧 JSON 里的 daily/weekend 原样留下，排课不再读。新组不补这个字段。
  // legacy 小标只在编辑过程里用，保存时丢掉。
  if (src?.mode === 'weekend' || src?.mode === 'daily') row.mode = src.mode
  if (keepLegacy && src?.legacy) row.legacy = true
  return row
}

function copyTasks(tasks) {
  const t = { ...(tasks || {}) }
  if (Array.isArray(t.lectures)) t.lectures = [...t.lectures]
  if (Array.isArray(t.hw_papers)) t.hw_papers = [...t.hw_papers]
  if (Array.isArray(t.focus_kps)) t.focus_kps = [...t.focus_kps]
  if (Array.isArray(t.fce)) t.fce = [...t.fce]
  if (Array.isArray(t.vocab_papers)) t.vocab_papers = [...t.vocab_papers]
  if (Array.isArray(t.article_units)) {
    t.article_units = t.article_units.map((u) => keptMode(
      { ids: (u?.ids || []).map(Number) }, u, true))
  }
  if (Array.isArray(t.video_units)) {
    t.video_units = t.video_units.map((u) => keptMode(
      { vids: (u?.vids || []).map(String) }, u, true))
  }
  if (Array.isArray(t.nce2_units)) {
    t.nce2_units = t.nce2_units.map((u) => keptMode({
      lessons: (u?.lessons || []).map(Number),
      task_mode: u?.task_mode === 'listen' ? 'listen' : 'shadow',
    }, u, true))
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
 * （旧精读、旧视频先转成一组，带 legacy 小标；mode 只是历史字段），
 * 旧键就地删掉，之后编辑与保存都只见新结构。不改入参。
 */
export function migrateLegacyUnits(tasks) {
  const t = { ...(tasks || {}) }
  const arts = [...new Set((Array.isArray(t.articles) ? t.articles : []).map(Number).filter((n) => Number.isFinite(n)))]
  const vids = [...new Set((Array.isArray(t.videos) ? t.videos : []).map(String).filter(Boolean))]
  if (arts.length) {
    t.article_units = [
      ...(Array.isArray(t.article_units) ? t.article_units : []),
      { ids: arts.sort((a, b) => a - b), legacy: true },
    ]
  }
  if (vids.length) {
    t.video_units = [
      ...(Array.isArray(t.video_units) ? t.video_units : []),
      { vids, legacy: true },
    ]
  }
  delete t.articles
  delete t.videos
  return t
}

/** 保存/预览用的形状：只留契约字段，编辑期的 legacy 小标不落库。
 *  历史 article_units 原样保留（不再排课）。不再单独落 speak。 */
export function tasksForSave(tasks) {
  const t = copyTasks(tasks)
  if (Array.isArray(t.article_units)) {
    t.article_units = t.article_units.map((u) => keptMode({ ids: u.ids }, u))
  }
  if (Array.isArray(t.video_units)) {
    t.video_units = t.video_units.map((u) => keptMode({ vids: u.vids }, u))
  }
  if (Array.isArray(t.nce2_units)) {
    t.nce2_units = t.nce2_units.map((u) => keptMode({
      lessons: u.lessons, task_mode: u.task_mode === 'listen' ? 'listen' : 'shadow',
    }, u))
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

// ---- 视频组（video_units）与微练习（micro）----

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

/** 视频加一组：vids 必须正好 2 个；组内和本周已有的不重复。不再写工作日/周末摊法。 */
export function appendVideoUnit(tasks, vids) {
  const t = copyTasks(tasks)
  const list = [...new Set((vids || []).map(String))]
  if (list.length !== 2) return t
  const taken = videoUnitVids(t)
  if (list.some((v) => taken.includes(v))) return t
  t.video_units = [...(t.video_units || []), { vids: list }]
  return t
}

/** NCE2 听力加一组：1–5 课；防重跨组同课；lessons 升序；task_mode 缺省 shadow（跟读）。 */
export function appendNce2Unit(tasks, lessonIds, _mode, taskMode) {
  const t = copyTasks(tasks)
  const list = [...new Set((lessonIds || []).map(Number).filter((n) => Number.isFinite(n)))]
    .sort((a, b) => a - b)
  if (list.length < 1 || list.length > 5) return t
  const taken = nce2UnitLessons(t)
  if (list.some((n) => taken.includes(n))) return t
  t.nce2_units = [
    ...(t.nce2_units || []),
    { lessons: list, task_mode: taskMode === 'listen' ? 'listen' : 'shadow' },
  ]
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

const NCE2_TASK_TEXT = { shadow: '跟读', listen: '精听' }

function unitTitle(titles, key) {
  if (!titles) return ''
  const v = typeof titles.get === 'function' ? titles.get(String(key)) : titles[String(key)]
  return v == null ? '' : String(v)
}

/** 清单行文案：「🎬 视频一组 · 2 个」。 */
export function videoUnitLine(unit, titles) {
  const u = unit || {}
  const vids = (u.vids || []).map(String)
  const names = vids.map((v) => (unitTitle(titles, v) || v).slice(0, 20))
  const head = ['🎬 视频一组', `${vids.length} 个`].join(' · ')
  return { head, sub: capSub((u.legacy ? '旧格式 · ' : '') + names.join('、')) }
}

/** 同上，NCE2 听力版：「🎧 听力一组 · N 课 · 跟读/精听」，小字=课号+课名。 */
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

/**
 * 词汇行默认选哪一级：已经选过就不动 → 孩子最近在背的级别 → 第一个还有词
 * 没掌握的级别。
 * 不能退到「第一个有词的级别」：那是 0 级，而 0 级多半早就背完了。选它，
 * 这一周的「本周新掌握」永远是 0，孩子背的词全不算进度（2026-11-23 那周）。
 */
export function defaultVocabLevel(tasks, vocabWeek, vLevels = []) {
  if (tasks?.vocab_lv != null) return Number(tasks.vocab_lv)
  const practicing = vocabWeek?.practicing_level
  if (practicing != null && practicing !== '') return Number(practicing)
  const stats = vocabWeek?.levels?.length ? vocabWeek.levels : vLevels
  const withWords = stats.find((x) => Number(x.words) > 0)
  if (!withWords) return vLevels[0]?.level ?? 0
  // base（周初已会）只有带周次取数时才有；没有就不猜，照旧取第一个有词的
  const open = withWords.base != null
    ? stats.find((x) => Number(x.words) > 0 && Number(x.base || 0) < Number(x.words))
    : null
  return (open || withWords).level
}

/**
 * 词汇这一行选了孩子已经背完的级别：给红条，保存前再让老师确认。
 * 只在老师真要「本周新掌握」时管（vocab_new > 0）。
 */
export function vocabLevelWarning(tasks, vocabWeek) {
  const lv = tasks?.vocab_lv
  if (lv == null || !(Number(tasks.vocab_new) > 0)) return ''
  const s = (vocabWeek?.levels || []).find((x) => Number(x.level) === Number(lv))
  if (!s || !(Number(s.words) > 0)) return ''
  if (Number(s.words) - Number(s.base || 0) > 0) return ''
  const practicing = vocabWeek?.practicing_level
  const hint = practicing != null && Number(practicing) !== Number(lv)
    ? `孩子最近在背 L${practicing}。`
    : ''
  return `词汇这一行选的是 L${lv}。这一级一共 ${s.words} 个词，周初已经全部掌握，`
    + `选它这周的「本周新掌握」会一直是 0，孩子背的词不算进度。${hint}`
    + '请换成孩子正在背的级别。'
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
    return '怎么算完成：本周在这一级新掌握的词达到这个个数，周一到周日都算。周初已经会的不算进这周，下一周才掌握的词也不挪回来。过了周日夜里，这一周的个数就定了。这里选的级别只决定新词记到哪一级。'
  }
  if (type === 'fce') return '怎么算完成：这一周里交了这一道 FCE 就记在这一周。过了周日夜里再交，记在下一周。'
  if (type === 'video') {
    return '怎么算完成：这一周里看完并交了复述录音就记在这一周。过了周日夜里再交，记在下一周。'
  }
  if (type === 'nce2') {
    const how = taskMode === 'listen' ? '交了精听记录' : '交了跟读录音'
    return `怎么算完成：这一周里这组每课都${how}就记在这一周。过了周日夜里再交，记在下一周。`
  }
  if (type === 'reading') return '怎么算完成：这一周结束前读完选中的章节。之后再读的进度记在后面的周。'
  if (type === 'paper') return '怎么算完成：这一周里在词汇考试登记了这一卷的成绩。过了周日夜里再登记，记在下一周。'
  if (type === 'micro') return '怎么算完成：周一到周日各打一次卡（每天 3 题）。过了北京时间当天不能补，漏打就留着。'
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
      throw new Error(`未知计划项：${item.type}`)
  }
  return t
}

export const PLAN_ADD_TYPES = [
  { id: 'lecture', label: '语法课程', hint: '排进本周，哪天学完都算' },
  { id: 'hw_paper', label: '作业卷', hint: '按讲次，登记作业卷成绩才算完成' },
  { id: 'vocab', label: '词汇目标', hint: '本周在某一级新掌握多少个词' },
  { id: 'video', label: '视频学习', hint: '2 个一组；这一周哪天看完都算' },
  { id: 'nce2', label: '🎧 NCE2 听力', hint: '1–5 课一组；这一周哪天做完都算' },
  { id: 'reading', label: '泛读书目', hint: '按章加入，这一周读完即可' },
  { id: 'fce', label: 'FCE 大题', hint: '排进本周，这一周哪天交都算' },
  { id: 'paper', label: '单词试卷', hint: '排进本周，这一周哪天登记都算' },
  { id: 'micro', label: '微练习', hint: '周一到周日各打一次卡，过了当天不能补' },
  { id: 'focus_kp', label: '攻坚讲次', hint: '标注本周攻坚讲次：周验收对照，微练习优先出这些讲的题' },
]

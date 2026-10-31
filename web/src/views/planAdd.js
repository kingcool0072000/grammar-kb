/** 周计划清单的增删。tasks 仍是 plan_weeks 那份 JSON，不改键名。 */

function copyTasks(tasks) {
  const t = { ...(tasks || {}) }
  if (Array.isArray(t.lectures)) t.lectures = [...t.lectures]
  if (Array.isArray(t.hw_papers)) t.hw_papers = [...t.hw_papers]
  if (Array.isArray(t.focus_kps)) t.focus_kps = [...t.focus_kps]
  if (Array.isArray(t.articles)) t.articles = [...t.articles]
  if (Array.isArray(t.fce)) t.fce = [...t.fce]
  if (Array.isArray(t.vocab_papers)) t.vocab_papers = [...t.vocab_papers]
  if (Array.isArray(t.videos)) t.videos = [...t.videos]
  if (t.reading && typeof t.reading === 'object') t.reading = { ...t.reading }
  return t
}

export { copyTasks }

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

/** 进度条上的「本周要新掌握」：目标词数减去周初已会。够了就是 0。 */
export function weeklyNewCount(tasks, base) {
  if (tasks?.vocab_goal == null || tasks.vocab_goal === '') return null
  const goal = Number(tasks.vocab_goal)
  if (!Number.isFinite(goal)) return null
  const b = Math.max(0, Math.round(Number(base) || 0))
  return Math.max(0, goal - b)
}

export function vocabBaseFor(tasks, vocabWeek) {
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

export function completionLine(type, { topicCounts = true } = {}) {
  if (type === 'lecture') {
    return topicCounts
      ? '怎么算完成：这讲有「课程测验」成绩，或这讲专题的知识点都学完。'
      : '怎么算完成：这讲有「课程测验」成绩。专题学完目前不算（规则关着）。'
  }
  if (type === 'hw_paper') {
    return '怎么算完成：这讲单独有一条「作业卷」成绩。上了课、做了课程测验，都不算作业做完。'
  }
  if (type === 'focus_kp') return '怎么算完成：当天这讲有练习成绩。'
  if (type === 'vocab') {
    return '怎么算完成：本周在这一级新掌握的词达到这个个数。周初已经会的不算进这周。'
  }
  if (type === 'fce') return '怎么算完成：本周交了这一道 FCE。'
  if (type === 'article') return '怎么算完成：这篇文章交了朗读录音。'
  if (type === 'video') return '怎么算完成：看完并交了复述录音。工作日每天摊 1 个。'
  if (type === 'reading') return '怎么算完成：泛读进度读完选中的章节。'
  if (type === 'paper') return '怎么算完成：词汇考试里登记了这一卷的成绩。'
  if (type === 'speak') return '怎么算完成：本周交够这么多篇朗读。课表上主要跟着精读课文走。'
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
    case 'article':
      t.articles = pushUnique(t.articles, item.article_id, true)
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
      t.speak = item.n
      break
    case 'reading': {
      const reading = { ...(t.reading || {}) }
      reading[String(item.book_id)] = [...item.chapters]
      t.reading = reading
      break
    }
    case 'video':
      t.videos = pushUnique(t.videos, item.vid, false)
      break
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
    case 'article':
      dropNum(t, 'articles', item.article_id)
      break
    case 'fce':
      dropStr(t, 'fce', item.label)
      break
    case 'paper':
      dropStr(t, 'vocab_papers', item.paper_id)
      break
    case 'video':
      dropStr(t, 'videos', item.vid)
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
  { id: 'lecture', label: '语法课程', hint: '本周要上的讲，工作日摊开' },
  { id: 'hw_paper', label: '作业卷', hint: '按讲次，登记作业卷成绩才算完成' },
  { id: 'vocab', label: '词汇目标', hint: '本周在某一级新掌握多少个词' },
  { id: 'video', label: '视频学习', hint: '看视频加复述，工作日每天 1 个' },
  { id: 'reading', label: '泛读书目', hint: '按章加入' },
  { id: 'fce', label: 'FCE 大题', hint: '周六做' },
  { id: 'article', label: '精读课文', hint: '交了朗读才算完成' },
  { id: 'paper', label: '单词试卷', hint: '周日做卷' },
  { id: 'speak', label: '朗读篇数', hint: '本周安排几篇录音' },
  { id: 'focus_kp', label: '微练习', hint: '工作日攻坚，每讲 5 题' },
]

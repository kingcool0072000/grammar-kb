/** 把向导的一项并进本周 tasks。不改动其它键，已存在的标量项覆盖，列表项去重。 */

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

function pushUnique(arr, value, sortNum) {
  const next = [...(arr || [])]
  if (!next.includes(value)) next.push(value)
  if (sortNum) next.sort((a, b) => a - b)
  return next
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
      t.vocab_lv = item.level
      t.vocab_pct = item.pct
      t.vocab_goal = item.goal
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

export const PLAN_ADD_TYPES = [
  { id: 'lecture', label: '语法课程', hint: '本周讲次，工作日均摊' },
  { id: 'hw_paper', label: '作业卷', hint: '按讲次配套，登记成绩后勾完' },
  { id: 'focus_kp', label: '微练习', hint: '工作日攻坚，每讲 5 题' },
  { id: 'vocab', label: '词汇目标', hint: '级别 × 百分比，覆盖本周词汇目标' },
  { id: 'fce', label: 'FCE 大题', hint: '周六输出型任务' },
  { id: 'article', label: '精读课文', hint: '提交朗读后自动勾完' },
  { id: 'video', label: '视频学习', hint: '看视频+复述，工作日每天 1 个' },
  { id: 'reading', label: '泛读书目', hint: '按章加入，覆盖该书已选章' },
  { id: 'paper', label: '单词试卷', hint: '周日试卷任务' },
  { id: 'speak', label: '朗读篇数', hint: '本周安排 N 篇录音' },
]

import { routeSegs } from './dom.js'

// 教师壳只露出三个 Tab。子工具页走 hash，不进顶部导航。
export const TEACHER_TABS = [
  { key: 'prep', label: '备课中心' },
  { key: 'plan', label: '计划表' },
  { key: 'grading', label: '批改中心' },
]

// 学生壳。已攻克词本从背单词进入，不占 Tab。
export const STUDENT_TABS = [
  { key: 'timetable', label: '课程表' },
  { key: 'recite', label: '背单词' },
  { key: 'reading', label: '阅读练习' },
  { key: 'topics', label: '专题学习' },
  { key: 'library', label: '泛读馆' },
  { key: 'fcePapers', label: 'FCE真题' },
]

const TEACHER_ALLOWED = new Set([
  ...TEACHER_TABS.map((t) => t.key),
  // 备课 / 批改 / 计划跳进来的工具页
  'courses', 'vocab', 'taxonomy', 'fce', 'fceListen', 'exams', 'vocabLevel',
  'students', 'paperAdmin', 'readingAdmin', 'readingList', 'topicsAdmin',
  'libraryManage', 'librarySettings',
  // 师生都能开的隐藏页（教师从卡片进专题、泛读馆）
  'topics', 'library', 'libraryReader', 'fcePapers', 'config', 'conquered',
])

const STUDENT_ALLOWED = new Set([
  ...STUDENT_TABS.map((t) => t.key),
  'conquered', 'config', 'libraryReader',
])

/** 旧 hash 归一。会改 location（与拆壳前 currentRoute 相同）。 */
export function normalizeKey() {
  const [v] = routeSegs()
  let key = v === 'ttsconf' || v === 'readconf' ? 'config' : v
  if (!key) return ''
  if (key === 'today') {
    location.replace('#/recite')
    key = 'recite'
  }
  if (key === 'vocabExam') {
    location.replace('#/vocabLevel/0')
    key = 'vocabLevel'
  }
  if (key === 'analytics') {
    location.replace('#/grading?tab=analytics')
    key = 'grading'
  }
  if (key === 'plan' && /(?:^|&)tab=analytics(?:&|$)/.test(location.hash.split('?')[1] || '')) {
    location.replace('#/grading?tab=analytics')
    key = 'grading'
  }
  return key
}

export function resolveRoute(role) {
  const key = normalizeKey()
  const allowed = role === 'teacher' ? TEACHER_ALLOWED : STUDENT_ALLOWED
  if (!allowed.has(key)) return role === 'teacher' ? 'prep' : 'recite'
  return key
}

export function tabsFor(role) {
  return role === 'teacher' ? TEACHER_TABS : STUDENT_TABS
}

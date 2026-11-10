import { routeSegs } from './dom.js'

// 教师壳只露出两个 Tab（批改闭环已按学科并入备课中心）。子工具页走 hash，不进顶部导航。
export const TEACHER_TABS = [
  { key: 'prep', label: '备课中心' },
  { key: 'plan', label: '计划表' },
]

// 学生壳。已攻克词本从背单词进入，不占 Tab。
export const STUDENT_TABS = [
  { key: 'timetable', label: '课程表' },
  { key: 'recite', label: '背单词' },
  { key: 'reading', label: '阅读练习' },
  { key: 'micro', label: '微练习' },
  { key: 'topics', label: '专题学习' },
  { key: 'library', label: '泛读馆' },
  { key: 'videoStudy', label: '视频学习' },
  { key: 'fcePapers', label: 'FCE真题' },
]

const TEACHER_ALLOWED = new Set([
  ...TEACHER_TABS.map((t) => t.key),
  // 批改中心不再进顶栏，但保留路由（学情分析/学习日志入口 + 旧链接防 404）
  'grading',
  // 备课 / 批改 / 计划跳进来的工具页
  'courses', 'taxonomy', 'fceListen', 'exams', 'vocabLevel', 'vocabExam',
  'students', 'paperAdmin', 'readingAdmin', 'readingList', 'topicsAdmin',
  'videoAdmin',
  'librarySettings',
  // 微练习题库管理 + 完成度 + 句型素材（教师专属，不进顶部 Tab）
  'microAdmin', 'microProgress', 'microPatterns',
  // 师生都能开的隐藏页（教师从卡片进专题、泛读馆）
  'topics', 'library', 'libraryReader', 'fcePapers', 'config', 'conquered',
  'videoStudy',
])

const STUDENT_ALLOWED = new Set([
  ...STUDENT_TABS.map((t) => t.key),
  // 课程表微练习入口卡进的隐藏页
  'conquered', 'config', 'libraryReader', 'micro',
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
  if (key === 'analytics') {
    location.replace('#/grading?tab=analytics')
    key = 'grading'
  }
  if (key === 'plan' && /(?:^|&)tab=analytics(?:&|$)/.test(location.hash.split('?')[1] || '')) {
    location.replace('#/grading?tab=analytics')
    key = 'grading'
  }
  // 批改看板已并入备课中心：#/grading（无 tab 或 tab=board）→ 备课中心；
  // tab=log / tab=analytics 保持原样（学情与日志仍走独立批改页）。
  if (key === 'grading') {
    const tab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab')
    if (tab !== 'log' && tab !== 'analytics') {
      location.replace('#/prep')
      key = 'prep'
    }
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

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
  { key: 'micro', label: '微练习' },
  { key: 'topics', label: '专题学习' },
  { key: 'library', label: '泛读馆' },
  { key: 'videoStudy', label: '视频学习' },
  { key: 'nce2', label: '听力跟读' },
  { key: 'fcePapers', label: 'FCE真题' },
]

const TEACHER_ALLOWED = new Set([
  ...TEACHER_TABS.map((t) => t.key),
  // 批改中心不再进顶栏，但保留路由（学情分析/学习日志入口 + 旧链接防 404）
  'grading',
  // 备课 / 批改 / 计划跳进来的工具页
  'courses', 'taxonomy', 'fceListen', 'exams', 'vocabLevel', 'vocabExam',
  'students', 'paperAdmin', 'topicsAdmin',
  'videoAdmin', 'videoReview', 'nce2Review',
  'librarySettings',
  // 微练习题库管理 + 完成度 + 句型素材（教师专属，不进顶部 Tab）
  'microAdmin', 'microProgress', 'microPatterns',
  // 师生都能开的隐藏页（教师从卡片进专题、泛读馆）
  'topics', 'library', 'libraryReader', 'fcePapers', 'config', 'conquered',
  'videoStudy',
  // NCE2 听力跟读：教师打开 = 全 96 课只读预览（同 videoStudy 模式）
  'nce2',
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

// 精读（阅读练习）已结束。旧书签不再打开那个页，教师回备课中心，学生回课程表。
const RETIRED_INTENSIVE = new Set(['reading', 'readingAdmin', 'readingList'])

// 旧书签：仍是学生主动打开的页，登录后不要改写成课程表。
const STUDENT_DEEP_ALIASES = new Set(['today', 'ttsconf', 'readconf'])

/** 无深链时的首页。教师备课中心，学生课程表。 */
export function homeRoute(role) {
  return role === 'teacher' ? 'prep' : 'timetable'
}

function hashKey(hash) {
  return String(hash || '').replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean)[0] || ''
}

/** 学生地址是不是指定页面（#/recite、#/topics/… 等）。空 hash 和登录页不是。 */
export function isStudentDeepLink(hash) {
  const key = hashKey(hash)
  return STUDENT_ALLOWED.has(key) || STUDENT_DEEP_ALIASES.has(key)
}

/**
 * 登录成功后留在哪个 hash。
 * 教师原样保留（默认仍由 resolveRoute 进备课中心）。
 * 学生没有深链时去 #/timetable；打开了具体页就还去那一页。
 */
export function hashAfterLogin(role, hash) {
  const current = hash == null ? '' : String(hash)
  if (role === 'teacher') return current
  if (isStudentDeepLink(current)) return current
  return '#/timetable'
}

export function resolveRoute(role) {
  const key = normalizeKey()
  if (RETIRED_INTENSIVE.has(key)) {
    const dest = homeRoute(role)
    const next = `#/${dest}`
    if (location.hash !== next) location.replace(next)
    return dest
  }
  const allowed = role === 'teacher' ? TEACHER_ALLOWED : STUDENT_ALLOWED
  // 空 hash、登录页、角色打不开的地址：回到该角色首页。
  // 学生显式深链（#/recite 等）在上面已经命中 allowed，不会进这里。
  if (!allowed.has(key)) return homeRoute(role)
  return key
}

export function tabsFor(role) {
  return role === 'teacher' ? TEACHER_TABS : STUDENT_TABS
}

import './styles.css'
import { api, fetchAllPoints, getAuth, setAuth } from './api.js'
import { mountCourses } from './views/courses.js'
import { mountVocabulary } from './views/vocabulary.js'
import { mountRecite } from './views/recite.js'
import { mountTaxonomy } from './views/taxonomy.js'
import { mountExams } from './views/exams.js'
import { mountFce } from './views/fce.js'
import { mountFcePapers } from './views/fcePapers.js'
import { mountReading } from './views/reading.js'
import { mountReadingAdmin } from './views/readingAdmin.js'
import { mountReadingList } from './views/readingList.js'
import { mountFceListen } from './views/fceListen.js'
import { mountTimetable } from './views/timetable.js'
import { mountLibraryShelf } from './views/library/shelf.js'
import { mountLibraryReader } from './views/library/reader.js'
import { mountLibraryManage } from './views/library/manage.js'
import { mountLibrarySettings } from './views/library/settings.js'
import { mountGrading } from './views/grading.js'
import { mountPrep } from './views/prep.js'
// 学情分析由批改中心动态 import('./analytics.js')；topics 手册（大静态
// 内容块）同样不静态引入（避免拖大首包）——两者都在路由分支动态 import
import { mountPlan } from './views/plan.js'
import { mountVocabExam } from './views/vocabExam.js'
import { mountStudents } from './views/students.js'
import { mountPaperAdmin } from './views/paperAdmin.js'
import { mountVocabLevel } from './views/vocabLevel.js'
import { mountConfig } from './views/config.js'
import { mountLogin } from './views/login.js'
import { createDrawer } from './components/drawer.js'

// 专注模式：禁正文右键系统菜单（输入框保留）；查词交互只走我们的浮层
document.addEventListener('contextmenu', (e) => {
  const t = e.target
  const editable = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
  if (!editable) e.preventDefault()
})

const app = document.getElementById('app')

// teacher: true 仅教师可见；studentOnly: true 仅学生可见
// 教师版三大 Tab：备课中心 / 计划表 / 批改中心（默认落地备课中心）；
// 子工具页（courses/vocab/taxonomy/fce/exams/readingAdmin）不进 Tab，
// 由备课/批改中心跳转进入。
const VIEWS = [
  // 教师版三 Tab：备课中心 / 计划表 / 批改中心（泛读馆入口收进备课中心）
  { key: 'prep', label: '备课中心', teacher: true },
  // 学情分析并入计划表（#/plan?tab=analytics）；旧 hash 重定向
  { key: 'plan', label: '计划表', teacher: true },
  { key: 'grading', label: '批改中心', teacher: true },
  // 学生课程表排第一位：教师周计划的只读镜像（仅看自己本周）
  { key: 'timetable', label: '课程表', studentOnly: true },
  { key: 'recite', label: '背单词', studentOnly: true },
  { key: 'reading', label: '阅读练习', studentOnly: true },
  // 专题学习：学生 Tab；教师从备课中心大卡片进入（hiddenTab=教师不进顶部 Tab）
  { key: 'topics', label: '专题学习', studentOnly: true, hiddenTab: true },
  // hiddenTab 语义：教师不进 Tab、学生仍显示（学生读书主入口）
  { key: 'library', label: '泛读馆', hiddenTab: true },
  // 子工具页（不在 Tab 显示，hash 直达）：fcePapers 师生共用
  { key: 'fcePapers', label: 'FCE真题', hiddenTab: true },
  // 设置中心：隐藏配置页 #/config（发音 + 阅读排版 + 泛读馆宽度）。
  // 注意 hiddenTab 的语义是「教师不进 Tab、学生仍显示」（FCE真题即如此），
  // 真隐藏须用 noTab。旧 #/ttsconf #/readconf 由路由重定向进来。
  { key: 'config', label: '设置中心', hiddenTab: true, noTab: true },
  { key: 'courses', hiddenTab: true, teacher: true },
  { key: 'vocab', hiddenTab: true, teacher: true },
  { key: 'taxonomy', hiddenTab: true, teacher: true },
  { key: 'fce', hiddenTab: true, teacher: true },
  // FCE 听力播放台（教师，备课中心进入）
  { key: 'fceListen', hiddenTab: true, teacher: true },
  { key: 'exams', hiddenTab: true, teacher: true },
  { key: 'vocabExam', hiddenTab: true, teacher: true },
  { key: 'students', hiddenTab: true, teacher: true },
  { key: 'paperAdmin', hiddenTab: true, teacher: true },
  { key: 'vocabLevel', hiddenTab: true, teacher: true },
  { key: 'readingAdmin', hiddenTab: true, teacher: true },
  { key: 'readingList', hiddenTab: true, teacher: true },
  // 泛读馆子页：阅读器 hash 带 /{bookId}；noTab = 双角色都不进 Tab，hash 直达
  { key: 'libraryReader', hiddenTab: true, noTab: true },
  { key: 'libraryManage', hiddenTab: true, noTab: true, teacher: true },
  { key: 'librarySettings', hiddenTab: true, noTab: true, teacher: true },
  // 专题内容管理（教师，备课中心进入）：JSON 导入/上架/开放配置
  { key: 'topicsAdmin', hiddenTab: true, teacher: true },
]

// hash 路径切段：#/libraryReader/7 -> ['libraryReader','7']
function routeSegs() {
  return (location.hash.replace(/^#\/?/, '') || '')
    .split('?')[0]
    .split('/')
    .filter(Boolean)
}

function h(tag, cls, html) {
  const e = document.createElement(tag)
  if (cls) e.className = cls
  if (html != null) e.innerHTML = html
  return e
}

function currentRoute(role) {
  const [v] = routeSegs()
  // 旧隐藏页归一：#/ttsconf #/readconf → #/config；#/analytics → #/plan?tab=analytics
  // #/today（我的任务页已删）→ #/recite
  let key = v === 'ttsconf' || v === 'readconf' ? 'config' : v
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
  // 旧入口归一：#/plan?tab=analytics（学情分析曾挂在计划表下）
  if (key === 'plan' && /(?:^|&)tab=analytics(?:&|$)/.test(location.hash.split('?')[1] || '')) {
    location.replace('#/grading?tab=analytics')
    key = 'grading'
  }
  const view = VIEWS.find((x) => x.key === key)
  // 学生访问教师页 → 回背单词；教师访问学生页 → 回批改中心；未匹配同理。
  // 例外：hiddenTab 的学生页（教师从备课中心卡片进入，如 topics）教师可访问。
  if (!view || (view.teacher && role !== 'teacher')
      || (view.studentOnly && role === 'teacher' && !view.hiddenTab)) {
    return role === 'teacher' ? 'prep' : 'recite'
  }
  return key
}

function pointsCacheKey(kpCount) {
  return `gkb-points-v1@${kpCount}`
}

async function bootstrap() {
  // ---- 登录守卫：无登录态直接进登录页 ----
  const auth = getAuth()
  if (!auth) {
    app.innerHTML = ''
    const loginEl = h('div')
    app.append(loginEl)
    mountLogin(loginEl, { onLogin: () => restartApp() })
    return
  }

  // header + main 容器
  const role = auth.role
  const visibleViews = VIEWS.filter(
    (v) =>
      !v.noTab &&
      (!v.hiddenTab || role !== 'teacher') &&
      (!v.teacher || role === 'teacher') &&
      (!v.studentOnly || role !== 'teacher'),
  )
  const header = h('header', 'app-header')
  const headerInner = h('div', 'header-inner')
  headerInner.innerHTML = `
    <div class="brand">
      <span class="logo">学</span>
      <div>和爸学<small>${role === 'teacher' ? '教师版' : '学生版'}</small></div>
    </div>
    <nav class="tabs" id="tabs">
      ${visibleViews.map((v) => `<button class="tab" data-view="${v.key}">${v.label}</button>`).join('')}
    </nav>
    <div class="stats-pills" id="stats-pills">
      <span class="pill user-pill" title="当前登录">
        <b>${role === 'teacher' ? '👩‍🏫' : '🎒'}</b> ${auth.user}
        <button class="logout-btn" id="logout-btn" title="退出登录">退出</button>
      </span>
    </div>
  `
  header.append(headerInner)
  const main = h('main')
  const boot = h('div', 'boot')
  boot.innerHTML = `
    <div class="brand" style="justify-content:center;margin-bottom:8px">
      <span class="logo">学</span>
      <div>和爸学<small>学习地图</small></div>
    </div>
    <p style="color:var(--ink-soft)">正在准备知识点…</p>
    <div class="bar"><i id="boot-bar"></i></div>
    <p id="boot-msg" style="color:var(--ink-faint);font-size:13px;margin-top:10px">连接数据后端…</p>
  `
  app.append(header, main, boot)
  main.append(boot)

  // Tab / 退出按钮在 header 挂载后立即绑定：bootstrap 后续还有多个 await
  // （知识点/词汇表加载，生产首次可达数秒），期间点击 Tab 若无监听会被静默
  // 吞掉（hash 不变，看似「没反应」）。绑定不依赖异步数据，先挂先得。
  headerInner.querySelector('#tabs').addEventListener('click', (e) => {
    const t = e.target.closest('.tab')
    if (!t) return
    location.hash = `/${t.dataset.view}`
  })
  headerInner.querySelector('#logout-btn').addEventListener('click', () => {
    setAuth(null)
    restartApp()
  })


  // 详情抽屉（注入考点信号反向索引，知识点加载后再填充）
  const signalCtx = { points: [], byTense: new Map(), byMarker: new Map() }
  const drawer = createDrawer(signalCtx)
  app.append(drawer.el)

  let state = { stats: null, lectures: [], points: [], vocab: [] }

  // 学生版只需要词汇表数据（背单词）；知识点/讲次等重数据仅教师版加载
  if (role === 'teacher') {
    // 拉取基础数据
    try {
      const [stats, lectures] = await Promise.all([api.stats(), api.lectures()])
      state.stats = stats
      state.lectures = lectures
      /* 统计 pill 已按需求移除（48讲/359知识点/526标志词）；stats 仅作探活 */
    } catch (e) {
      boot.querySelector('#boot-msg').innerHTML =
        `<span style="color:#b42318">无法连接数据后端（${e.message}）。请确认 grammar-kb 服务在 127.0.0.1:8000 运行。</span>`
      return
    }

    // 知识点：优先 localStorage 缓存（按知识点总数版本化）
    const kpCount = state.stats.knowledge_points
    const key = pointsCacheKey(kpCount)
    const bar = boot.querySelector('#boot-bar')
    const msg = boot.querySelector('#boot-msg')
    try {
      const cached = localStorage.getItem(key)
      if (cached) {
        state.points = JSON.parse(cached)
        msg.textContent = `已加载缓存 ${state.points.length} 个知识点`
      } else {
        state.points = await fetchAllPoints((done, total) => {
          bar.style.width = `${Math.round((done / total) * 100)}%`
          msg.textContent = `检索知识点 ${done}/${total}…`
        })
        bar.style.width = '100%'
        msg.textContent = `整理 ${state.points.length} 个知识点…`
        try {
          localStorage.setItem(key, JSON.stringify(state.points))
        } catch { /* 配额不足则跳过缓存 */ }
      }
    } catch (e) {
      boot.querySelector('#boot-msg').innerHTML =
        `<span style="color:#b42318">加载知识点失败：${e.message}</span>`
      return
    }

    // 构建考点信号反向索引：时态 / 标志词 → 涉及它的知识点列表（供知识点详情双向跳转）
    signalCtx.points = state.points
    signalCtx.byTense = buildSignalIndex(state.points, (p) =>
      [...new Set((p.markers || []).map((m) => m.tense).filter(Boolean))],
    )
    signalCtx.byMarker = buildSignalIndex(state.points, (p) =>
      [...new Set((p.markers || []).map((m) => m.marker).filter(Boolean))],
    )
  } else {
    try {
      await api.stats() // 探活，同时触发 401 守卫
    } catch (e) {
      return // 已被 handle401 踢回登录页
    }
  }

  // 词汇表（基于讲义语料的 /vocabulary，串行避免后端并发压力）
  try {
    state.vocab = await api.vocabulary()
  } catch {
    state.vocab = []
  }

  boot.remove()

  // 路由
  const viewEl = h('div')
  main.append(viewEl)

  const pointsById = new Map(state.points.map((p) => [p.id, p]))
  const ctx = {
    get points() { return state.points },
    get lectures() { return state.lectures },
    openKp: (p) => drawer.openKp(p),
    openLecture: (n, meta) => drawer.openLecture(n, meta),
  }

  function render() {
    const route = currentRoute(role)
    headerInner.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === route),
    )
    // 竞态守卫：每次路由切换都新建子容器并同步挂到 viewEl，再把 container 传给
    // mount——hash 快速切换时，旧视图 await 后的 innerHTML 写入只会落到已脱离
    // 文档的旧容器上，不再覆写新视图的 DOM（也免去旧视图查询器在新 DOM 上取到
    // null 的 TypeError）。
    const prev = viewEl.firstElementChild
    // 切换前先跑旧容器登记的清理（护眼 class / 事件监听 / 计时器等，见各视图 _cleanups）
    if (prev && Array.isArray(prev._cleanups)) prev._cleanups.forEach((fn) => fn())
    const container = h('div')
    viewEl.replaceChildren(container)
    let mounted
    if (route === 'grading') {
      mounted = mountGrading(container)
    } else if (route === 'prep') {
      mounted = mountPrep(container, ctx)
    } else if (route === 'plan') {
      mounted = mountPlan(container)
    } else if (route === 'courses') {
      mounted = mountCourses(container, { lectures: state.lectures, openLecture: ctx.openLecture })
    } else if (route === 'vocab') {
      mounted = mountVocabulary(container, { vocab: state.vocab, openWord: (e) => drawer.showWord(e) })
    } else if (route === 'recite') {
      mounted = mountRecite(container, { vocab: state.vocab, role })
    } else if (route === 'timetable') {
      // 学生课程表：教师周计划只读镜像
      mounted = mountTimetable(container)
    } else if (route === 'topics') {
      // 专题学习（大静态手册独立 chunk）：#/topics 列表 / #/topics/{id} 详情
      mounted = import('./views/topics.js').then(({ mountTopics }) =>
        mountTopics(container, { role }))
    } else if (route === 'topicsAdmin') {
      // 专题内容管理（教师）：JSON 导入制作/上架/开放配置
      mounted = import('./views/topicsAdmin.js').then(({ mountTopicsAdmin }) =>
        mountTopicsAdmin(container))
    } else if (route === 'config') {
      // 设置中心（隐藏页 #/config；旧 #/ttsconf #/readconf 已并入）
      mounted = mountConfig(container)
    } else if (route === 'taxonomy') {
      mounted = mountTaxonomy(container, { pointsById, openKp: ctx.openKp })
    } else if (route === 'fce') {
      mounted = mountFce(container)
    } else if (route === 'fcePapers') {
      mounted = mountFcePapers(container, { role })
    } else if (route === 'fceListen') {
      // FCE 听力播放台（教师备课：16 段音频 + 频谱进度条）
      mounted = mountFceListen(container)
    } else if (route === 'readingList') {
      // 精读文章列表（纯派生文；备课中心-精读板块）
      mounted = mountReadingList(container)
    } else if (route === 'readingAdmin') {
      mounted = mountReadingAdmin(container)
    } else if (route === 'reading') {
      mounted = mountReading(container, { role })
    } else if (route === 'library') {
      mounted = mountLibraryShelf(container, { role })
    } else if (route === 'libraryReader') {
      mounted = mountLibraryReader(container, { role, bookId: Number(routeSegs()[1]) })
    } else if (route === 'libraryManage') {
      mounted = mountLibraryManage(container, { bookId: Number(routeSegs()[1]) })
    } else if (route === 'librarySettings') {
      mounted = mountLibrarySettings(container)
    } else if (route === 'vocabLevel') {
      // 背单词级别详情：本级词表 + 考试设计 + 解锁配置（hash 携带级别）
      mounted = mountVocabLevel(container, { level: Number(routeSegs()[1]) || 0 })
    } else if (route === 'paperAdmin') {
      // 作业卷管理：题目 + 答案（爱问云已批改测验逐题对错）
      mounted = mountPaperAdmin(container)
    } else if (route === 'students') {
      // 学生管理：增删账号 + 配置总目标
      mounted = mountStudents(container)
    } else if (route === 'vocabExam') {
      // 词汇级别考试（备课中心子页：登记成绩 + 打印考卷）
      mounted = mountVocabExam(container)
    } else if (route === 'exams') {
      mounted = mountExams(container, { lectures: state.lectures })
    }
    // 教师子工具页（备课/批改中心跳转进入）：mount 完成后 prepend 返回条
    // （mount 内部会覆写 innerHTML——异步视图须等 settle 后再插入）
    // libraryManage 例外：页内已有「← 返回书架」（主路径从书架进入），
    // 再叠全局条会双返回堆叠（复走查 Major）
    const routeDef = VIEWS.find((v) => v.key === route)
    if (role === 'teacher' && routeDef && routeDef.hiddenTab && route !== 'libraryManage') {
      const addBack = () => {
        // 必须插在 container 内：视图自身 re-render（innerHTML 覆写）时返回条随之
        // 消失，与改造前行为一致；若插在 viewEl 上会跨页残留
        if (!container.querySelector('.gd-tool-back')) {
          // 页面可声明自己的上层（BACK_BASE 映射）；缺省回备课中心
          const BACK_BASE = {
            readingAdmin: ['/grading', '← 返回批改中心'],
            readingList: ['/prep', '← 返回备课中心'],
            students: ['/plan', '← 返回计划表'],
            vocabLevel: ['/prep', '← 返回备课中心'],
            paperAdmin: ['/prep', '← 返回备课中心'],
            vocabExam: ['/vocabLevel/0', '← 返回背单词'],
            exams: ['/grading', '← 返回批改中心'],
            topics: ['/prep', '← 返回备课中心'],
            topicsAdmin: ['/prep', '← 返回备课中心'],
          }
          const [hash, label] = BACK_BASE[route] || ['/prep', '← 返回备课中心']
          const back = h('button', 'fce-back-btn gd-tool-back', label)
          back.addEventListener('click', () => {
            location.hash = hash
          })
          container.prepend(back)
        }
      }
      Promise.resolve(mounted).then(addBack, addBack)
    }
    window.scrollTo(0, 0)
  }

  // Tab / 退出的监听已在 header 挂载后立即绑定（见上方），此处不再重复
  window.addEventListener('hashchange', render)
  render()
}

// 退出登录 / 登录成功后整页重载应用状态
function restartApp() {
  location.reload()
}

function renderStats() {
  /* 已移除：统计 pill（48 讲/359 知识点/526 标志词）不再展示 */
}

// 反向索引：keyFn(point) -> 该知识点命中的若干键；返回 Map<key, point[]>
function buildSignalIndex(points, keyFn) {
  const m = new Map()
  for (const p of points) {
    for (const k of keyFn(p) || []) {
      if (!k) continue
      ;(m.get(k) || m.set(k, []).get(k)).push(p)
    }
  }
  for (const arr of m.values()) {
    arr.sort((a, b) => a.lectureNumber - b.lectureNumber || a.id - b.id)
  }
  return m
}

bootstrap()

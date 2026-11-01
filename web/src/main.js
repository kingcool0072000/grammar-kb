import './styles.css'
import { api, getAuth, setAuth } from './api.js'
import { mountLogin } from './views/login.js'
import { createDrawer } from './components/drawer.js'
import { h, swapView } from './shell/dom.js'
import { resolveRoute, tabsFor } from './shell/catalog.js'
import { prepareTeacher, attachTeacherBack } from './shell/teacher.js'
import { prepareStudent } from './shell/student.js'
import { mountView } from './shell/mountView.js'
import { loadWordCards } from './wordcard.js'

// 专注模式：禁正文右键系统菜单（输入框保留）；查词交互只走我们的浮层
document.addEventListener('contextmenu', (e) => {
  const t = e.target
  const editable = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
  if (!editable) e.preventDefault()
})

const app = document.getElementById('app')

async function bootstrap() {
  const auth = getAuth()
  if (!auth) {
    app.innerHTML = ''
    const loginEl = h('div')
    app.append(loginEl)
    mountLogin(loginEl, { onLogin: () => restartApp() })
    return
  }

  const role = auth.role
  const visibleViews = tabsFor(role)
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

  // Tab / 退出在 header 挂上后立刻绑定：后面还有 await，晚绑会吞掉点击。
  headerInner.querySelector('#tabs').addEventListener('click', (e) => {
    const t = e.target.closest('.tab')
    if (!t) return
    location.hash = `/${t.dataset.view}`
  })
  headerInner.querySelector('#logout-btn').addEventListener('click', () => {
    setAuth(null)
    restartApp()
  })

  const cardsReady = loadWordCards()
  const signalCtx = { points: [], byTense: new Map(), byMarker: new Map() }
  const drawer = createDrawer(signalCtx)
  app.append(drawer.el)

  const state = { stats: null, lectures: [], points: [], vocabPromise: null }
  // 词汇表（~528KB）计划表等首屏用不到：只发起不 await，与知识点加载并行，
  // 消费者（背单词页）自行 await 这个 Promise。失败兜底收进 catch（空数组）。
  state.vocabPromise = api.vocabulary().catch(() => [])
  if (role === 'teacher') {
    const ok = await prepareTeacher(state, boot, signalCtx)
    if (!ok) return
  } else {
    const ok = await prepareStudent()
    if (!ok) return
  }

  try { await cardsReady } catch { /* 词卡开关读不到时用默认 */ }
  boot.remove()

  const viewEl = h('div')
  main.append(viewEl)
  const ctx = {
    get points() { return state.points },
    get lectures() { return state.lectures },
    openKp: (p) => drawer.openKp(p),
    openLecture: (n, meta) => drawer.openLecture(n, meta),
    openWord: (e) => drawer.showWord(e),
  }

  function render() {
    const route = resolveRoute(role)
    headerInner.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === route),
    )
    const container = swapView(viewEl)
    const pointsById = new Map(state.points.map((p) => [p.id, p]))
    const mounted = mountView(route, container, { role, state, ctx, pointsById })
    if (role === 'teacher') attachTeacherBack(container, route, mounted)
    window.scrollTo(0, 0)
  }

  window.addEventListener('hashchange', render)
  render()
}

function restartApp() {
  location.reload()
}

bootstrap()

import './styles.css'
import { api, fetchAllPoints } from './api.js'
import { mountCourses } from './views/courses.js'
import { mountVocabulary } from './views/vocabulary.js'
import { mountTaxonomy } from './views/taxonomy.js'
import { mountExams } from './views/exams.js'
import { mountFce } from './views/fce.js'
import { createDrawer } from './components/drawer.js'

const app = document.getElementById('app')

const VIEWS = [
  { key: 'courses', label: '课程' },
  { key: 'vocab', label: '词汇表' },
  { key: 'taxonomy', label: '知识体系' },
  { key: 'fce', label: 'FCE' },
  { key: 'exams', label: '作业成绩' },
]

function h(tag, cls, html) {
  const e = document.createElement(tag)
  if (cls) e.className = cls
  if (html != null) e.innerHTML = html
  return e
}

function currentRoute() {
  const v = (location.hash.replace(/^#\/?/, '') || 'courses').split('?')[0]
  return VIEWS.some((x) => x.key === v) ? v : 'courses'
}

function pointsCacheKey(kpCount) {
  return `gkb-points-v1@${kpCount}`
}

async function bootstrap() {
  // header + main 容器
  const header = h('header', 'app-header')
  const headerInner = h('div', 'header-inner')
  headerInner.innerHTML = `
    <div class="brand">
      <span class="logo">语</span>
      <div>语法知识库<small>学习地图</small></div>
    </div>
    <nav class="tabs" id="tabs">
      ${VIEWS.map((v) => `<button class="tab" data-view="${v.key}">${v.label}</button>`).join('')}
    </nav>
    <div class="stats-pills" id="stats-pills"></div>
  `
  header.append(headerInner)
  const main = h('main')
  const boot = h('div', 'boot')
  boot.innerHTML = `
    <div class="brand" style="justify-content:center;margin-bottom:8px">
      <span class="logo">语</span>
      <div>语法知识库<small>学习地图</small></div>
    </div>
    <p style="color:var(--ink-soft)">正在准备知识点…</p>
    <div class="bar"><i id="boot-bar"></i></div>
    <p id="boot-msg" style="color:var(--ink-faint);font-size:13px;margin-top:10px">连接数据后端…</p>
  `
  app.append(header, main, boot)
  main.append(boot)

  // 详情抽屉（注入考点信号反向索引，知识点加载后再填充）
  const signalCtx = { points: [], byTense: new Map(), byMarker: new Map() }
  const drawer = createDrawer(signalCtx)
  app.append(drawer.el)

  let state = { stats: null, lectures: [], points: [], vocab: [] }

  // 拉取基础数据
  try {
    const [stats, lectures] = await Promise.all([api.stats(), api.lectures()])
    state.stats = stats
    state.lectures = lectures
    renderStats(stats)
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
    const route = currentRoute()
    headerInner.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === route),
    )
    viewEl.innerHTML = ''
    if (route === 'courses') {
      mountCourses(viewEl, { lectures: state.lectures, openLecture: ctx.openLecture })
    } else if (route === 'vocab') {
      mountVocabulary(viewEl, { vocab: state.vocab, openWord: (e) => drawer.showWord(e) })
    } else if (route === 'taxonomy') {
      mountTaxonomy(viewEl, { pointsById, openKp: ctx.openKp })
    } else if (route === 'fce') {
      mountFce(viewEl)
    } else if (route === 'exams') {
      mountExams(viewEl, { lectures: state.lectures })
    }
    window.scrollTo(0, 0)
  }

  headerInner.querySelector('#tabs').addEventListener('click', (e) => {
    const t = e.target.closest('.tab')
    if (!t) return
    location.hash = `/${t.dataset.view}`
  })
  window.addEventListener('hashchange', render)
  render()
}

function renderStats(stats) {
  const pills = document.querySelector('#stats-pills')
  if (!stats || !pills) return
  pills.innerHTML = `
    <span class="pill"><b>${stats.lectures}</b> 讲</span>
    <span class="pill"><b>${stats.knowledge_points}</b> 知识点</span>
    <span class="pill"><b>${stats.markers}</b> 标志词</span>
  `
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

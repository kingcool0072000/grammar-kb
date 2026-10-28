import { api, fetchAllPoints } from '../api.js'
import { h } from './dom.js'

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
  videoAdmin: ['/prep', '← 返回备课中心'],
}

// 这三个 Tab 自己有页头，不再叠一条返回。
const TAB_ROUTES = new Set(['prep', 'plan', 'grading'])

export function pointsCacheKey(kpCount) {
  return `gkb-points-v1@${kpCount}`
}

/** 教师壳启动：知识点 / 讲次。学生壳不走这里。失败时返回 false（boot 上已写原因）。 */
export async function prepareTeacher(state, boot, signalCtx) {
  try {
    const [stats, lectures] = await Promise.all([api.stats(), api.lectures()])
    state.stats = stats
    state.lectures = lectures
  } catch (e) {
    boot.querySelector('#boot-msg').innerHTML =
      `<span style="color:#b42318">无法连接数据后端（${e.message}）。请确认 grammar-kb 服务在 127.0.0.1:8000 运行。</span>`
    return false
  }

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
    return false
  }

  signalCtx.points = state.points
  signalCtx.byTense = buildSignalIndex(state.points, (p) =>
    [...new Set((p.markers || []).map((m) => m.tense).filter(Boolean))],
  )
  signalCtx.byMarker = buildSignalIndex(state.points, (p) =>
    [...new Set((p.markers || []).map((m) => m.marker).filter(Boolean))],
  )
  return true
}

/**
 * 教师子工具页 prepend 返回条。三个主 Tab 不加。
 * libraryManage 页内已有「← 返回书架」，再叠一条会双返回。
 */
export function attachTeacherBack(container, route, mounted) {
  if (TAB_ROUTES.has(route) || route === 'libraryManage') return
  const addBack = () => {
    if (!container.querySelector('.gd-tool-back')) {
      const [hash, label] = BACK_BASE[route] || ['/prep', '← 返回备课中心']
      const back = h('button', 'fce-back-btn gd-tool-back', label)
      back.addEventListener('click', () => { location.hash = hash })
      container.prepend(back)
    }
  }
  Promise.resolve(mounted).then(addBack, addBack)
}

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

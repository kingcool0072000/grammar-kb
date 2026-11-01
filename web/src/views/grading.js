import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { subTabBar, bindSubTabs } from '../tabs.js'
import { mountBoard } from './grading/board.js'
import { mountLog } from './grading/log.js'

// 教师版 · 批改中心。打开默认是待办队列：
//   待批录音 → 待批复述 → 待批作文 → 异常专注
// 已批改 / 已自动阅卷 / 正常专注收进折叠区，不和队列抢位置。
export async function mountGrading(el) {
  // 三子 Tab：批改看板（默认）/ 学习日志 / 学情分析（原计划表子 Tab 迁入）
  const subTab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab') || 'board'
  const state = {
    tab: subTab === 'analytics' ? 'analytics' : subTab === 'log' ? 'log' : 'board',
    student: '',
  }
  // 批改视图返回看板时复用最近一次已加载数据，避免再次等待 5 个接口（按学生分键）。
  const boardSnapshots = new Map()
  let students = []
  try {
    const r = await api.users()
    students = (r.users || []).filter((u) => u.role === 'student').map((u) => u.user)
    if (students.length) state.student = students[0]
  } catch { /* 单学生/接口不可用时筛选条隐藏，展示全部 */ }
  render()

  function render() {
    el.innerHTML = `
      <div class="view-head">
        <h1>批改中心</h1>
      </div>
      ${students.length ? `
      <div class="pw-stubar" style="margin-bottom:14px">
        <label class="pw-student">学生
          <select id="gd-user">
            ${students.map((x) => `<option value="${escapeHtml(x)}" ${state.student === x ? 'selected' : ''}>${escapeHtml(x)}</option>`).join('')}
          </select>
        </label>
      </div>` : ''}
      ${subTabBar('gtab', [['board', '📋 批改看板'], ['log', '📚 学习日志'], ['analytics', '📊 学情分析']], state.tab)}
      <div id="gd-body"></div>
    `
    bindSubTabs(el, 'gtab', (t) => { state.tab = t; render() })
    const sel = el.querySelector('#gd-user')
    if (sel) sel.addEventListener('change', (e) => {
      state.student = e.target.value
      render()
    })
    const body = el.querySelector('#gd-body')
    if (state.tab === 'analytics') {
      import('./analytics.js').then(({ mountAnalytics }) => {
        const host = document.createElement('div')
        body.replaceChildren(host)
        mountAnalytics(host, { bare: true, user: state.student })
      })
      return
    }
    if (state.tab === 'log') {
      mountLog(body, { state })
      return
    }
    mountBoard(body, { state, render, boardSnapshots, students, go })
  }
}

// 跳转路由（sessionStorage 联动目标页自动打开对应面板）
export function go(target) {
  if (target === 'recording') {
    sessionStorage.setItem('gkb-open-review', '1')
    location.hash = '#/readingAdmin'
  } else if (target === 'essay') {
    sessionStorage.setItem('gkb-open-essay', '1')
    location.hash = '#/fcePapers'
  } else if (target === 'fce-subs') {
    location.hash = '#/fcePapers'
  } else if (target === 'haya-exam') {
    location.hash = '#/exams'
  } else if (target === 'recite') {
    // 背单词全量趋势在学情分析板块；改 query 触发 hashchange 重挂子 Tab
    location.hash = '#/grading?tab=analytics'
  }
}

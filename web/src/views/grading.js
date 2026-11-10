import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { subTabBar, bindSubTabs } from '../tabs.js'
import { mountLog } from './grading/log.js'

// 教师版 · 学情与日志（原批改中心瘦身后独立页）。
// 批改队列已按学科内嵌进备课中心各分区（views/grading/queues.js +
// views/grading/vocabGrading.js），本页只留学习日志与学情分析。
// 旧链接 ?tab=board 或无 tab 一律回落学习日志，不在此重定向
// （#/grading → 备课中心的跳转由 catalog.js 侧统一处理）。
export async function mountGrading(el) {
  const subTab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab')
  const state = {
    tab: subTab === 'analytics' ? 'analytics' : 'log',
    student: '',
  }
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
        <h1>学情与日志</h1>
      </div>
      ${students.length ? `
      <div class="pw-stubar" style="margin-bottom:14px">
        <label class="pw-student">学生
          <select id="gd-user">
            ${students.map((x) => `<option value="${escapeHtml(x)}" ${state.student === x ? 'selected' : ''}>${escapeHtml(x)}</option>`).join('')}
          </select>
        </label>
      </div>` : ''}
      ${subTabBar('gtab', [['log', '📚 学习日志'], ['analytics', '📊 学情分析']], state.tab)}
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
    mountLog(body, { state })
  }
}

// 跳转路由（sessionStorage 联动目标页自动打开对应面板）。
// 录音/作文批改已内嵌进备课中心分区，旧的批改外跳分支（录音 / 作文 /
// FCE 记录，及其 sessionStorage 置位联动）全部删除。
export function go(target) {
  if (target === 'haya-exam') {
    location.hash = '#/exams'
  } else if (target === 'recite') {
    // 背单词全量趋势在学情分析板块；改 query 触发 hashchange 重挂子 Tab
    location.hash = '#/grading?tab=analytics'
  }
}

// FCE 真题（壳）：公共骨架 = 数据加载 / 周计划 Part 过滤 / 卷列表 + 练习记录 /
// 按角色分流入口。做题流见 fcePapers/student.js，教师批改流见 fcePapers/teacher.js。
import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { renderPartList, doneCount, subRow, bindSubRowActions, setAllowedPartsCache } from './fcePapers/student.js'
import { renderEssayReview } from './fcePapers/teacher.js'

export async function mountFcePapers(el, { role } = {}) {
  const auth = getAuth()
  const myRole = role || (auth && auth.role) || 'teacher'
  el.innerHTML = '<div class="view-head"><h1>FCE 真题</h1><p>加载中…</p></div>'
  let papers, history, weekTodo
  try {
    ;[papers, history, weekTodo] = await Promise.all([
      api.fcePapers(),
      api.fceSubmissions(),
      api.planWeekTodo().catch(() => null), // 学生过滤用；失败则不过滤
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  if (!papers.length) {
    el.querySelector('p').textContent = '题库为空（data/fce.db 未入库）'
    return
  }

  // 学生版：只显示本周计划配置的大题（label "Test 1 · List P1"：
  // List=听力 / RUE=读写 / Writ=写作；Test+Paper+Part 三元组都要匹配）
  let allowedParts = null
  if (myRole !== 'teacher' && weekTodo) {
    const labels = (weekTodo.goals && weekTodo.goals.fce) || []
    // paper 前缀 → 后端 paper 全名（fce_section.paper）
    const PAPER_BY_CODE = { List: 'Listening', RUE: 'Reading and Use of English', Writ: 'Writing' }
    allowedParts = new Map() // testId → Set("paper|part")
    for (const l of labels) {
      const m = String(l).match(/Test\s*(\d+)\s*·\s*(RUE|List|Writ)\s*P(\d+)/i)
      if (!m) continue
      const tid = Number(m[1])
      const paper = PAPER_BY_CODE[m[2]]
      if (!paper) continue
      if (!allowedParts.has(tid)) allowedParts.set(tid, new Set())
      allowedParts.get(tid).add(`${paper}|${Number(m[3])}`)
    }
    papers = papers.filter((t) => (allowedParts.get(t.test_id) || new Set()).size > 0)
  }
  setAllowedPartsCache(allowedParts) // 练习页返回路径共用（见 renderPartList 定义处注释）

  const pending = history.filter((s) => s.status === 'pending')
  const totalQ = (t) => Object.values(t.papers).flat().reduce((s, p) => s + p.questions, 0)

  const emptyPlan = myRole !== 'teacher' && allowedParts !== null && !papers.length
  el.innerHTML = `
    <div class="view-head">
      <h1>FCE 真题</h1>
      <p>${myRole === 'teacher'
        ? '选一套试卷，每次练习一个大题。客观题提交即自动批改；作文提交后由老师批改。'
        : '老师本周布置的试卷在这里；每次练习一个大题，客观题提交即自动批改。'}</p>
    </div>
    ${myRole === 'teacher' && pending.length ? `
      <button class="fce-essay-review-btn" id="essay-review">📝 待批改作文（${pending.length}）</button>
    ` : ''}
    ${emptyPlan ? `
      <section class="fce-group">
        <div class="fce-group-title">📅 本周暂无 FCE 布置</div>
        <p class="fce-empty-plan">老师还没有在周计划里布置 FCE 练习。布置后会出现在这里。</p>
      </section>` : `
    <div class="course-grid">
      ${papers.map((t) => `
        <article class="course-card fce-paper-card" data-test="${t.test_id}" style="--cat-color:#7c3aed">
          <div class="course-num">Test ${t.test_id}</div>
          <div class="course-title">FCE 青少版模拟卷 · ${totalQ(t)} 题</div>
          <div class="course-meta">
            <span class="tag" style="--cat-color:#7c3aed">FCE</span>
            <span>${doneCount(history, t.test_id)} 次练习</span>
          </div>
        </article>`).join('')}
    </div>`}
    ${history.length ? `
      <section class="fce-group" style="margin-top:26px">
        <div class="fce-group-title">📊 我的练习记录</div>
        ${history.slice(0, 10).map((s) => subRow(s, myRole)).join('')}
      </section>` : ''}
  `

  el.querySelectorAll('.fce-paper-card').forEach((card) => {
    card.addEventListener('click', () => renderPartList(el, Number(card.dataset.test), myRole, history, allowedParts))
  })
  const reviewBtn = el.querySelector('#essay-review')
  if (reviewBtn) reviewBtn.addEventListener('click', () => renderEssayReview(el, pending, myRole))
  bindSubRowActions(el, history)
  // 批改中心跳转：自动打开作文批改
  if (sessionStorage.getItem('gkb-open-essay') === '1') {
    sessionStorage.removeItem('gkb-open-essay')
    if (reviewBtn) reviewBtn.click()
  }
}

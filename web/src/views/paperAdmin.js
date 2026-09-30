import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 作业卷管理（备课中心子页）：纯题库视图——每讲的题目原卷 + 答案。
// 作答记录不在本页（统一在批改中心看）；答案可人工补录/编辑（自动保存）。
export async function mountPaperAdmin(el) {
  el.innerHTML = `
    <div class="view-head">
      <button class="chip" data-back>← 返回备课中心</button>
      <h1>作业卷管理</h1>
      <p>每讲作业卷的原题与答案（45 讲 · 1575 题，AI 生成已全覆盖）。作答记录在批改中心。</p>
    </div>
    <div id="pa-body"><p class="muted">加载中…</p></div>
  `
  el.querySelector('[data-back]').addEventListener('click', () => {
    location.hash = '/prep'
  })
  const body = el.querySelector('#pa-body')
  let papers = []

  async function load() {
    try {
      const r = await api.hwPapers()
      papers = r.papers || []
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    render()
  }

  function render() {
    const withAns = papers.filter((p) => p.answered > 0).length
    body.innerHTML = `
      <section class="pa-head card">
        <div class="pa-head-main">
          <b>${papers.length} 讲作业卷</b>
          <span class="muted">${withAns} 讲已补答案</span>
        </div>
      </section>
      <div class="pa-grid">
        ${papers.map((p) => `
          <button class="pa-card card ${p.answered ? 'has-data' : ''}" data-lec="${p.lecture}">
            <b>第 ${p.lecture} 讲</b>
            <span>${p.questions} 题</span>
            <i>${p.answered ? `已补 ${p.answered} 题答案` : '答案未补'}</i>
          </button>`).join('')}
      </div>
      <div id="pa-detail"></div>`
    body.querySelectorAll('[data-lec]').forEach((b) =>
      b.addEventListener('click', () => openDetail(Number(b.dataset.lec))))
  }

  async function openDetail(lec) {
    const host = body.querySelector('#pa-detail')
    host.innerHTML = '<p class="muted">加载中…</p>'
    let d
    try {
      d = await api.hwPaper(lec)
    } catch (e) {
      host.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    host.innerHTML = `
      <section class="pa-detail card">
        <header class="pa-detail-head">
          <h3>第 ${lec} 讲 · ${d.questions.length} 题</h3>
          <button class="chip" id="pa-close">收起</button>
        </header>
        <div class="pa-qlist">
          ${d.questions.map((q) => qRow(q)).join('')}
        </div>
      </section>`
    host.querySelector('#pa-close').addEventListener('click', () => { host.innerHTML = '' })
    host.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function qRow(q) {
    let opts = []
    try { opts = JSON.parse(q.options_json || '[]') } catch { opts = [] }
    const optLetters = ['A', 'B', 'C', 'D', 'E', 'F']
    return `<div class="pa-q" data-q="${q.qnum}">
      <span class="pa-qnum">${q.qnum}</span>
      <div class="pa-qmain">
        <p class="pa-stem">${escapeHtml(q.stem)}</p>
        ${opts.length ? `<div class="pa-opts">${opts.map((o, i) => `<span><i>${optLetters[i] || '·'}</i>${escapeHtml(String(o))}</span>`).join('')}</div>` : ''}
        <div class="pa-ans-line ${q.answer ? '' : 'empty'}">答案：${q.answer ? escapeHtml(q.answer) : '待补'}</div>
      </div>
    </div>`
  }


  await load()
}

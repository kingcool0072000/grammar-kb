import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 作业卷管理（备课中心子页）：每讲作业卷的题目与「答案」。
// 答案口径：爱问云历次已批改测验的逐题对错（未批改暂不获取）；
// homework_question.answer 为真答案文本预留位（可人工编辑）。
export async function mountPaperAdmin(el) {
  el.innerHTML = `
    <div class="view-head">
      <button class="chip" data-back>← 返回备课中心</button>
      <h1>作业卷管理</h1>
      <p>每讲作业卷（题 + 答案）。答案来自爱问云已批改测验的逐题对错；未批改的暂不获取。</p>
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
    const synced = papers.filter((p) => p.attempts > 0).length
    body.innerHTML = `
      <section class="pa-head card">
        <div class="pa-head-main">
          <b>${papers.length} 讲作业卷</b>
          <span class="muted">${synced} 讲已同步过历次对错</span>
        </div>
        <button class="btn-primary" id="pa-sync">🔄 从爱问云同步答案</button>
      </section>
      <div class="pa-grid">
        ${papers.map((p) => `
          <button class="pa-card card ${p.attempts ? 'has-data' : ''}" data-lec="${p.lecture}">
            <b>第 ${p.lecture} 讲</b>
            <span>${p.questions} 题</span>
            <i>${p.attempts ? `${p.attempts} 次作答 · 错 ${(p.answered - p.ok)} 题次` : '未同步'}</i>
          </button>`).join('')}
      </div>
      <div id="pa-detail"></div>`
    body.querySelector('#pa-sync').addEventListener('click', doSync)
    body.querySelectorAll('[data-lec]').forEach((b) =>
      b.addEventListener('click', () => openDetail(Number(b.dataset.lec))))
  }

  async function doSync() {
    const btn = body.querySelector('#pa-sync')
    btn.disabled = true
    btn.textContent = '同步中…（约 30 秒）'
    try {
      const rep = await api.hwSyncAicloud()
      toast(`✅ 同步 ${rep.synced} 场测验 / ${rep.rows} 题次对错（跳过 ${rep.skipped} 场未出分）`)
      await load()
    } catch (e) {
      alert('同步失败：' + e.message)
      btn.disabled = false
      btn.textContent = '🔄 从爱问云同步答案'
    }
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
    const stMeta = {
      steady: ['✅ 历次全对', 'ok'],
      never: ['❌ 历次全错', 'no'],
      mixed: ['⚖️ 对错交替', 'mix'],
      new: ['— 未考过', 'na'],
    }
    host.innerHTML = `
      <section class="pa-detail card">
        <header class="pa-detail-head">
          <h3>第 ${lec} 讲 · ${d.questions.length} 题</h3>
          <button class="chip" id="pa-close">收起</button>
        </header>
        <div class="pa-qlist">
          ${d.questions.map((q) => {
            const [label, cls] = stMeta[q.status] || stMeta.new
            const hist = (q.history || [])
              .map((h) => `<i class="${h.correct ? 'ok' : 'no'}" title="${h.date}">${h.correct ? '✓' : '✗'}</i>`).join('')
            let opts = []
            try { opts = JSON.parse(q.options_json || '[]') } catch { opts = [] }
            return `<div class="pa-q ${cls}" data-q="${q.qnum}">
              <span class="pa-qnum">${q.qnum}</span>
              <div class="pa-qmain">
                <p class="pa-stem">${escapeHtml(q.stem)}</p>
                ${opts.length ? `<div class="pa-opts">${opts.map((o, i) => `<span><i>${'ABCD'[i] || '·'}</i>${escapeHtml(String(o))}</span>`).join('')}</div>` : ''}
                <div class="pa-qmeta">
                  <span class="pa-st ${cls}">${label}</span>
                  <span class="pa-hist">${hist}</span>
                  <span class="pa-ans">答案：${q.answer ? escapeHtml(q.answer) : '<em>历次对错即掌握度；答案文本待人工补</em>'}</span>
                </div>
              </div>
            </div>`
          }).join('')}
        </div>
      </section>`
    host.querySelector('#pa-close').addEventListener('click', () => { host.innerHTML = '' })
    host.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function toast(msg) {
    const t = document.createElement('div')
    t.className = 'plan-toast'
    t.textContent = msg
    document.body.appendChild(t)
    setTimeout(() => t.remove(), 4000)
  }

  await load()
}

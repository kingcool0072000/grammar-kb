// 微练习题库管理（教师端 #/microAdmin）：分类统计（上架/草稿量 + 作答正确率）、
// 草稿审核（上架/下架/删除/行内编辑）、手动加题。题目数组与写接口契约见
// /micro-admin/questions（PUT 只吃 category/topic/stem/options/answer/explain/status）。
import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'
import { subTabBar, bindSubTabs } from '../tabs.js'

const CATS = [
  ['wrong', '错题重练'],
  ['variant', '变形题'],
  ['tense', '时态'],
  ['phrasal', '短语动词'],
  ['idiom', '习语'],
  ['grammar', '语法'],
]
const catLabel = (c) => (CATS.find(([k]) => k === c) || [null, c || '语法'])[1]
const letterOf = (i) => String.fromCharCode(65 + i)

// 选项按位置对应 A-D：空项只允许出现在末尾（否则后面的字母会整体错位），
// 至少要有 A、B 两项。read(i) 返回第 i 个选项去掉首尾空格后的文本。
function readOptions(read) {
  const vals = [0, 1, 2, 3].map(read)
  let last = -1
  vals.forEach((v, i) => { if (v !== '') last = i })
  if (last < 1) return { error: '至少要填 A、B 两个选项' }
  for (let i = 0; i <= last; i++) {
    if (vals[i] === '') return { error: '空选项只能空在末尾（选项按位置对应 A-D）' }
  }
  return { options: vals.slice(0, last + 1) }
}

export async function mountMicroAdmin(el, { role } = {}) {
  const state = { tab: 'draft', category: '' }

  el.innerHTML = `
    <div class="view-head">
      <h1>✏️ 微练习 · 题库管理</h1>
      <p>学生每天自动抽 3 题。草稿上架后才进抽题池；删除不可恢复。</p>
    </div>
    <div id="mcad-stats"></div>
    <div id="mcad-body"></div>`

  render()

  async function render() {
    const body = el.querySelector('#mcad-body')
    body.innerHTML = `
      ${subTabBar('mtab', [['draft', '待审草稿'], ['published', '已上架'], ['add', '＋ 手动加题']], state.tab)}
      ${state.tab !== 'add' ? catFilterHtml() : ''}
      <p class="muted">加载中…</p>`
    bindSubTabs(body, 'mtab', (t) => { state.tab = t; render() })
    bindCatFilter(body)

    if (state.tab === 'add') {
      renderAddForm(body)
      return
    }

    // 统计与列表互不依赖，同发；统计失败不影响列表
    const statsP = api.microAdminStats().catch(() => null)
    const listP = api.microAdminList({
      status: state.tab, category: state.category, limit: 200,
    })
    renderStats(await statsP, el.querySelector('#mcad-stats'))
    let items
    try {
      items = await listP
    } catch (e) {
      body.querySelector('p.muted').outerHTML =
        `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    renderList(body, Array.isArray(items) ? items : [])
  }

  function catFilterHtml() {
    const chips = [['', '全部'], ...CATS]
    return `
      <div class="filter-bar">
        ${chips.map(([k, label]) =>
          `<button class="chip ${state.category === k ? 'active' : ''}" data-mcat="${k}">${label}</button>`).join('')}
      </div>`
  }

  function bindCatFilter(root) {
    root.querySelectorAll('[data-mcat]').forEach((b) => {
      b.addEventListener('click', () => {
        state.category = b.dataset.mcat
        render()
      })
    })
  }

  // ---- 顶部统计卡：by_category 上架/草稿 + log 作答量与正确率 ----
  function renderStats(stats, host) {
    if (!host) return
    if (!stats) {
      host.innerHTML = ''
      return
    }
    const by = stats.by_category || {}
    const log = stats.log || {}
    const cats = [...new Set([...Object.keys(by), ...Object.keys(log)])]
    host.innerHTML = `
      <section class="card mcad-stats-card">
        <h3>分类统计</h3>
        <div class="mcad-stats-grid">
          <span class="mcad-stats-head">分类</span>
          <span class="mcad-stats-head">已上架</span>
          <span class="mcad-stats-head">草稿</span>
          <span class="mcad-stats-head">作答</span>
          <span class="mcad-stats-head">正确率</span>
          ${cats.map((c) => {
            const b = by[c] || {}
            const l = log[c] || {}
            const rate = l.asked ? `${Math.round((l.correct || 0) / l.asked * 100)}%` : '—'
            return `
              <span class="mcad-stats-cat"><i class="mc-badge ${escAttr(c)}"></i>${escapeHtml(catLabel(c))}</span>
              <span>${b.published ?? 0}</span>
              <span>${b.draft ?? 0}</span>
              <span>${l.asked ?? 0}</span>
              <span>${rate}</span>`
          }).join('')}
        </div>
      </section>`
  }

  // ---- 题目卡（列表） ----
  function renderList(body, items) {
    if (!items.length) {
      body.querySelectorAll('p.muted').forEach((p) => p.remove())
      body.insertAdjacentHTML('beforeend',
        `<div class="mc-card mc-empty">${state.tab === 'draft' ? '没有待审草稿' : '还没有已上架的题'}</div>`)
      return
    }
    body.querySelectorAll('p.muted').forEach((p) => p.remove())
    body.insertAdjacentHTML('beforeend',
      `<div class="mcad-list">${items.map(cardHtml).join('')}</div>`)
    bindCards(body)
  }

  function cardHtml(q) {
    const opts = Array.isArray(q.options) ? q.options : []
    const ref = q.ref_lecture
      ? `第${escapeHtml(q.ref_lecture)}讲${q.ref_qnum != null ? ` · 第${escapeHtml(q.ref_qnum)}题` : ''}`
      : ''
    const meta = [q.source, ref].filter(Boolean)
      .map((s) => `<span>${s}</span>`).join('')
    return `
      <section class="mc-card mc-adm-card" data-id="${q.id}">
        <div class="mc-card-head">
          <span class="mc-badge ${escAttr(q.category || 'grammar')}">${escapeHtml(catLabel(q.category))}</span>
          ${q.topic ? `<span class="mc-topic">${escapeHtml(q.topic)}</span>` : ''}
          <span class="mc-qnum">#${q.id}</span>
          <span class="mc-qnum">${q.status === 'published' ? '已上架' : '草稿'}</span>
        </div>
        <p class="mc-stem">${escapeHtml(q.stem)}</p>
        <div class="mc-opts">
          ${opts.map((t, j) => {
            const key = letterOf(j)
            return `
              <div class="mc-opt locked ${key === q.answer ? 'right' : ''}">
                <span class="mc-opt-key">${key}</span>
                <span class="mc-opt-text">${escapeHtml(t)}</span>
                ${key === q.answer ? '<span class="mc-opt-tag">答案</span>' : ''}
              </div>`
          }).join('')}
        </div>
        ${q.explain ? `<div class="mc-explain"><b>解析</b>${escapeHtml(q.explain)}</div>` : ''}
        ${meta ? `<p class="mc-meta">${meta}</p>` : ''}
        <div class="mcad-actions">
          <button class="btn-ghost" data-act="toggle">${q.status === 'published' ? '下架' : '上架'}</button>
          <button class="btn-ghost" data-act="edit">编辑</button>
          <button class="btn-ghost mcad-del" data-act="del">删除</button>
        </div>
      </section>`
  }

  function bindCards(body) {
    body.querySelectorAll('.mc-adm-card').forEach((card) => {
      const id = Number(card.dataset.id)
      card.querySelectorAll('[data-act]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const act = btn.dataset.act
          if (act === 'toggle') {
            const to = state.tab === 'published' ? 'draft' : 'published'
            btn.disabled = true
            try {
              await api.microAdminUpdate(id, { status: to })
              render()
            } catch (e) { alert(`操作失败：${e.message}`); btn.disabled = false }
          } else if (act === 'del') {
            if (!confirm('确定删除这道题？删除后不可恢复。')) return
            btn.disabled = true
            try {
              await api.microAdminDelete(id)
              render()
            } catch (e) { alert(`删除失败：${e.message}`); btn.disabled = false }
          } else if (act === 'edit') {
            enterEdit(card, id)
          }
        })
      })
    })
  }

  // ---- 行内编辑：整卡替换成小表单，保存走 PUT 白名单字段 ----
  async function enterEdit(card, id) {
    let q
    try {
      const items = await api.microAdminList({ status: state.tab, category: state.category, limit: 200 })
      q = (items || []).find((x) => x.id === id)
    } catch { /* 下面兜底 */ }
    if (!q) { alert('题目数据拉取失败，请重试'); return }
    const opts = [0, 1, 2, 3].map((i) => (q.options || [])[i] || '')
    card.innerHTML = `
      <div class="mc-card-head">
        <span class="mc-badge ${escAttr(q.category || 'grammar')}">${escapeHtml(catLabel(q.category))}</span>
        <span class="mc-qnum">#${q.id} 编辑中</span>
      </div>
      <div class="mcad-form">
        <label>考点<input data-f="topic" value="${escAttr(q.topic || '')}"></label>
        <label>题干<textarea data-f="stem" rows="3">${escapeHtml(q.stem || '')}</textarea></label>
        ${opts.map((t, i) => `
          <label>${letterOf(i)}<input data-opt="${i}" value="${escAttr(t)}"></label>`).join('')}
        <label>正确答案
          <select data-f="answer">
            ${opts.map((_, i) => `<option value="${letterOf(i)}" ${letterOf(i) === q.answer ? 'selected' : ''}>${letterOf(i)}</option>`).join('')}
          </select>
        </label>
        <label>解析<textarea data-f="explain" rows="2">${escapeHtml(q.explain || '')}</textarea></label>
        <div class="mcad-actions">
          <button class="btn-primary" data-eact="save">保存</button>
          <button class="btn-ghost" data-eact="cancel">取消</button>
        </div>
      </div>`
    card.querySelector('[data-eact="cancel"]').addEventListener('click', render)
    card.querySelector('[data-eact="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget
      const get = (f) => card.querySelector(`[data-f="${f}"]`).value.trim()
      const stem = get('stem')
      if (!stem) { alert('题干不能为空'); return }
      const ro = readOptions((i) => card.querySelector(`[data-opt="${i}"]`).value.trim())
      if (ro.error) { alert(ro.error); return }
      const ans = card.querySelector('[data-f="answer"]').value
      if (ans.charCodeAt(0) - 65 >= ro.options.length) { alert('正确答案要选已填的选项'); return }
      btn.disabled = true
      try {
        await api.microAdminUpdate(id, {
          topic: get('topic'), stem, options: ro.options,
          answer: ans, explain: get('explain'),
        })
        render()
      } catch (err) {
        alert(`保存失败：${err.message}`)
        btn.disabled = false
      }
    })
  }

  // ---- 手动加题 ----
  function renderAddForm(body) {
    body.insertAdjacentHTML('beforeend', `
      <section class="card mcad-add-card">
        <h3>手动加题</h3>
        <p class="muted">保存后为草稿，到「待审草稿」上架才会进学生抽题池。</p>
        <div class="mcad-form">
          <label>分类
            <select data-nf="category">
              ${CATS.map(([k, label]) => `<option value="${k}">${label}</option>`).join('')}
            </select>
          </label>
          <label>考点（选填）<input data-nf="topic" placeholder="如：一般过去时 vs 现在完成时"></label>
          <label>题干<textarea data-nf="stem" rows="3" placeholder="题面……"></textarea></label>
          <label>A<input data-nf="o0"></label>
          <label>B<input data-nf="o1"></label>
          <label>C<input data-nf="o2"></label>
          <label>D<input data-nf="o3"></label>
          <label>正确答案
            <select data-nf="answer">
              ${['A', 'B', 'C', 'D'].map((k) => `<option value="${k}">${k}</option>`).join('')}
            </select>
          </label>
          <label>解析（选填）<textarea data-nf="explain" rows="2"></textarea></label>
          <label>来源（选填）<input data-nf="source" placeholder="如：第5讲作业卷"></label>
          <div class="mcad-actions">
            <button class="btn-primary" id="mcad-add-btn">保存草稿</button>
          </div>
        </div>
      </section>`)
    body.querySelector('#mcad-add-btn').addEventListener('click', async (e) => {
      const btn = e.currentTarget
      const get = (f) => body.querySelector(`[data-nf="${f}"]`).value.trim()
      const stem = get('stem')
      if (!stem) { alert('题干不能为空'); return }
      const ro = readOptions((i) => get(`o${i}`))
      if (ro.error) { alert(ro.error); return }
      const ans = get('answer')
      if (ans.charCodeAt(0) - 65 >= ro.options.length) { alert('正确答案要选已填的选项'); return }
      btn.disabled = true
      try {
        await api.microAdminAdd({
          category: get('category'), topic: get('topic'), stem, options: ro.options,
          answer: ans, explain: get('explain'), source: get('source'),
          status: 'draft',
        })
        state.tab = 'draft'
        render()
      } catch (err) {
        alert(`保存失败：${err.message}`)
        btn.disabled = false
      }
    })
  }
}

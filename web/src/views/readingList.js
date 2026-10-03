import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

// 教师版 · 精读文章列表（备课中心-精读板块专属）：
// 只列派生阅读文章（按 Test/Part 分组），点开编辑/删除，支持新增。
// 难度评价体系：每篇三维评分（LX 词汇 / 语法结构·哈1讲次 / 题材），
// 列表挂难度徽章，点徽章看详情；可按难度排序。
// 原文段落管理仍在阅读内容管理页（readingAdmin）。

const LV_COLORS = { 1: '#3b8a3b', 2: '#7a9c2e', 3: '#c98a1b', 4: '#c9591b', 5: '#b42318' }

function lvBadge(r) {
  const c = LV_COLORS[r.level] || '#666'
  return `<span class="rl-diff-badge" style="--lv-c:${c}" data-diff="${r.id}"
    title="点击查看难度详情">${r.level_label} ${Math.round(r.score)}</span>`
}

function diffModal(r) {
  const v = r.dims.vocab
  const g = r.dims.grammar
  const t = r.dims.topic
  const distMax = Math.max(1, ...Object.values(v.dist))
  const distBars = Object.entries(v.dist).map(([lv, n]) =>
    `<div class="rl-dist-row"><span class="rl-dist-lv">L${lv}</span>
      <div class="rl-dist-bar"><i style="width:${(n / distMax) * 100}%"></i></div>
      <span class="rl-dist-n">${n}</span></div>`).join('')
  const structRows = (g.structures || []).map((s) =>
    `<div class="rl-struct-row">
      <span class="rl-struct-name">${escapeHtml(s.name)}<i>×${s.count}</i></span>
      <span class="tag rl-lesson-tag">${escapeHtml(s.lessons)}</span>
    </div>`).join('')
  const wordChips = (items, cls) => items.map(([w, n]) =>
    `<span class="${cls}">${escapeHtml(w)}${n > 1 ? `<i>×${n}</i>` : ''}</span>`).join('')
  const dlg = document.createElement('div')
  dlg.className = 'plan-editor'
  dlg.innerHTML = `
    <div class="plan-editor-mask"></div>
    <div class="plan-editor-body card rl-diff-detail">
      <h3>${escapeHtml(r.title)}
        <span class="rl-diff-badge big" style="--lv-c:${LV_COLORS[r.level] || '#666'}">${r.level_label} · ${Math.round(r.score)} 分</span></h3>
      <p class="rl-diff-sub">${escapeHtml(r.base_key)} · ${r.words} 词 · 三维难度详情</p>
      <div class="rl-dim-grid">
        <section>
          <h4>📖 词汇（LX 分层）<b>${v.score}</b></h4>
          <div class="rl-dim-kv">
            <span>平均词级</span><b>L${v.avg_level}</b>
            <span>词库匹配</span><b>${v.matched}/${v.content}</b>
            <span>L5+ 高阶词</span><b>${v.hard_pct}%</b>
            <span>库外生词</span><b>${v.off_pct}%</b>
          </div>
          <div class="rl-dist">${distBars}</div>
          ${v.hard_words.length ? `<p class="rl-word-cap">高阶词（L5-L7）</p>
            <div class="rl-chips">${wordChips(v.hard_words, 'rl-wchip hard')}</div>` : ''}
          ${v.offlist.length ? `<p class="rl-word-cap">库外生词（需教师判断）</p>
            <div class="rl-chips">${wordChips(v.offlist, 'rl-wchip off')}</div>` : ''}
        </section>
        <section>
          <h4>🧩 语法结构（哈1讲次）<b>${g.score}</b></h4>
          <div class="rl-dim-kv">
            <span>平均句长</span><b>${g.avg_sent} 词</b>
            <span>最长句</span><b>${g.max_sent} 词</b>
          </div>
          ${structRows || '<p class="muted">未检出目标结构</p>'}
        </section>
        <section>
          <h4>🌍 题材<b>${t.score}</b></h4>
          <div class="rl-dim-kv">
            <span>题材</span><b>${escapeHtml(t.label)}</b>
            <span>文体</span><b>${t.expository ? '说明/议论' : '叙事'}</b>
          </div>
          <p class="rl-topic-desc">${escapeHtml(t.desc)}</p>
          ${t.keywords.length ? `<div class="rl-chips">${t.keywords.map((k) =>
            `<span class="rl-wchip kw">${escapeHtml(k)}</span>`).join('')}</div>` : ''}
        </section>
      </div>
      <p class="rl-diff-note">口径：词汇 40% + 语法 45% + 题材 15%；结构计数为正则约值（系统能力内参考，非人工标注）。</p>
      <div class="chip-row"><button class="chip" id="rl-diff-close">关闭</button></div>
    </div>`
  document.body.appendChild(dlg)
  const close = () => dlg.remove()
  dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
  dlg.querySelector('#rl-diff-close').addEventListener('click', close)
}

export async function mountReadingList(el) {
  el.innerHTML = '<p class="muted">加载中…</p>'
  let derived, diff
  try {
    ;[derived, diff] = await Promise.all([
      api.readingArticles(),
      api.readingDifficulty().catch(() => []),
    ])
  } catch (e) {
    el.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
  const diffMap = new Map(diff.map((d) => [d.id, d]))
  let sortByDiff = false
  render()

  function groupByKey(list) {
    const m = new Map()
    for (const a of list) {
      const k = a.base_key || '—'
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(a)
    }
    return [...m.entries()].sort((x, y) => x[0].localeCompare(y[0]))
  }

  function overviewHtml() {
    if (!diff.length) return ''
    const n = diff.length
    const avg = Math.round(diff.reduce((s, d) => s + d.score, 0) / n)
    const lvCount = {}
    for (const d of diff) lvCount[d.level_label] = (lvCount[d.level_label] || 0) + 1
    const lvOrder = ['入门', '进阶', '中阶', '中高', '高阶']
    const hardest = [...diff].sort((a, b) => b.score - a.score).slice(0, 3)
    return `
      <div class="rl-diff-overview">
        <div class="rl-ov-item"><b>${n}</b><span>篇</span></div>
        <div class="rl-ov-item"><b>${avg}</b><span>平均难度分</span></div>
        <div class="rl-ov-item lv">
          ${lvOrder.filter((lb) => lvCount[lb]).map((lb) =>
            `<i style="--lv-c:${LV_COLORS[lvOrder.indexOf(lb) + 1]}">${lb} ${lvCount[lb]}</i>`).join('')}
        </div>
        <div class="rl-ov-hard">最难：${hardest.map((d) =>
          `${escapeHtml(d.title.length > 18 ? d.title.slice(0, 18) + '…' : d.title)}（${Math.round(d.score)}）`).join(' · ')}</div>
      </div>`
  }

  function cardHtml(d) {
    const r = diffMap.get(d.id)
    return `
      <div class="reading-card static">
        <span class="reading-card-title">${escapeHtml(d.title)}</span>
        <span class="reading-card-meta">
          ${r ? lvBadge(r) : ''}
          <span class="tag" style="--cat-color:#8a6d3b">${d.words} 词</span>
          ${d.source ? `<span class="reading-card-src">${escapeHtml(d.source)}</span>` : ''}
          <button class="reading-btn small" data-pv="${d.id}">👁 预览</button>
          <button class="reading-btn small danger" data-del="${d.id}">删除</button>
        </span>
      </div>`
  }

  function render() {
    const groups = groupByKey(derived)
    const sorted = sortByDiff
      ? [...derived].sort((a, b) => (diffMap.get(b.id)?.score || 0) - (diffMap.get(a.id)?.score || 0))
      : null
    el.innerHTML = `
      <div class="view-head">
        <h1>精读文章</h1>
        <p>派生阅读文章 ${derived.length} 篇。难度评价：LX 词汇分层 + 语法结构（哈1讲次）+ 题材，点徽章看详情。学生的录音提交与评分在批改中心。</p>
      </div>
      ${overviewHtml()}
      <div class="rl-toolbar">
        <button class="reading-btn primary" id="rl-add">＋ 新增派生文章</button>
        <button class="reading-btn ${sortByDiff ? 'primary' : ''}" id="rl-sort">${sortByDiff ? '↕ 按难度降序（点回分组）' : '↕ 按难度排序'}</button>
      </div>
      ${sorted
        ? `<section class="fce-group"><div class="reading-art-list">${sorted.map(cardHtml).join('')}</div></section>`
        : groups.map(([key, list]) => `
        <section class="fce-group">
          <div class="fce-group-title">${escapeHtml(key)}（${list.length} 篇）</div>
          <div class="reading-art-list">${list.map(cardHtml).join('')}</div>
        </section>`).join('') || '<p class="reading-hint">暂无派生文章——去阅读内容管理页从原文段落生成。</p>'}
      <div class="reading-editor" id="rl-editor" hidden>
        <div class="reading-editor-head">
          <input id="rl-ed-key" placeholder="来源段落（如 T1P2 / T1P7-A）"/>
          <input id="rl-ed-title" placeholder="标题"/>
          <input id="rl-ed-source" placeholder="来源（网站/杂志名）"/>
        </div>
        <textarea id="rl-ed-text" rows="10" placeholder="粘贴派生文章正文（120-300 词，B2 难度，青少年主题）。空行分段。"></textarea>
        <div class="reading-editor-actions">
          <button class="reading-btn primary" id="rl-ed-save">保存</button>
          <button class="reading-btn" id="rl-ed-cancel">取消</button>
        </div>
      </div>`
    el.querySelector('#rl-add').addEventListener('click', () => openEditor())
    el.querySelector('#rl-sort').addEventListener('click', () => { sortByDiff = !sortByDiff; render() })
    el.querySelectorAll('[data-diff]').forEach((b) =>
      b.addEventListener('click', (e) => {
        e.stopPropagation()
        const r = diffMap.get(Number(b.dataset.diff))
        if (r) diffModal(r)
      }))
    el.querySelectorAll('[data-pv]').forEach((b) =>
      b.addEventListener('click', async () => {
        const d = derived.find((x) => x.id === Number(b.dataset.pv))
        if (!d) return
        let full = d
        try { full = await api.readingArticle(d.id) } catch { /* 用列表数据 */ }
        const dlg = document.createElement('div')
        dlg.className = 'plan-editor'
        dlg.innerHTML = `
          <div class="plan-editor-mask"></div>
          <div class="plan-editor-body card rl-pv">
            <h3>${escapeHtml(d.title)} <i>${escapeHtml(d.base_key)} · ${d.words} 词</i></h3>
            <div class="rl-pv-text">${(full.text || '').split(/\n+/).map((par) =>
              `<p>${escapeHtml(par).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}</p>`).join('')}</div>
            <div class="chip-row"><button class="chip" id="rl-pv-close">关闭</button></div>
          </div>`
        document.body.appendChild(dlg)
        const close = () => dlg.remove()
        dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
        dlg.querySelector('#rl-pv-close').addEventListener('click', close)
      }))
    el.querySelectorAll('[data-del]').forEach((b) =>
      b.addEventListener('click', async () => {
        const d = derived.find((x) => x.id === Number(b.dataset.del))
        if (!confirm(`删除「${d?.title}」？`)) return
        try {
          await api.readingDeleteDerived(d.id)
          derived = derived.filter((x) => x.id !== d.id)
          render()
        } catch (e) { alert('删除失败：' + e.message) }
      }))
  }

  let editId = null
  const openEditor = async (d = null) => {
    editId = d ? d.id : null
    const editor = el.querySelector('#rl-editor')
    editor.hidden = false
    el.querySelector('#rl-ed-key').value = d ? d.base_key : ''
    el.querySelector('#rl-ed-title').value = d ? d.title : ''
    el.querySelector('#rl-ed-source').value = d ? (d.source || '') : ''
    const ta = el.querySelector('#rl-ed-text')
    ta.value = ''
    if (d) {
      try { ta.value = (await api.readingArticle(d.id)).text } catch { /* 空 */ }
    }
    editor.scrollIntoView({ behavior: 'smooth' })
    // 绑保存（render 后重绑）
    el.querySelector('#rl-ed-save').onclick = async () => {
      const key = el.querySelector('#rl-ed-key').value.trim()
      const title = el.querySelector('#rl-ed-title').value.trim()
      const source = el.querySelector('#rl-ed-source').value.trim()
      const text = ta.value.trim()
      if (!key || !title || !text) return alert('来源段落/标题/正文必填')
      try {
        if (editId) {
          await api.readingUpdateDerived(editId, { base_key: key, title, source, text })
        } else {
          await api.readingAddDerived({ base_key: key, title, source, text })
        }
        editor.hidden = true
        // 重载列表+难度
        ;[derived, diff] = await Promise.all([
          api.readingArticles(),
          api.readingDifficulty().catch(() => []),
        ])
        diffMap.clear()
        diff.forEach((x) => diffMap.set(x.id, x))
        render()
      } catch (e) { alert('保存失败：' + e.message) }
    }
    el.querySelector('#rl-ed-cancel').onclick = () => { editor.hidden = true }
  }
}

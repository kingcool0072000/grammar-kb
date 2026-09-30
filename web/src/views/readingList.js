import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

// 教师版 · 精读文章列表（备课中心-精读板块专属）：
// 只列派生阅读文章（按 Test/Part 分组），点开编辑/删除，支持新增。
// 原文段落管理仍在阅读内容管理页（readingAdmin）。
export async function mountReadingList(el) {
  el.innerHTML = '<p class="muted">加载中…</p>'
  let derived
  try {
    derived = await api.readingArticles()
  } catch (e) {
    el.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
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

  function render() {
    el.innerHTML = `
      <div class="view-head">
        <h1>精读文章</h1>
        <p>派生阅读文章 ${derived.length} 篇（按来源 Test/Part 分组）。学生的录音提交与评分在批改中心。</p>
      </div>
      <button class="reading-btn primary" id="rl-add" style="margin-bottom:14px">＋ 新增派生文章</button>
      ${groupByKey(derived).map(([key, list]) => `
        <section class="fce-group">
          <div class="fce-group-title">${escapeHtml(key)}（${list.length} 篇）</div>
          <div class="reading-art-list">
            ${list.map((d) => `
              <div class="reading-card static">
                <span class="reading-card-title">${escapeHtml(d.title)}</span>
                <span class="reading-card-meta">
                  <span class="tag" style="--cat-color:#8a6d3b">${d.words} 词</span>
                  ${d.source ? `<span class="reading-card-src">${escapeHtml(d.source)}</span>` : ''}
                  <button class="reading-btn small" data-pv="${d.id}">👁 预览</button>
                  <button class="reading-btn small danger" data-del="${d.id}">删除</button>
                </span>
              </div>`).join('')}
          </div>
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
          await api.reqJson ? null : null
          await fetch(`/api/reading/articles/${b.dataset.del}`, {
            method: 'DELETE',
            headers: { Authorization: 'Bearer ' + JSON.parse(localStorage.getItem('gkb-auth-v1')).token },
          }).then(async (r) => {
            if (!r.ok) throw new Error(await r.text())
          })
          derived = derived.filter((x) => x.id !== Number(b.dataset.del))
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
        const auth = JSON.parse(localStorage.getItem('gkb-auth-v1'))
        const r = await fetch(editId ? `/api/reading/articles/${editId}` : '/api/reading/articles', {
          method: editId ? 'PUT' : 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + auth.token },
          body: JSON.stringify({ base_key: key, title, source, text }),
        })
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
        editor.hidden = true
        // 重载列表
        derived = await api.readingArticles()
        render()
      } catch (e) { alert('保存失败：' + e.message) }
    }
    el.querySelector('#rl-ed-cancel').onclick = () => { editor.hidden = true }
  }
}

// 句型素材管理（教师端 #/microPatterns）：提升档「句型跟读复述」的句型
// 清单——按讲次分组展示、行内编辑（lecture/sentence/translation/hints）、
// 删除（confirm；boost_log 历史流水保留）、新增单条（走 import 幂等语义）。
// 契约：GET /patterns、PUT/DELETE /patterns-admin/{id}、POST /patterns-admin/import。
import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

export async function mountMicroPatterns(el) {
  el.innerHTML = `
    <div class="view-head">
      <h1>⭐ 微练习 · 句型素材</h1>
      <p>提升档「句型复述」跟读的原句。编辑立即生效；删除不影响历史流水与成绩。</p>
    </div>
    <div class="mpt-toolbar">
      <button class="reading-btn primary" type="button" id="mpt-add">＋ 新增句型</button>
      <span class="mpt-count" id="mpt-count"></span>
    </div>
    <div id="mpt-body"><p class="muted">加载中…</p></div>`
  const body = el.querySelector('#mpt-body')
  let items = []
  let showAdd = false

  await reload()

  async function reload() {
    try {
      items = await api.patternsList()
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    render()
  }

  function render() {
    el.querySelector('#mpt-count').textContent = items.length ? `共 ${items.length} 条` : ''
    if (!items.length && !showAdd) {
      body.innerHTML = '<p class="muted">还没有句型素材。点「＋ 新增句型」添加第一条。</p>'
      return
    }
    // 按讲次分组
    const groups = new Map()
    for (const it of items) {
      const k = it.lecture ?? '—'
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k).push(it)
    }
    body.innerHTML = (showAdd ? addFormHtml() : '')
      + [...groups.entries()].map(([lec, rows]) => `
        <div class="mpt-group">
          <h3>第 ${escapeHtml(String(lec))} 讲 <small>${rows.length} 条</small></h3>
          ${rows.map(rowHtml).join('')}
        </div>`).join('')
    bind()
  }

  function rowHtml(it) {
    const hints = (it.hints || []).filter(Boolean)
    return `
      <div class="mpt-row card" data-id="${it.id}">
        <div class="mpt-row-head">
          <b class="mpt-id">#${it.id}</b>
          <span class="mpt-sentence">${escapeHtml(it.sentence || '')}</span>
        </div>
        ${it.translation ? `<p class="mpt-trans">${escapeHtml(it.translation)}</p>` : ''}
        ${hints.length ? `<div class="mpt-hints">${hints.map((x) => `<span>${escapeHtml(x)}</span>`).join('')}</div>` : ''}
        <div class="mpt-acts">
          <button class="reading-btn small" type="button" data-edit>编辑</button>
          <button class="reading-btn small danger" type="button" data-del>删除</button>
        </div>
      </div>`
  }

  function addFormHtml() {
    return `
      <form class="mpt-form card" id="mpt-addform">
        <b>新增句型</b>
        <label>讲次（1-99）<input name="lecture" type="number" min="1" max="99" value="30" required /></label>
        <label>英文原句<input name="sentence" required maxlength="500" placeholder="The Great Wall was built thousands of years ago." /></label>
        <label>中文翻译（可选）<input name="translation" maxlength="500" /></label>
        <label>提示（可选，逗号分隔）<input name="hints" placeholder="被动结构？, 时态？" /></label>
        <div class="mpt-acts">
          <button class="reading-btn primary" type="submit">保存</button>
          <button class="reading-btn" type="button" data-canceladd>取消</button>
        </div>
      </form>`
  }

  function editFormHtml(it) {
    const hints = (it.hints || []).join(', ')
    return `
      <form class="mpt-form card" data-editform="${it.id}">
        <b>编辑 #${it.id}</b>
        <label>讲次（1-99）<input name="lecture" type="number" min="1" max="99" value="${escAttr(String(it.lecture ?? ''))}" required /></label>
        <label>英文原句<input name="sentence" required maxlength="500" value="${escAttr(it.sentence || '')}" /></label>
        <label>中文翻译（可选）<input name="translation" maxlength="500" value="${escAttr(it.translation || '')}" /></label>
        <label>提示（可选，逗号分隔）<input name="hints" value="${escAttr(hints)}" /></label>
        <p class="mpt-err" data-err hidden></p>
        <div class="mpt-acts">
          <button class="reading-btn primary" type="submit">保存</button>
          <button class="reading-btn" type="button" data-canceledit>取消</button>
        </div>
      </form>`
  }

  function parseHints(raw) {
    return String(raw || '').split(/[,，]/).map((x) => x.trim()).filter(Boolean)
  }

  function bind() {
    el.querySelector('#mpt-add').addEventListener('click', () => {
      showAdd = !showAdd
      render()
    })
    const cancelAdd = body.querySelector('[data-canceladd]')
    if (cancelAdd) cancelAdd.addEventListener('click', () => { showAdd = false; render() })
    const addForm = body.querySelector('#mpt-addform')
    if (addForm) {
      addForm.addEventListener('submit', async (e) => {
        e.preventDefault()
        const f = new FormData(addForm)
        try {
          await api.patternsImport([{
            lecture: Number(f.get('lecture')),
            sentence: String(f.get('sentence') || '').trim(),
            translation: String(f.get('translation') || '').trim(),
            hints: parseHints(f.get('hints')),
          }])
          showAdd = false
          await reload()
        } catch (err) { showFormErr(addForm, err.message) }
      })
    }
    body.querySelectorAll('[data-id]').forEach((row) => {
      const id = Number(row.dataset.id)
      const it = items.find((x) => x.id === id)
      row.querySelector('[data-edit]').addEventListener('click', () => {
        if (!it) return
        row.insertAdjacentHTML('beforebegin', editFormHtml(it))
        row.hidden = true
        const form = row.previousElementSibling
        form.querySelector('[data-canceledit]').addEventListener('click', () => {
          form.remove(); row.hidden = false
        })
        form.addEventListener('submit', async (e) => {
          e.preventDefault()
          const f = new FormData(form)
          try {
            await api.patternsUpdate(id, {
              lecture: Number(f.get('lecture')),
              sentence: String(f.get('sentence') || '').trim(),
              translation: String(f.get('translation') || '').trim(),
              hints: parseHints(f.get('hints')),
            })
            await reload()
          } catch (err) { showFormErr(form, err.message) }
        })
      })
      row.querySelector('[data-del]').addEventListener('click', async () => {
        if (!it) return
        if (!confirm(`删除句型 #${id}？\n\n${it.sentence}\n\n历史提交与成绩保留，只删素材。`)) return
        try {
          await api.patternsDelete(id)
          await reload()
        } catch (err) { alert(`删除失败：${err.message}`) }
      })
    })
  }

  function showFormErr(form, msg) {
    const p = form.querySelector('[data-err]')
    if (p) { p.textContent = `保存失败：${msg}`; p.hidden = false }
  }
}

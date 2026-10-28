import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'

// 教师版 · 视频学习管理（完整管理）：
//   📚 内容管理：视频清单 CRUD（vid/标题/类目/时长/生僻词/提示/上下架）
//   📊 学生进度：按学生看 本周安排（完成态）/ 未完成 / 已完成（成绩评语）/ 计划外
// 复述录音的试听批改在批改中心；这里给待批改的直达入口。
export async function mountVideoAdmin(el) {
  el.innerHTML = '<div class="view-head"><h1>视频学习管理</h1><p>加载中…</p></div>'
  let students = []
  try {
    const r = await api.users()
    students = (r.users || []).filter((u) => u.role === 'student').map((u) => u.user)
  } catch { /* 单学生场景隐藏选择条 */ }
  const state = {
    tab: new URLSearchParams(location.hash.split('?')[1] || '').get('tab') === 'progress'
      ? 'progress' : 'content',
    student: students[0] || 'malin',
  }
  render()

  function render() {
    el.innerHTML = `
      <div class="view-head"><h1>视频学习管理</h1></div>
      ${students.length ? `
      <div class="pw-stubar" style="margin-bottom:14px">
        <label class="pw-student">学生
          <select id="va-user">
            ${students.map((x) => `<option value="${escapeHtml(x)}" ${state.student === x ? 'selected' : ''}>${escapeHtml(x)}</option>`).join('')}
          </select>
        </label>
      </div>` : ''}
      <div class="ana-tabs plan-subtabs">
        <button class="reading-btn ${state.tab === 'content' ? 'primary' : ''}" data-atab="content">📚 内容管理</button>
        <button class="reading-btn ${state.tab === 'progress' ? 'primary' : ''}" data-atab="progress">📊 学生进度</button>
      </div>
      <div id="va-body"></div>
    `
    el.querySelectorAll('[data-atab]').forEach((b) =>
      b.addEventListener('click', () => { state.tab = b.dataset.atab; render() }))
    const sel = el.querySelector('#va-user')
    if (sel) sel.addEventListener('change', (e) => {
      state.student = e.target.value
      render()
    })
    const body = el.querySelector('#va-body')
    if (state.tab === 'progress') mountProgress(body)
    else mountContent(body)
  }

  // ---- 📊 学生进度：本周安排 / 未完成 / 已完成 / 计划外 ----
  async function mountProgress(body) {
    body.innerHTML = '<p class="muted">加载中…</p>'
    let data
    try {
      data = await api.videoProgress(state.student)
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    const byVid = new Map((data.videos || []).map((v) => [v.vid, v]))
    const cats = data.categories || {}
    const inCats = (c) => (cats[c] || []).map((vid) => byVid.get(vid)).filter(Boolean)
    const weekAll = inCats('week')
    const weekDone = weekAll.filter((v) => v.done)
    const todoOld = inCats('todo').filter((v) => !v.week)
    const doneOld = inCats('done').filter((v) => !v.week)
    const extra = inCats('extra')
    const pendN = (data.videos || []).filter((v) => v.pending).length

    const row = (v, inWeek) => {
      const score = v.best_score != null
        ? `<b class="fce-his-score ${v.best_score >= 8 ? 'ok' : ''}">${v.best_score}/10</b>`
        : v.pending ? '<b class="fce-his-score pend">待批改</b>' : ''
      return `
      <div class="reading-card static">
        <span class="reading-card-title">🎬 ${escapeHtml(v.title)}</span>
        <span class="reading-card-meta">
          ${inWeek && !v.done ? '<b class="rd-week-tag">📌 本周</b>' : ''}
          <span class="tag" style="--cat-color:#8a6d3b">${escapeHtml(v.category || '综合')}</span>
          <span class="tag" style="--cat-color:#4a6fa5">${fmtMin(v.duration_sec)}</span>
          ${v.retell_n ? `<span class="tag" style="--cat-color:#7a5ba6">复述 ${v.retell_n} 次</span>` : ''}
          ${v.done ? '<b class="fce-his-score ok">✓ 已完成</b>' : '<b class="fce-his-score">未完成</b>'}
          ${score}
          ${v.last_at ? `<span class="reading-card-src">${(v.last_at || '').slice(0, 10)}</span>` : ''}
          <a class="reading-btn small" href="#/videoStudy?vid=${encodeURIComponent(v.vid)}">预览</a>
        </span>
        ${v.last_comment ? `<div class="reading-graded-comment" style="margin-top:6px">💬 ${escapeHtml(v.last_comment)}</div>` : ''}
      </div>`
    }
    const section = (title, list, inWeek = false) => list.length ? `
      <section class="fce-group">
        <div class="fce-group-title">${title}（${list.length}）</div>
        <div class="reading-art-list">${list.map((v) => row(v, inWeek)).join('')}</div>
      </section>` : ''

    body.innerHTML = `
      ${pendN ? `<button class="fce-essay-review-btn" id="va-go-grade">🎙 待批复述（${pendN}）→ 去批改</button>` : ''}
      ${weekAll.length
        ? section(`📌 本周安排 · 已完成 ${weekDone.length}/${weekAll.length}`, weekAll, true)
        : `<section class="fce-group"><div class="fce-group-title">📌 本周安排</div>
           <p class="reading-hint">本周计划里还没有视频。到计划表「+ 加一项 → 视频学习」安排。</p></section>`}
      ${section('🗓 未完成（之前安排的）', todoOld)}
      ${doneOld.length ? `
      <details class="rd-done-fold" open>
        <summary>✅ 已完成（${doneOld.length}）<i>含成绩与最近评语</i></summary>
        <div class="reading-art-list">${doneOld.map((v) => row(v, false)).join('')}</div>
      </details>` : ''}
      ${extra.length ? `
      <details class="rd-done-fold">
        <summary>📎 计划外做过（${extra.length}）</summary>
        <div class="reading-art-list">${extra.map((v) => row(v, false)).join('')}</div>
      </details>` : ''}
      ${!weekAll.length && !todoOld.length && !doneOld.length && !extra.length
        ? '<p class="reading-hint">该生还没有任何视频安排或复述记录。</p>' : ''}
    `
    const go = body.querySelector('#va-go-grade')
    if (go) go.addEventListener('click', () => { location.hash = '/grading' })
  }

  // ---- 📚 内容管理：清单 CRUD ----
  async function mountContent(body) {
    body.innerHTML = '<p class="muted">加载中…</p>'
    let vids
    try {
      vids = await api.videoStudyList({ includeDisabled: true })
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    const on = vids.filter((v) => v.enabled)
    const off = vids.filter((v) => !v.enabled)
    const card = (v) => `
      <div class="reading-card static">
        <span class="reading-card-title">🎬 ${escapeHtml(v.title)}</span>
        <span class="reading-card-meta">
          <span class="tag" style="--cat-color:#8a6d3b">${escapeHtml(v.category || '综合')}</span>
          <span class="tag" style="--cat-color:#4a6fa5">${fmtMin(v.duration_sec || 0)}</span>
          <span class="tag" style="--cat-color:#7a5ba6">${(v.vocab || []).length} 生僻词</span>
          ${v.enabled ? '' : '<b class="fce-his-score bad">已下架</b>'}
          <a class="reading-btn small" href="#/videoStudy?vid=${encodeURIComponent(v.vid)}" title="预览视频页">预览</a>
          <button class="reading-btn small" data-edit="${escAttr(v.vid)}">编辑</button>
          <button class="reading-btn small" data-toggle="${escAttr(v.vid)}" data-on="${v.enabled ? 1 : 0}">${v.enabled ? '下架' : '上架'}</button>
          <button class="reading-btn small danger" data-del="${escAttr(v.vid)}">删除</button>
        </span>
      </div>`
    body.innerHTML = `
      <p class="reading-hint">上架 ${on.length} 个${off.length ? ` · 已下架 ${off.length} 个` : ''}。学生只看得到「被安排进周计划」的视频；安排走计划表「加一项」，进度看「学生进度」页签。</p>
      <section class="fce-group">
        <div class="fce-group-title">🎞 视频清单（${vids.length}）</div>
        <div class="reading-art-list">${on.map(card).join('') || '<p class="reading-hint">暂无上架视频</p>'}</div>
        ${off.length ? `
        <details class="rd-done-fold">
          <summary>已下架（${off.length}）<i>学生不可见；计划里已排的不出新行</i></summary>
          <div class="reading-art-list">${off.map(card).join('')}</div>
        </details>` : ''}
        <button class="reading-btn primary" id="va-add" style="margin-top:12px">＋ 新增视频</button>
      </section>
      <div class="va-editor" id="va-editor" hidden>
        <div class="va-editor-head">
          <label class="va-field">视频 ID
            <input id="va-ed-vid" placeholder="YouTube 链接 watch?v= 后面的 11 位" maxlength="16" />
          </label>
          <label class="va-field">标题
            <input id="va-ed-title" placeholder="如：10 Interesting Facts About Penguins" maxlength="120" />
          </label>
          <label class="va-field">类目
            <input id="va-ed-cat" placeholder="动物 / 科学 / 历史 / 地理" maxlength="24" />
          </label>
          <label class="va-field">时长（分钟）
            <input id="va-ed-dur" type="number" min="0" max="240" step="1" style="width:80px" />
          </label>
        </div>
        <div class="va-editor-cols">
          <label class="va-col">生僻词（每行一个：单词 | 中文提示）
            <textarea id="va-ed-vocab" rows="10" placeholder="predator | 掠食者&#10;blubber | 厚脂肪层&#10;camouflage | 伪装"></textarea>
          </label>
          <label class="va-col">复述提示（每行一个问题，按复述顺序）
            <textarea id="va-ed-hints" rows="10" placeholder="Where do they live?&#10;What do they eat?&#10;How do they stay warm?"></textarea>
          </label>
        </div>
        <div class="reading-editor-actions">
          <button class="reading-btn primary" id="va-ed-save">保存</button>
          <button class="reading-btn" id="va-ed-cancel">取消</button>
          <span class="reading-pick-info" id="va-ed-msg"></span>
        </div>
      </div>
    `

    let editVid = null
    const editor = body.querySelector('#va-editor')
    const openEditor = (v = null) => {
      editVid = v ? v.vid : null
      editor.hidden = false
      const vidInput = body.querySelector('#va-ed-vid')
      vidInput.value = v ? v.vid : ''
      vidInput.disabled = !!v // 编辑态 vid 不可改（vid 是主键）
      body.querySelector('#va-ed-title').value = v ? v.title : ''
      body.querySelector('#va-ed-cat').value = v ? (v.category || '') : ''
      body.querySelector('#va-ed-dur').value = v ? Math.round((v.duration_sec || 0) / 60) : ''
      body.querySelector('#va-ed-vocab').value = (v ? v.vocab : [])
        .map((w) => `${w.word} | ${w.gloss || ''}`.replace(/ \|$/, '')).join('\n')
      body.querySelector('#va-ed-hints').value = v ? (v.hints || '') : ''
      body.querySelector('#va-ed-msg').textContent = ''
      editor.scrollIntoView({ behavior: 'smooth' })
    }
    body.querySelector('#va-add').addEventListener('click', () => openEditor())
    body.querySelectorAll('[data-edit]').forEach((b) =>
      b.addEventListener('click', (e) => {
        e.stopPropagation()
        openEditor(vids.find((x) => x.vid === b.dataset.edit))
      }),
    )
    body.querySelector('#va-ed-cancel').addEventListener('click', () => (editor.hidden = true))
    body.querySelector('#va-ed-save').addEventListener('click', async () => {
      const vid = body.querySelector('#va-ed-vid').value.trim()
      const title = body.querySelector('#va-ed-title').value.trim()
      const category = body.querySelector('#va-ed-cat').value.trim()
      const durMin = Number(body.querySelector('#va-ed-dur').value) || 0
      // 生僻词行格式：word | gloss（宽容：无 | 时整行当 word）
      const vocab = body.querySelector('#va-ed-vocab').value.split(/\n+/)
        .map((line) => line.trim()).filter(Boolean).map((line) => {
          const i = line.indexOf('|')
          return i >= 0
            ? { word: line.slice(0, i).trim(), gloss: line.slice(i + 1).trim() }
            : { word: line, gloss: '' }
        })
      const hints = body.querySelector('#va-ed-hints').value.trim()
      const msg = body.querySelector('#va-ed-msg')
      if (!vid || !title) {
        msg.textContent = '视频 ID 与标题必填'
        return
      }
      const rec = { vid, title, category, duration_sec: Math.round(durMin * 60),
                    vocab, hints, enabled: true }
      try {
        if (editVid) await api.videoItemPut(editVid, rec)
        else await api.videoItemAdd(rec)
        render()
      } catch (e) {
        msg.textContent = `保存失败：${e.message}`
      }
    })
    // 上下架（保留其余字段：用现值回写）
    body.querySelectorAll('[data-toggle]').forEach((b) =>
      b.addEventListener('click', async (e) => {
        e.stopPropagation()
        const v = vids.find((x) => x.vid === b.dataset.toggle)
        if (!v) return
        const next = !v.enabled
        try {
          await api.videoItemPut(v.vid, {
            vid: v.vid, title: v.title, category: v.category || '',
            duration_sec: v.duration_sec || 0, vocab: v.vocab || [],
            hints: v.hints || '', enabled: next,
          })
          render()
        } catch (err) {
          alert(`操作失败：${err.message}`)
        }
      }),
    )
    body.querySelectorAll('[data-del]').forEach((b) =>
      b.addEventListener('click', async (e) => {
        e.stopPropagation()
        const v = vids.find((x) => x.vid === b.dataset.del)
        if (!confirm(`确定删除《${v?.title || ''}》？${off.some((x) => x.vid === b.dataset.del) ? '' : '学生端立即不可见；'}已提交的复述录音会保留。`)) return
        try {
          await api.videoItemDelete(b.dataset.del)
          render()
        } catch (err) {
          alert(`删除失败：${err.message}`)
        }
      }),
    )
  }
}

function fmtMin(sec) {
  const m = Math.round((sec || 0) / 60)
  return m >= 1 ? `${m} 分钟` : `${sec || 0} 秒`
}

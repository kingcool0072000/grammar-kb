import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 阅读内容管理：
// 1) 基础 FCE 阅读原文（50 段 base，按 Test/Part 分组，1 段为一篇）
// 2) 每段 base 下挂派生阅读（可持续新增/编辑/删除）
// 3) 学生录音批改（收听 → 10 分制打分 + 评语）
export async function mountReadingAdmin(el) {
  el.innerHTML = '<div class="view-head"><h1>阅读内容</h1><p>加载中…</p></div>'
  let bases, derived, recs
  try {
    ;[bases, derived, recs] = await Promise.all([
      api.readingArticles({ kind: 'base' }),
      api.readingArticles(),
      api.readingRecordings(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  const arts = [...bases, ...derived]
  renderHome(el, arts, recs)
}

function renderHome(el, arts, recs) {
  const pending = recs.filter((r) => r.status === 'pending')
  const bases = arts.filter((a) => a.kind === 'base')
  const derived = arts.filter((a) => a.kind === 'derived')
  el.innerHTML = `
    <div class="view-head">
      <h1>阅读内容</h1>
      <p>FCE 原文 ${bases.length} 段 · 派生文章 ${derived.length} 篇。点开原文段落可管理其派生阅读。</p>
    </div>
    ${pending.length ? `
      <button class="fce-essay-review-btn" id="rd-review">🎙 待批改录音（${pending.length}）</button>` : ''}
    <div class="course-grid">
      ${[...groupBase(bases).entries()].map(([test, parts]) => `
        <article class="course-card fce-paper-card" data-test="${test}" style="--cat-color:#0e7490">
          <div class="course-num">Test ${test}</div>
          <div class="course-title">FCE 阅读原文 · ${parts.reduce((s, p) => s + p.list.length, 0)} 段</div>
          <div class="course-meta">
            ${parts.map((p) => `<span class="tag" style="--cat-color:#0e7490">${p.label}</span>`).join('')}
            <span>${derivedCount(derived, test)} 篇派生</span>
          </div>
        </article>`).join('')}
    </div>
    ${recs.length ? `
    <section class="fce-group" style="margin-top:26px">
      <div class="fce-group-title">📊 最近录音提交</div>
      ${recs.slice(0, 8).map(recRow).join('')}
    </section>` : ''}
  `
  el.querySelectorAll('.fce-paper-card').forEach((card) =>
    card.addEventListener('click', () => renderTest(el, arts, Number(card.dataset.test), recs)),
  )
  const rv = el.querySelector('#rd-review')
  if (rv) rv.addEventListener('click', () => renderReview(el, recs))
}

function groupBase(bases) {
  // 按 test → part 聚合
  const tests = new Map()
  for (const b of bases) {
    const m = b.base_key.match(/^T(\d)P(\d)(?:-([A-D]))?$/)
    if (!m) continue
    const t = Number(m[1])
    if (!tests.has(t)) tests.set(t, new Map())
    const plabel = `P${m[2]}${m[3] ? `-${m[3]}` : ''}`
    const pm = tests.get(t)
    if (!pm.has(plabel)) pm.set(plabel, [])
    pm.get(plabel).push(b)
  }
  return new Map([...tests.entries()].map(([t, pm]) => [t, [...pm.entries()].map(([label, list]) => ({ label, list }))]))
}

function derivedCount(derived, test) {
  return derived.filter((a) => a.base_key.startsWith(`T${test}P`)).length
}

function recRow(r) {
  const score = r.status === 'graded'
    ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
    : '<b class="fce-his-score pend">待批改</b>'
  return `
    <div class="fce-his-row" data-rec="${r.id}">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
      ${score}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

// ---------- 单套 Test 的段落列表 ----------
function renderTest(el, arts, test, recs) {
  const bases = arts.filter(
    (a) => a.kind === 'base' && a.base_key.startsWith(`T${test}P`),
  )
  const derived = arts.filter(
    (a) => a.kind === 'derived' && a.base_key.startsWith(`T${test}P`),
  )
  const groups = groupBase(bases).get(test) || []
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="rd-back">← 返回</button>
      <h1>Test ${test} · 阅读原文段落</h1>
      <p>每段一篇；点开查看原文与派生阅读。</p>
    </div>
    ${groups.map((g) => `
      <section class="fce-group">
        <div class="fce-group-title">${g.label}（${g.list.length} 段）</div>
        <div class="reading-art-list">
          ${g.list.map((b) => {
            const d = derived.filter((x) => x.base_key === b.base_key)
            return `
            <button class="reading-card" data-id="${b.id}" data-key="${escapeHtml(b.base_key)}">
              <span class="reading-card-title">${escapeHtml(b.title)}</span>
              <span class="reading-card-meta">
                <span class="tag" style="--cat-color:#64748b">原文 ${b.words} 词</span>
                <span class="tag" style="--cat-color:#0e7490">派生 ${d.length} 篇</span>
              </span>
            </button>`
          }).join('')}
        </div>
      </section>`).join('')}
  `
  el.querySelector('#rd-back').addEventListener('click', () => renderHome(el, arts, recs))
  el.querySelectorAll('.reading-card').forEach((card) =>
    card.addEventListener('click', () =>
      renderBase(el, arts, Number(card.dataset.id), recs),
    ),
  )
}

// ---------- 某段 base：原文 + 派生管理 ----------
async function renderBase(el, arts, baseId, recs) {
  const base = arts.find((a) => a.id === baseId)
  let full
  try {
    full = await api.readingArticle(baseId)
  } catch (e) {
    alert(`加载原文失败：${e.message}`)
    return
  }
  const derived = arts.filter((a) => a.kind === 'derived' && a.base_key === base.base_key)
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="rd-back">← 返回</button>
      <h1>${escapeHtml(base.title)}</h1>
      <p>${escapeHtml(base.base_key)} · ${base.words} 词</p>
    </div>
    <section class="fce-group">
      <div class="fce-group-title">📖 原文（含正确答案）</div>
      <div class="reading-article admin">${full.text.split(/\n+/).map((p) => `<p>${escapeHtml(p).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}</p>`).join('')}</div>
    </section>
    <section class="fce-group">
      <div class="fce-group-title">🧬 派生阅读（${derived.length} 篇）</div>
      <div class="reading-art-list">
        ${derived.map((d) => `
          <div class="reading-card static">
            <span class="reading-card-title">${escapeHtml(d.title)}</span>
            <span class="reading-card-meta">
              <span class="tag" style="--cat-color:#0e7490">${d.words} 词</span>
              ${d.source ? `<span class="reading-card-src">${escapeHtml(d.source)}</span>` : ''}
              <button class="reading-btn small" data-edit="${d.id}">编辑</button>
              <button class="reading-btn small danger" data-del="${d.id}">删除</button>
            </span>
          </div>`).join('') || '<p class="reading-hint">暂无派生文章</p>'}
      </div>
      <button class="reading-btn primary" id="rd-add">＋ 新增派生阅读</button>
    </section>
    <div class="reading-editor" id="rd-editor" hidden>
      <div class="reading-editor-head">
        <input id="rd-ed-title" placeholder="标题（如：Emma 的面包房周六工）" />
        <input id="rd-ed-source" placeholder="来源（网站/杂志名）" />
      </div>
      <textarea id="rd-ed-text" rows="10" placeholder="粘贴派生文章正文（120-300 词，B2 难度，青少年主题）。空行分段。"></textarea>
      <div class="reading-editor-actions">
        <button class="reading-btn primary" id="rd-ed-save">保存</button>
        <button class="reading-btn" id="rd-ed-cancel">取消</button>
      </div>
    </div>
  `
  el.querySelector('#rd-back').addEventListener('click', () => {
    mountReadingAdmin(el).then(() => {
      // 返回后停在 Test 列表页即可（简单起见回首页）
    })
  })

  // 新增 / 编辑
  let editId = null
  const editor = el.querySelector('#rd-editor')
  const openEditor = (d = null) => {
    editId = d ? d.id : null
    editor.hidden = false
    el.querySelector('#rd-ed-title').value = d ? d.title : ''
    el.querySelector('#rd-ed-source').value = d ? (d.source || '') : ''
    el.querySelector('#rd-ed-text').value = d ? '' : '' // 正文编辑态走接口取全文
    if (d) {
      api.readingArticle(d.id).then((x) => {
        el.querySelector('#rd-ed-text').value = x.text
      })
    }
    editor.scrollIntoView({ behavior: 'smooth' })
  }
  el.querySelector('#rd-add').addEventListener('click', () => openEditor())
  el.querySelectorAll('[data-edit]').forEach((b) =>
    b.addEventListener('click', (e) => {
      e.stopPropagation()
      const d = derived.find((x) => x.id === Number(b.dataset.edit))
      openEditor(d)
    }),
  )
  el.querySelector('#rd-ed-cancel').addEventListener('click', () => (editor.hidden = true))
  el.querySelector('#rd-ed-save').addEventListener('click', async () => {
    const title = el.querySelector('#rd-ed-title').value.trim()
    const source = el.querySelector('#rd-ed-source').value.trim()
    const text = el.querySelector('#rd-ed-text').value.trim()
    if (text.length < 20) {
      alert('正文太短')
      return
    }
    try {
      if (editId) await api.readingUpdateDerived(editId, { title, source, text })
      else await api.readingAddDerived({ base_key: base.base_key, title, source, text })
      mountReadingAdmin(el)
    } catch (e) {
      alert(`保存失败：${e.message}`)
    }
  })
  el.querySelectorAll('[data-del]').forEach((b) =>
    b.addEventListener('click', async (e) => {
      e.stopPropagation()
      if (!confirm('确定删除这篇派生文章？')) return
      try {
        await api.readingDeleteDerived(Number(b.dataset.del))
        mountReadingAdmin(el)
      } catch (err) {
        alert(`删除失败：${err.message}`)
      }
    }),
  )
}

// ---------- 录音批改 ----------
async function renderReview(el, recs) {
  const pending = recs.filter((r) => r.status === 'pending')
  const list = pending.length ? pending : recs
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="rd-back">← 返回</button>
      <h1>录音批改${pending.length ? `（待批 ${pending.length}）` : ''}</h1>
      <p>试听学生录音，10 分制打分 + 评语。</p>
    </div>
    <div id="rd-review-list"></div>
  `
  const box = el.querySelector('#rd-review-list')
  for (const r of list.slice(0, 30)) {
    const art = await api.readingArticle(r.article_id).catch(() => null)
    const row = document.createElement('div')
    row.className = 'reading-review-card'
    row.innerHTML = `
      <div class="reading-review-head">
        <b>${escapeHtml(r.user)}</b> · ${escapeHtml(r.article_title || '')}
        <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
      </div>
      ${r.selected_text ? `
      <details class="reading-review-text" open><summary>学生朗读的选段</summary>
        <div class="reading-article picked-view">${escapeHtml(r.selected_text).split(/\n+/).map((p) => `<p>${p}</p>`).join('')}</div>
      </details>` : ''}
      <details class="reading-review-text"><summary>查看全文</summary>
        <div class="reading-article">${(art?.text || '').split(/\n+/).map((p) => `<p>${escapeHtml(p).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}</p>`).join('')}</div>
      </details>
      <audio controls preload="none" style="width:100%;margin:8px 0"></audio>
      <div class="reading-review-grade">
        <label>分数 <input type="number" min="0" max="10" value="${r.teacher_score ?? 8}" style="width:56px" data-score></label>
        <input placeholder="评语（流利度/发音/语调建议）" value="${escapeHtml(r.teacher_comment || '')}" data-comment style="flex:1" />
        <button class="reading-btn primary" data-grade>提交批改</button>
      </div>
      <div class="reading-review-msg"></div>
    `
    // 音频（懒加载 base64：首次点播放才拉取，设 src 后续播）
    const audio = row.querySelector('audio')
    audio.addEventListener('play', async () => {
      if (audio.src && audio.readyState > 0) return
      if (!audio.src) {
        const full = await api.readingRecording(r.id)
        audio.src = `data:${full.mime};base64,${full.audio_b64}`
        audio.load()
      }
      try { await audio.play() } catch { /* 浏览器策略拦截时由用户再点 */ }
    })
    row.querySelector('[data-grade]').addEventListener('click', async () => {
      const score = Number(row.querySelector('[data-score]').value)
      const comment = row.querySelector('[data-comment]').value
      if (!(score >= 0 && score <= 10)) {
        row.querySelector('.reading-review-msg').textContent = '分数须为 0-10'
        return
      }
      try {
        await api.readingGradeRecording(r.id, { score, comment })
        row.querySelector('.reading-review-msg').innerHTML = `✅ 已批改：${score}/10`
        row.querySelector('[data-grade]').disabled = true
      } catch (e) {
        row.querySelector('.reading-review-msg').textContent = `失败：${e.message}`
      }
    })
    box.append(row)
  }
  if (!list.length) box.innerHTML = '<p class="reading-hint">暂无录音提交</p>'
  el.querySelector('#rd-back').addEventListener('click', () => mountReadingAdmin(el))
}

function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

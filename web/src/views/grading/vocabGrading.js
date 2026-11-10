import { api } from '../../api.js'
import { escapeHtml, escAttr, todayIso } from '../../render.js'
import { toast } from '../../toast.js'

// 背单词 · 考试打分卡（自原批改看板视图整体平移，挂进备课中心背单词分区）。
//
// 签名：mountExamGrading(host, { state, students, refresh })
//   students  学生名单（登记表单的下拉选项；缺省回落 state.students）
//   refresh   可选，登记/修改/删除成功后调用——宿主可用来刷新分区角标
//             或重挂分区；不传则无操作。
// 卡片壳（gcard gd-exam-grading）由本函数渲染，宿主给一个空容器即可。

// ---- 考试打分卡：已存档单词试卷 + 逐卷登记成绩（关联学生，手动填分） ----

// 错词标记（登记表单折叠 chips）+ 错词徽标。样式随视图注入一次
// （对齐 analytics.js 的 ensureStyles 惯例），配色沿用本页暖色系。
const GEG_CSS = `
.geg-wrong { grid-column: 1 / -1; min-width: 0; }
.geg-wrong summary { cursor: pointer; font-size: 12px; color: var(--ink-soft, #64748b); user-select: none; }
.geg-wrong summary i { font-style: normal; }
.geg-wrong summary .geg-wn { color: #b42318; font-weight: 600; margin-left: 4px; }
.geg-wrong summary .geg-whint { color: var(--ink-faint, #94a3b8); margin-left: 8px; font-weight: 400; }
.geg-words { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
.geg-word {
  font: inherit; font-size: 11.5px; line-height: 1.4; padding: 2px 9px;
  border-radius: 999px; border: 1px solid var(--line, #e2e8f0);
  background: var(--bg-soft, #f8fafc); color: var(--ink-soft, #64748b); cursor: pointer;
}
.geg-word.sel { background: #fdeeea; border-color: #f5c6c0; color: #b42318; font-weight: 600; }
.geg-wbadge {
  font: inherit; font-size: 11px; line-height: 1.4; padding: 1px 8px;
  border-radius: 999px; border: 1px solid #f5c6c0; background: #fdeeea;
  color: #b42318; font-weight: 600; cursor: pointer;
}
.geg-word:focus-visible, .geg-wbadge:focus-visible { outline: 2px solid #c8a06a; outline-offset: 1px; }
`

function ensureGegStyles() {
  if (!document.getElementById('geg-wrong-style')) {
    const tag = document.createElement('style')
    tag.id = 'geg-wrong-style'
    tag.textContent = GEG_CSS
    document.head.appendChild(tag)
  }
}

// 错词数：优先 wrong_words 数组，退回 wrong_count（后端两版字段兼容）
function wrongCountOf(e) {
  if (!e) return 0
  if (Array.isArray(e.wrong_words)) return e.wrong_words.length
  const n = Number(e.wrong_count)
  return Number.isFinite(n) && n > 0 ? Math.round(n) : 0
}

// 明细里的错词词表。exam.wrong_words 是服务端从 answers 投影的词表
// （编辑预选的来源）；没有投影时从 answers 的答错行取。
function wordsFromExamDetail(d) {
  const exam = (d && d.exam) || {}
  if (Array.isArray(exam.wrong_words)) return exam.wrong_words.slice()
  const answers = Array.isArray(d && d.answers) ? d.answers : []
  const out = []
  for (const a of answers) {
    if (!a || !a.word) continue
    if ((a.correct === false || a.correct === 0) && !out.includes(a.word)) out.push(a.word)
  }
  return out
}

function sameWordSet(a, b) {
  const norm = (xs) => xs.map((w) => String(w || '').trim().toLowerCase()).filter(Boolean)
  const x = norm(a)
  const y = norm(b)
  if (x.length !== y.length) return false
  const s = new Set(x)
  return y.every((w) => s.has(w))
}

// 修改按钮回填用：已登记错词序列化进 data 属性（词不含 |，安全）
function wrongWordsKey(e) {
  return Array.isArray(e && e.wrong_words) ? e.wrong_words.join('|') : ''
}

function updateWrongSummary(row) {
  if (!row) return
  const hint = row.querySelector('.geg-wn')
  if (!hint) return
  const n = row.querySelectorAll('.geg-word.sel').length
  hint.textContent = n ? `已选 ${n}` : '未选'
}

// 卷面题型 → 中文（与生成卷五段一致）。空 qtype 留空，不编造标签；未知值原样展示。
const GEG_QTYPES = {
  form: '词形变化', spell: '拼写', ctx: '例句填空',
  zh2en: '中译英', en2zh: '英译中',
}
const gegQtypeLabel = (q) => (q ? (GEG_QTYPES[q] || String(q)) : '')

function closeVocabWrongOverlay() {
  const overlay = document.querySelector('.gd-focus-detail.geg-wwin')
  if (!overlay) return
  if (overlay._onKey) document.removeEventListener('keydown', overlay._onKey)
  overlay.remove()
}

// 已登记成绩 → 错词明细浮层（GET /vocab-exams/{id} → {exam, answers}）。
// 复用 gd-focus-detail 弹窗骨架与 gd-focus-chip 胶囊，不另起样式。
async function openVocabWrongOverlay(examId) {
  let d
  try {
    d = await api.vocabExamGet(Number(examId))
  } catch (err) {
    alert('加载错词明细失败：' + err.message)
    return
  }
  const exam = (d && d.exam) || {}
  const answers = Array.isArray(d && d.answers) ? d.answers : []
  const wrong = wordsFromExamDetail(d)
  const qByWord = new Map()
  for (const a of answers) {
    if (!a || !a.word || qByWord.has(a.word)) continue
    if (a.correct === false || a.correct === 0) qByWord.set(a.word, a.qtype || '')
  }
  closeVocabWrongOverlay() // 防重复叠加
  const overlay = document.createElement('div')
  overlay.className = 'gd-focus-detail geg-wwin'
  overlay.innerHTML = `
    <div class="gd-focus-detail-mask" data-close></div>
    <div class="gd-focus-detail-card">
      <div class="gd-focus-detail-head">
        <div class="gd-focus-detail-title">
          <b>错词明细</b>
          <span>${escapeHtml(exam.user || '')} · L${escapeHtml(exam.level ?? '-')} · ${escapeHtml(exam.score ?? '-')} 分 · ${escapeHtml(exam.exam_date || '')}${exam.paper_id ? ` · 卷 ${escapeHtml(exam.paper_id)}` : ''}</span>
        </div>
        <button class="reading-btn small" data-close>关闭</button>
      </div>
      <div class="gd-focus-detail-body">
        ${wrong.length
          ? `<p class="reading-hint" style="margin:0 0 8px">共 ${wrong.length} 个错词，已自动加入背词复习队列。</p>
             <div class="gd-focus-chips">${wrong.map((w) => {
               const q = qByWord.get(w)
               return `<i class="gd-focus-chip warn">${escapeHtml(w)}${q ? ` · ${escapeHtml(gegQtypeLabel(q))}` : ''}</i>`
             }).join('')}</div>`
          : '<p class="reading-hint">这场考试登记为全对，没有错词。</p>'}
      </div>
    </div>`
  document.body.append(overlay)
  const onKey = (e) => { if (e.key === 'Escape') closeVocabWrongOverlay() }
  document.addEventListener('keydown', onKey)
  overlay._onKey = onKey
  overlay.querySelectorAll('[data-close]').forEach((x) =>
    x.addEventListener('click', closeVocabWrongOverlay))
}

export async function mountExamGrading(host, { state, students, refresh } = {}) {
  if (!host) return
  const studs = (Array.isArray(students) && students.length)
    ? students
    : ((state && state.students) || [])
  ensureGegStyles()
  // 卡片壳在此渲染（原先由批改看板视图提供），内部引用改用 data 属性避免多实例 id 冲突
  host.innerHTML = `
    <section class="gcard gd-exam-grading">
      <header class="gcard-head">
        <h2>📝 考试打分 <i>单词试卷 · 登记、修改、删除</i></h2>
        <button class="chip" data-geg-all type="button">全部记录</button>
        <b class="gcard-n" data-geg-count>…</b>
      </header>
      <div class="gcard-list"><p class="reading-hint">加载试卷…</p></div>
    </section>`
  const card = host.querySelector('.gd-exam-grading')
  const list = card.querySelector('.gcard-list')
  const countEl = card.querySelector('[data-geg-count]')
  // 登记/修改/删除成功后通知宿主（刷新分区角标等）；不传则无操作
  const notify = () => { if (typeof refresh === 'function') { try { refresh() } catch { /* 宿主刷新失败不影响本卡 */ } } }
  let papers = []
  // 及格线：与词汇考试页同一条路径——响应里的 pass_score，取不到回退 80
  let passScore = 80
  // 混合卷登记级别默认 = 所选学生当前解锁级别（vocab_exam.record 拒绝 level=-1）
  const unlockByUser = new Map()
  try {
    const [grading, pairs] = await Promise.all([
      api.vocabPaperGrading(),
      Promise.all((studs || []).map(async (s) => {
        try {
          const d = await api.vocabExams({ user: s })
          const n = Number(d.unlocked_level)
          return [s, Number.isFinite(n) ? n : 0]
        } catch {
          return [s, 0]
        }
      })),
    ])
    papers = grading.papers || []
    const ps = Number(grading && grading.pass_score)
    if (Number.isFinite(ps) && ps > 0) passScore = Math.round(ps)
    for (const [s, n] of pairs) unlockByUser.set(s, n)
  } catch {
    list.innerHTML = '<p class="reading-hint">试卷列表加载失败（可刷新重试）。</p>'
    countEl.textContent = '—'
    return
  }
  const registerLevel = (user) => {
    const n = Number(unlockByUser.get(user))
    if (!Number.isFinite(n)) return 0
    return Math.max(0, Math.min(7, Math.round(n)))
  }
  const pending = papers.filter((p) => !(p.exams || []).length)
  countEl.textContent = `${pending.length} 卷待打分`
  notify()

  if (!papers.length) {
    list.innerHTML = '<p class="reading-hint">还没有存档试卷——备课中心 → 背单词 → 生成试卷并保存后，在这里登记线下考试成绩。</p>'
    return
  }

  const today = todayIso()
  const levelOpts = (selected) => [0, 1, 2, 3, 4, 5, 6, 7].map((n) =>
    `<option value="${n}" ${n === selected ? 'selected' : ''}>L${n}</option>`).join('')
  const rowHtml = (p) => {
    const graded = (p.exams || [])
    const isMix = Number(p.level) < 0 || String(p.paper_id || '').startsWith('MIX-')
    const defaultLv = registerLevel(studs[0])
    return `
    <div class="geg-row ${graded.length ? 'graded' : ''}" data-pid="${escapeHtml(p.paper_id)}">
      <div class="geg-info">
        <b>${isMix ? '🧩 已学混合卷' : `L${p.level} 卷`} ${escapeHtml(p.paper_id)}</b>
        <span>${p.count} 词 · ${escapeHtml((p.created_at || '').slice(0, 10))}</span>
      </div>
      <div class="geg-grades">
        ${graded.length
          ? graded.map((e) => {
              const wn = wrongCountOf(e)
              return `<span class="geg-chip ${e.score >= passScore ? 'ok' : ''}">${escapeHtml(e.user)} L${e.level} ${e.score} 分
              ${wn ? `<button type="button" class="geg-wbadge" data-gwrong-view="${e.id}" title="查看错词明细">错 ${wn}</button>` : ''}
              <button type="button" class="geg-op" data-gedit="${e.id}" data-user="${escapeHtml(e.user)}" data-level="${e.level}" data-score="${e.score}" data-date="${escapeHtml(e.exam_date || '')}" data-gwords="${escAttr(wrongWordsKey(e))}" data-gwn="${wn}">修改</button>
              <button type="button" class="geg-op" data-gdel="${e.id}">删除</button>
            </span>`
            }).join('')
          : '<span class="geg-chip none">未登记</span>'}
      </div>
      <div class="geg-form">
        <select class="geg-user" data-pid="${escapeHtml(p.paper_id)}">
          ${studs.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('')}
        </select>
        ${isMix ? `
        <label class="geg-lv-wrap">登记到
          <select class="geg-lv" data-pid="${escapeHtml(p.paper_id)}" title="混合卷跨级别。成绩记在所选级别上；单词级别是否变化取决于该生有没有被单独设定">
            ${levelOpts(defaultLv)}
          </select>
        </label>` : ''}
        <input class="geg-score" type="number" min="0" max="100" placeholder="分数" data-pid="${escapeHtml(p.paper_id)}">
        <input class="geg-date" type="date" value="${today}" data-pid="${escapeHtml(p.paper_id)}">
        <button class="reading-btn small primary" data-geg="${escapeHtml(p.paper_id)}" data-lv="${p.level}">登记</button>
      </div>
      <details class="geg-wrong">
        <summary>标记错词 <i class="geg-wn">未选</i><i class="geg-whint">点选=错词，全对不用标</i></summary>
        <div class="geg-words">
          ${(p.words || []).map((w) => `<button type="button" class="geg-word" data-w="${escapeHtml(w)}">${escapeHtml(w)}</button>`).join('')}
        </div>
      </details>
    </div>`
  }
  list.innerHTML = papers.map(rowHtml).join('')
  const allBtn = card.querySelector('[data-geg-all]')
  if (allBtn) allBtn.addEventListener('click', () => { location.hash = '/vocabExam' })

  // 换学生时，混合卷登记级别跟着该生当前解锁级别走
  list.addEventListener('change', (e) => {
    const userSel = e.target.closest('.geg-user')
    if (!userSel) return
    const lvSel = userSel.closest('.geg-row')?.querySelector('.geg-lv')
    if (!lvSel) return
    lvSel.value = String(registerLevel(userSel.value))
  })

  async function refreshRow(pid, row) {
    const fresh = (await api.vocabPaperGrading()).papers || []
    const np = fresh.find((x) => x.paper_id === pid)
    if (np && row.isConnected) row.outerHTML = rowHtml(np)
    countEl.textContent = `${fresh.filter((x) => !(x.exams || []).length).length} 卷待打分`
  }

  function rememberUnlock(user, level) {
    const n = Number(level)
    if (user && Number.isFinite(n)) unlockByUser.set(user, n)
  }

  // 登记 / 修改 / 删除 / 错词：list 级事件委托（行替换后监听不丢）
  list.addEventListener('click', async (e) => {
    const wvBtn = e.target.closest('[data-gwrong-view]')
    if (wvBtn) {
      openVocabWrongOverlay(wvBtn.dataset.gwrongView)
      return
    }
    const wordBtn = e.target.closest('.geg-word')
    if (wordBtn) {
      wordBtn.classList.toggle('sel')
      updateWrongSummary(wordBtn.closest('.geg-row'))
      return
    }
    const editBtn = e.target.closest('[data-gedit]')
    if (editBtn) {
      const row = editBtn.closest('.geg-row')
      if (!row) return
      const userSel = row.querySelector('.geg-user')
      if (userSel && [...userSel.options].some((o) => o.value === editBtn.dataset.user)) {
        userSel.value = editBtn.dataset.user
      }
      row.querySelector('.geg-score').value = editBtn.dataset.score
      row.querySelector('.geg-date').value = editBtn.dataset.date
      const lvSel = row.querySelector('.geg-lv')
      if (lvSel) lvSel.value = editBtn.dataset.level
      // 回填该成绩已登记的错词：对应 chips 预选中（有错词时顺带展开折叠区）。
      // 列表缺词表时懒加载明细：exam.wrong_words（从 answers 投影）为准。
      let wrongSel = String(editBtn.dataset.gwords || '').split('|').filter(Boolean)
      if (!wrongSel.length && Number(editBtn.dataset.gwn) > 0 && typeof api.vocabExamGet === 'function') {
        try {
          const wd = await api.vocabExamGet(Number(editBtn.dataset.gedit))
          wrongSel = wordsFromExamDetail(wd)
        } catch { /* 取不到就按无预选处理 */ }
      }
      if (row.querySelector('.geg-words')) {
        const sel = new Set(wrongSel.map((w) => String(w).trim().toLowerCase()))
        row.querySelectorAll('.geg-word').forEach((c) =>
          c.classList.toggle('sel', sel.has(String(c.dataset.w || '').trim().toLowerCase())))
        const det = row.querySelector('.geg-wrong')
        if (det && wrongSel.length) det.open = true
        updateWrongSummary(row)
        // 基线按「卷面上实际点亮的 chips」记，保存时没改勾选就不重传错词
        row.dataset.editWords = [...row.querySelectorAll('.geg-word.sel')]
          .map((c) => String(c.dataset.w || '')).join('|')
      } else {
        row.dataset.editWords = wrongSel.join('|')
      }
      row.dataset.editId = editBtn.dataset.gedit
      row.dataset.editLevel = editBtn.dataset.level
      const reg = row.querySelector('[data-geg]')
      if (reg) reg.textContent = '保存修改'
      row.querySelector('.geg-score').focus()
      return
    }
    const delBtn = e.target.closest('[data-gdel]')
    if (delBtn) {
      if (!confirm('删除这条考试记录？')) return
      const row = delBtn.closest('.geg-row')
      const pid = row?.dataset.pid
      delBtn.disabled = true
      try {
        await api.vocabExamDel(delBtn.dataset.gdel)
        toast('已删除')
        if (row && pid) await refreshRow(pid, row)
        notify()
      } catch (err) {
        alert(err.message)
        delBtn.disabled = false
      }
      return
    }
    const btn = e.target.closest('[data-geg]')
    if (!btn) return
    const pid = btn.dataset.geg
    let lv = Number(btn.dataset.lv)
    const row = list.querySelector(`.geg-row[data-pid="${CSS.escape(pid)}"]`)
    const user = row.querySelector('.geg-user').value
    const score = Number(row.querySelector('.geg-score').value)
    const date = row.querySelector('.geg-date').value
    if (!user) return alert('请选择学生')
    if (!(score >= 0 && score <= 100)) return alert('分数须为 0-100')
    if (!date) return alert('请选择考试日期')
    const editId = row.dataset.editId
    // 混合卷（lv<0 或 MIX）：用行内所选级别登记，否则 record 会 422
    const lvSel = row.querySelector('.geg-lv')
    if (editId && !lvSel) {
      lv = Number(row.dataset.editLevel)
    } else if (lvSel || lv < 0) {
      if (!lvSel) return alert('混合卷须选择登记级别（L0–L7）')
      lv = Number(lvSel.value)
      if (!(lv >= 0 && lv <= 7)) return alert('混合卷须选择登记级别（L0–L7）')
    }
    const before = Number(unlockByUser.get(user))
    // 新登记：空数组也传（[]=这份成绩没有错词）。
    // 修改：勾选与回填一致时不传 wrong_words（null=不动明细、不回流）；
    // 勾选变了才传新数组。服务端仍只把相对旧明细新增的词送进复习队列。
    const wrongWords = [...row.querySelectorAll('.geg-word.sel')].map((c) => String(c.dataset.w || ''))
    const payload = { user, level: lv, score, exam_date: date }
    if (editId) {
      const prev = String(row.dataset.editWords || '').split('|').filter(Boolean)
      if (!sameWordSet(prev, wrongWords)) payload.wrong_words = wrongWords
    } else {
      payload.wrong_words = wrongWords
    }
    btn.disabled = true
    btn.textContent = editId ? '保存中…' : '登记中…'
    try {
      const r = editId
        ? await api.vocabExamUpdate(editId, payload)
        : await api.vocabExamAdd({ ...payload, paper_id: pid })
      rememberUnlock(user, r.unlocked_level)
      if (r.previous_user) rememberUnlock(r.previous_user, r.previous_unlocked_level)
      const after = Number(r.unlocked_level)
      let msg = `${editId ? '已修改' : '已登记'} ${user} ${score} 分`
      if (Array.isArray(payload.wrong_words)) {
        if (payload.wrong_words.length) {
          msg += editId
            ? `，错词已更新为 ${payload.wrong_words.length} 个`
            : `，已记 ${payload.wrong_words.length} 个错词并加入复习队列`
        } else if (editId) {
          msg += '，错词已清空'
        }
      }
      if (Number.isFinite(before) && Number.isFinite(after) && after !== before) {
        msg += `，单词级别现为 L${after}`
      }
      toast(msg)
      await refreshRow(pid, row)
      notify()
    } catch (err) {
      alert((editId ? '修改失败：' : '登记失败：') + err.message)
      btn.disabled = false
      btn.textContent = editId ? '保存修改' : '登记'
    }
  })
}

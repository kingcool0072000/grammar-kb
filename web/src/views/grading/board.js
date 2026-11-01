import { api } from '../../api.js'
import { escapeHtml, todayIso } from '../../render.js'
import { fmtDur } from '../../fmt.js'
import { renderReadingReview } from '../readingReview.js'
import { renderVideoReview, videoRetellRow } from '../videoReview.js'
import { focusRow, focusTitle } from './shared.js'
import { openFocusDetail } from './focus.js'

// 批改中心 · 批改看板子 Tab（默认）：五类待办队列（待批录音/待批复述/待批作文/
// 异常专注/考试打分）+ 各待办列表与批改视图入口 + 已处理折叠区。

export async function mountBoard(el, { state, render, boardSnapshots, students, go }) {
    const student = state.student
    const openRecordingReview = async () => {
      // 渲染进卡片网格容器（保留外层页头/子Tab），返回时重挂看板
      const host = document.createElement('div')
      el.replaceChildren(host)
      host.innerHTML = '<p class="muted">加载录音批改…</p>'
      try {
        const recs = await api.readingRecordings({ limit: 200, user: student })
        await renderReadingReview(host, recs, {
          onBack: () => {
            state.tab = 'board'
            // 清除旧的批改视图节点后重建看板；返回值让 readingReview
            // 知道回调已接管，不走同 hash 的路由兜底。
            render()
            return true
          },
        })
      } catch (e) {
        host.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      }
    }
    const openVideoReview = async () => {
      const host = document.createElement('div')
      el.replaceChildren(host)
      host.innerHTML = '<p class="muted">加载视频复述批改…</p>'
      try {
        const retells = await api.videoRetells({ limit: 200, user: student })
        await renderVideoReview(host, retells, {
          onBack: () => {
            state.tab = 'board'
            render()
            return true
          },
        })
      } catch (e) {
        host.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      }
    }
    el.innerHTML = '<p class="muted">加载中…</p>'
    let recs, fceSubs, recite, exams, focus, vretells
    const boardSnapshot = boardSnapshots.get(student)
    if (boardSnapshot) {
      ({ recs, fceSubs, recite, exams, focus, vretells } = boardSnapshot)
    } else {
      try {
        const timed = (p, ms = 8000) => Promise.race([
          p, new Promise((resolve) => setTimeout(() => resolve([]), ms)),
        ]).catch(() => [])
        ;[recs, fceSubs, recite, exams, focus, vretells] = await Promise.all([
          timed(api.readingRecordings({ limit: 200, user: student })),
          timed(api.fceSubmissions({ limit: 200, user: student })),
          timed(api.reciteSessions({ limit: 100, user: student })),
          timed(api.examsList(student)),
          timed(api.focusSessions({ limit: 50, user: student })),
          timed(api.videoRetells({ limit: 200, user: student })),
        ])
        focus = Array.isArray(focus) ? focus : []
        await attachFocusRw(focus)
        boardSnapshots.set(student, { recs, fceSubs, recite, exams, focus, vretells })
      } catch (e) {
        el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
        return
      }
    }

  recs = Array.isArray(recs) ? recs : []
  fceSubs = Array.isArray(fceSubs) ? fceSubs : []
  exams = Array.isArray(exams) ? exams : []
  focus = Array.isArray(focus) ? focus : []
  recite = Array.isArray(recite) ? recite : []
  vretells = Array.isArray(vretells) ? vretells : []

  const pendRec = recs.filter((r) => r.status === 'pending')
  const pendVR = vretells.filter((r) => r.status === 'pending')
  const pendEssay = fceSubs.filter((s) => s.status === 'pending')
  const abnormal = focus.filter(isAbnormalFocus)
  const doneRec = recs.filter((r) => r.status !== 'pending')
  const doneVR = vretells.filter((r) => r.status !== 'pending')
  const doneEssay = fceSubs.filter((s) => s.status !== 'pending')
  const calmFocus = focus.filter((s) => !isAbnormalFocus(s) && !isZombieFocus(s))
  const handledN = doneRec.length + doneVR.length + doneEssay.length + calmFocus.length + exams.length

  const queueBlock = (key, title, n, rows, empty, more) => `
    <section class="g-queue-block ${n ? 'has-pend' : ''}" data-card="${key}">
      <header class="gcard-head">
        <h2>${title}</h2>
        ${n ? `<b class="gcard-pend">待处理 ${n}</b>` : '<b class="gcard-n">0</b>'}
      </header>
      <div class="gcard-list">
        ${rows || `<p class="reading-hint">${empty}</p>`}
      </div>
      ${more || ''}
    </section>`

  el.innerHTML = `
    <section class="g-queue">
      <header class="g-queue-head">
        <h2>待处理</h2>
        <p class="g-queue-note">待批录音 → 待批复述 → 待批作文 → 异常专注。已处理的收在下面。</p>
      </header>
      ${queueBlock(
        'recording',
        '1 · 待批录音',
        pendRec.length,
        pendRec.map((r) => recRow(r)).join(''),
        '没有待批录音',
        pendRec.length ? `<button class="gcard-more" data-all="recording">去批改录音（${pendRec.length}）→</button>` : '',
      )}
      ${queueBlock(
        'vretell',
        '2 · 待批复述',
        pendVR.length,
        pendVR.map((r) => videoRetellRow(r)).join(''),
        '没有待批的视频复述',
        pendVR.length ? `<button class="gcard-more" data-all="vretell">去批批复述（${pendVR.length}）→</button>` : '',
      )}
      ${queueBlock(
        'essay',
        '3 · 待批作文',
        pendEssay.length,
        pendEssay.map((s) => fceRow(s)).join(''),
        '没有待批作文',
        pendEssay.length ? `<button class="gcard-more" data-all="essay">去批改作文（${pendEssay.length}）→</button>` : '',
      )}
      ${queueBlock(
        'focus',
        '4 · 异常专注',
        abnormal.length,
        abnormal.map((s) => abnormalFocusRow(s)).join(''),
        '最近没有需要看的异常专注',
        abnormal.length ? `<button class="gcard-more" data-all="focus">打开学习日志 →</button>` : '',
      )}
    </section>
    <section class="gcard gd-exam-grading" id="gd-exam-grading">
      <header class="gcard-head">
        <h2>📝 考试打分 <i>单词试卷 · 登记、修改、删除</i></h2>
        <button class="chip" id="geg-all" type="button">全部记录</button>
        <b class="gcard-n" id="geg-count">…</b>
      </header>
      <div class="gcard-list"><p class="reading-hint">加载试卷…</p></div>
    </section>
    <details class="g-handled">
      <summary>已处理（${handledN}）<i>已批录音、已批复述、已阅练习、正常专注、哈一成绩</i></summary>
      <div class="g-handled-grid">
        <section class="gcard" data-card="recording-done">
          <header class="gcard-head"><h2>已批录音</h2><b class="gcard-n">${doneRec.length}</b></header>
          <div class="gcard-list">${doneRec.slice(0, 6).map((r) => recRow(r)).join('') || '<p class="reading-hint">暂无</p>'}</div>
          ${doneRec.length ? `<button class="gcard-more" data-all="recording">查看全部录音 →</button>` : ''}
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>已批复述</h2><b class="gcard-n">${doneVR.length}</b></header>
          <div class="gcard-list">${doneVR.slice(0, 6).map((r) => videoRetellRow(r)).join('') || '<p class="reading-hint">暂无</p>'}</div>
          ${doneVR.length ? `<button class="gcard-more" data-all="vretell">查看全部复述 →</button>` : ''}
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>已阅作文与练习</h2><b class="gcard-n">${doneEssay.length}</b></header>
          <div class="gcard-list">${doneEssay.slice(0, 6).map((s) => fceRow(s)).join('') || '<p class="reading-hint">暂无</p>'}</div>
          ${doneEssay.length ? `<button class="gcard-more" data-all="essay">查看 FCE 记录 →</button>` : ''}
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>正常专注</h2><b class="gcard-n">${calmFocus.length}</b></header>
          <div class="gcard-list">${calmFocus.slice(0, 4).map((s) => focusRow(s)).join('') || '<p class="reading-hint">暂无</p>'}</div>
          <button class="gcard-more" data-all="focus">学习日志 →</button>
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>哈一成绩</h2><b class="gcard-n">${exams.length}</b></header>
          <div class="gcard-list">${exams.slice(0, 6).map((e) => `
            <div class="fce-his-row">
              <span class="fce-his-what">${escapeHtml(e.user || '')} · 第 ${e.lecture} 讲</span>
              <b class="fce-his-score ${e.score >= 90 ? 'ok' : e.score >= 70 ? '' : 'bad'}">${e.score}</b>
              <span class="fce-his-date">${e.date}${e.wrong && e.wrong.length ? ` · 错 ${e.wrong.length} 题` : ''}</span>
            </div>`).join('') || '<p class="reading-hint">暂无成绩</p>'}</div>
          ${exams.length ? `<button class="gcard-more" data-all="haya-exam">成绩页 →</button>` : ''}
        </section>
      </div>
      ${recite.length ? `<p class="reading-hint">背词记录 ${recite.length} 条在学习日志里。</p>` : ''}
    </details>
  `
  mountExamGrading(el.querySelector('#gd-exam-grading'), students)

  const showAll = {
    recording: async () => openRecordingReview(),
    vretell: async () => openVideoReview(),
    essay: () => go('essay'),
    'haya-exam': () => go('haya-exam'),
    focus: () => { state.tab = 'log'; render() },
  }
  el.querySelectorAll('[data-all]').forEach((b) =>
    b.addEventListener('click', () => {
      const fn = showAll[b.dataset.all]
      if (fn) fn()
    }))
  const recList = el.querySelector('[data-card="recording"] .gcard-list')
  if (recList) recList.addEventListener('click', (e) => {
    if (e.target.closest('[data-rec-del],[data-rec-edit],audio,a')) return
    if (!e.target.closest('.fce-his-row')) return
    openRecordingReview()
  })
  const vrList = el.querySelector('[data-card="vretell"] .gcard-list')
  if (vrList) vrList.addEventListener('click', (e) => {
    if (e.target.closest('audio')) return
    if (!e.target.closest('.fce-his-row')) return
    openVideoReview()
  })
  const essayList = el.querySelector('[data-card="essay"] .gcard-list')
  if (essayList) essayList.addEventListener('click', (e) => {
    if (e.target.closest('.fce-his-row')) go('essay')
  })
  el.querySelectorAll('[data-focus-id]').forEach((row) =>
    row.addEventListener('click', () => openFocusDetail(Number(row.dataset.focusId))))
  // readingAdmin 跳转联动：自动展开批改视图
  if (sessionStorage.getItem('gkb-open-review') === '1') {
    sessionStorage.removeItem('gkb-open-review')
    openRecordingReview()
  }
}

// ---- 考试打分卡：已存档单词试卷 + 逐卷登记成绩（关联学生，手动填分） ----
function toast(msg) {
  const t = document.createElement('div')
  t.className = 'plan-toast'
  t.textContent = msg
  document.body.appendChild(t)
  setTimeout(() => t.remove(), 3200)
}

async function mountExamGrading(host, students) {
  if (!host) return
  const list = host.querySelector('.gcard-list')
  const countEl = host.querySelector('#geg-count')
  let papers = []
  // 混合卷登记级别默认 = 所选学生当前解锁级别（vocab_exam.record 拒绝 level=-1）
  const unlockByUser = new Map()
  try {
    const [grading, pairs] = await Promise.all([
      api.vocabPaperGrading(),
      Promise.all((students || []).map(async (s) => {
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
    const defaultLv = registerLevel(students[0])
    return `
    <div class="geg-row ${graded.length ? 'graded' : ''}" data-pid="${escapeHtml(p.paper_id)}">
      <div class="geg-info">
        <b>${isMix ? '🧩 已学混合卷' : `L${p.level} 卷`} ${escapeHtml(p.paper_id)}</b>
        <span>${p.count} 词 · ${escapeHtml((p.created_at || '').slice(0, 10))}</span>
      </div>
      <div class="geg-grades">
        ${graded.length
          ? graded.map((e) => `<span class="geg-chip ${e.score >= 80 ? 'ok' : ''}">${escapeHtml(e.user)} L${e.level} ${e.score} 分
              <button type="button" class="geg-op" data-gedit="${e.id}" data-user="${escapeHtml(e.user)}" data-level="${e.level}" data-score="${e.score}" data-date="${escapeHtml(e.exam_date || '')}">修改</button>
              <button type="button" class="geg-op" data-gdel="${e.id}">删除</button>
            </span>`).join('')
          : '<span class="geg-chip none">未登记</span>'}
      </div>
      <div class="geg-form">
        <select class="geg-user" data-pid="${escapeHtml(p.paper_id)}">
          ${students.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('')}
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
    </div>`
  }
  list.innerHTML = papers.map(rowHtml).join('')
  const allBtn = host.querySelector('#geg-all')
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

  // 登记 / 修改 / 删除：list 级事件委托（行替换后监听不丢）
  list.addEventListener('click', async (e) => {
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
    btn.disabled = true
    btn.textContent = editId ? '保存中…' : '登记中…'
    try {
      const payload = { user, level: lv, score, exam_date: date }
      const r = editId
        ? await api.vocabExamUpdate(editId, payload)
        : await api.vocabExamAdd({ ...payload, paper_id: pid })
      rememberUnlock(user, r.unlocked_level)
      if (r.previous_user) rememberUnlock(r.previous_user, r.previous_unlocked_level)
      const after = Number(r.unlocked_level)
      let msg = `${editId ? '已修改' : '已登记'} ${user} ${score} 分`
      if (Number.isFinite(before) && Number.isFinite(after) && after !== before) {
        msg += `，单词级别现为 L${after}`
      }
      toast(msg)
      await refreshRow(pid, row)
    } catch (err) {
      alert((editId ? '修改失败：' : '登记失败：') + err.message)
      btn.disabled = false
      btn.textContent = editId ? '保存修改' : '登记'
    }
  })
}

function isZombieFocus(s) {
  const flags = (s.rw && s.rw.flags) || []
  return flags.includes('无有效阅读') && !(s.rw && s.rw.words) && !(s.rw && s.rw.overlap)
}

function isAbnormalFocus(s) {
  if (isZombieFocus(s)) return false
  const flags = ((s.rw && s.rw.flags) || []).filter((f) => f !== '无有效阅读')
  if (flags.length) return true
  const link = s.rw && s.rw.link
  if (link && link !== 'next' && link !== 'first') return true
  if (typeof s.score === 'number' && s.score < 60) return true
  if ((s.fast_scroll_flags || 0) > 0) return true
  const start = Number(s.percent_start)
  const end = Number(s.percent_end)
  if (Number.isFinite(start) && Number.isFinite(end) && end + 1 < start) return true
  return false
}

async function attachFocusRw(focus) {
  const libIds = focus
    .filter((s) => (s.module || 'library') === 'library')
    .map((s) => s.id)
  if (!libIds.length) return
  try {
    const wm = await api.focusWords(libIds.slice(0, 50))
    if (!wm || typeof wm !== 'object') return
    for (const s of focus) {
      const hit = wm[s.id] || wm[String(s.id)]
      if (hit) s.rw = hit
    }
  } catch { /* 没有逐会话标记时仍按分数 / 快滚判断 */ }
}

function abnormalFocusRow(s) {
  const flags = ((s.rw && s.rw.flags) || []).filter((f) => f !== '无有效阅读')
  const link = s.rw && s.rw.link && s.rw.link !== 'next' && s.rw.link !== 'first'
    ? (s.rw.link_note || s.rw.link) : ''
  const bits = [...flags]
  if (link) bits.push(link)
  if (typeof s.score === 'number' && s.score < 60) bits.push(`专注 ${Math.round(s.score)}`)
  if (s.fast_scroll_flags) bits.push(`快滚 ${s.fast_scroll_flags}`)
  return `
    <div class="fce-his-row gd-focus-row" data-focus-id="${s.id}">
      <span class="fce-his-what">${escapeHtml(focusTitle(s))}</span>
      <b class="fce-his-score bad">异常</b>
      <span class="fce-his-date">${bits.map((b) => escapeHtml(String(b))).join(' · ') || '需要看一眼'}</span>
    </div>`
}

function recRow(r) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
      ${r.status === 'graded'
        ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
        : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

function fceRow(s) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(s.user)} · T${s.test_id} P${s.part}</span>
      ${s.status === 'auto'
        ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b>`
        : s.status === 'graded'
          ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b>`
          : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${s.duration_sec ? fmtDur(s.duration_sec) + ' · ' : ''}${(s.created_at || '').slice(0, 10)}</span>
    </div>`
}

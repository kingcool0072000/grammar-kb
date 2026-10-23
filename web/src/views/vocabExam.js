import { api } from '../api.js'
import { escapeHtml, todayIso } from '../render.js'

// 教师版 · 词汇考试记录：登记、修改、删除。
// 成绩本身不改写已单独设定的单词级别（级别在背单词级别页设置）。
const LEVELS = [0, 1, 2, 3, 4, 5, 6, 7]

export async function mountVocabExam(el) {
  const today = todayIso()
  el.innerHTML = `
    <div class="view-head">
      <h1>词汇考试</h1>
      <p>登记、修改、删除线下考试记录（仅教师）。及格线 80 分。学生的单词级别在背单词级别页单独设置；还没单独设置过的学生，及格仍会解锁下一级。</p>
    </div>
    <div id="ve-body"><p class="muted">加载中…</p></div>
  `
  const body = el.querySelector('#ve-body')

  let students = []
  try {
    const r = await api.users()
    students = (r.users || []).filter((u) => u.role === 'student')
  } catch (e) {
    body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }

  let filter = ''
  let editing = null
  let vex = { exams: [] }

  const levelOpts = LEVELS.map((l) => `<option value="${l}">L${l}</option>`).join('')
  const studentOpts = students.map((u) =>
    `<option value="${escapeHtml(u.user)}">${escapeHtml(u.user)}</option>`).join('')

  body.innerHTML = `
    <section class="fce-group plan-vocab-exam">
      <div class="plan-vex-row">
        <label>查看
          <select id="ve-filter">
            <option value="">全部学生</option>
            ${studentOpts}
          </select>
        </label>
      </div>
      <p class="plan-vex-state" id="ve-state"></p>
      <div class="plan-vex-row">
        <label>学生
          <select id="pv-user">${studentOpts || '<option value="">（无学生）</option>'}</select>
        </label>
        <label>级别
          <select id="pv-level">${levelOpts}</select>
        </label>
        <label>日期 <input id="pv-date" type="date" value="${today}"/></label>
        <label>得分 <input id="pv-score" type="number" min="0" max="100" placeholder="0-100" style="width:90px"/></label>
        <label>备注 <input id="pv-note" type="text" maxlength="200" placeholder="选填" style="width:160px"/></label>
        <button class="btn-primary" id="pv-save" type="button">登记成绩</button>
        <button class="chip" id="pv-cancel" type="button" hidden>取消修改</button>
      </div>
      <div id="ve-list"></div>
    </section>`

  const listEl = body.querySelector('#ve-list')

  function resetForm() {
    editing = null
    body.querySelector('#pv-save').textContent = '登记成绩'
    body.querySelector('#pv-cancel').hidden = true
    body.querySelector('#pv-score').value = ''
    body.querySelector('#pv-note').value = ''
    body.querySelector('#pv-date').value = today
  }

  function paintState() {
    const host = body.querySelector('#ve-state')
    if (!filter) {
      host.textContent = '全部考试记录。点「修改」可改正记错的级别、分数、日期或学生。'
      return
    }
    const lv = vex.unlocked_level ?? 0
    const passed = vex.passed_levels || []
    const how = vex.level_configured ? '教师已单独设定' : '尚未单独设定（及格会解锁下一级）'
    host.innerHTML = `${escapeHtml(filter)} 的单词级别 <b>L${lv}</b>（${how}）· 已及格：${
      passed.length ? passed.map((l) => 'L' + l).join('、') : '无'}`
  }

  function paintList() {
    paintState()
    const items = vex.exams || []
    if (!items.length) {
      listEl.innerHTML = '<p class="muted" style="margin:8px 0 0">还没有考试记录。</p>'
      return
    }
    listEl.innerHTML = `<div class="ve-list">${items.map((x) => `
      <div class="ve-row ${x.score >= 80 ? 'ok' : 'fail'}">
        <b>${escapeHtml(x.user)}</b>
        <span>L${x.level}</span>
        <span>${x.score} 分</span>
        <span>${escapeHtml(x.exam_date || '')}</span>
        ${x.note ? `<span class="ve-note">${escapeHtml(x.note)}</span>` : ''}
        ${x.paper_id ? `<span class="ve-note">卷 ${escapeHtml(x.paper_id)}</span>` : ''}
        <span class="ve-ops">
          <button type="button" class="chip" data-edit="${x.id}">修改</button>
          <button type="button" class="chip" data-del="${x.id}">删除</button>
        </span>
      </div>`).join('')}</div>`
  }

  async function reload() {
    try {
      vex = filter
        ? await api.vocabExams({ user: filter })
        : await api.vocabExams({ includeAll: true })
    } catch (e) {
      listEl.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    paintList()
  }

  function readForm() {
    const user = body.querySelector('#pv-user').value
    const level = Number(body.querySelector('#pv-level').value)
    const score = Number(body.querySelector('#pv-score').value)
    const exam_date = body.querySelector('#pv-date').value
    const note = body.querySelector('#pv-note').value.trim()
    if (!user) {
      alert('请先在学生管理里添加学生')
      return null
    }
    if (!(score >= 0 && score <= 100) || !exam_date) {
      alert('得分与日期须填完整')
      return null
    }
    if (!(level >= 0 && level <= 7)) {
      alert('级别须为 L0–L7')
      return null
    }
    return { user, level, score, exam_date, note }
  }

  body.querySelector('#ve-filter').addEventListener('change', async (e) => {
    filter = e.target.value
    await reload()
  })
  body.querySelector('#pv-cancel').addEventListener('click', () => resetForm())
  body.querySelector('#pv-save').addEventListener('click', async () => {
    const rec = readForm()
    if (!rec) return
    const btn = body.querySelector('#pv-save')
    btn.disabled = true
    try {
      if (editing) {
        await api.vocabExamUpdate(editing.id, rec)
        toast('已保存修改')
      } else {
        await api.vocabExamAdd(rec)
        toast('已登记')
      }
      resetForm()
      await reload()
    } catch (e) {
      alert((editing ? '修改失败：' : '登记失败：') + e.message)
    } finally {
      btn.disabled = false
    }
  })
  listEl.addEventListener('click', async (e) => {
    const editBtn = e.target.closest('[data-edit]')
    if (editBtn) {
      const exam = (vex.exams || []).find((x) => String(x.id) === editBtn.dataset.edit)
      if (!exam) return
      editing = exam
      const userSel = body.querySelector('#pv-user')
      if (![...userSel.options].some((o) => o.value === exam.user)) {
        const opt = document.createElement('option')
        opt.value = exam.user
        opt.textContent = exam.user
        userSel.append(opt)
      }
      userSel.value = exam.user
      body.querySelector('#pv-level').value = String(exam.level)
      body.querySelector('#pv-date').value = exam.exam_date || today
      body.querySelector('#pv-score').value = exam.score
      body.querySelector('#pv-note').value = exam.note || ''
      body.querySelector('#pv-save').textContent = '保存修改'
      body.querySelector('#pv-cancel').hidden = false
      body.querySelector('#pv-score').focus()
      return
    }
    const delBtn = e.target.closest('[data-del]')
    if (!delBtn) return
    if (!confirm('删除这条考试记录？')) return
    try {
      await api.vocabExamDel(delBtn.dataset.del)
      if (editing && String(editing.id) === delBtn.dataset.del) resetForm()
      toast('已删除')
      await reload()
    } catch (err) {
      alert(err.message)
    }
  })

  await reload()

  function toast(msg) {
    const t = document.createElement('div')
    t.className = 'plan-toast'
    t.textContent = msg
    document.body.appendChild(t)
    setTimeout(() => t.remove(), 3200)
  }
}

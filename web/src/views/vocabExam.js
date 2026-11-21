import { api } from '../api.js'
import { escapeHtml, escAttr, todayIso } from '../render.js'
import { FORM_CN, vocabPaperShort } from '../fmt.js'
import { toast } from '../toast.js'
import { mountExamGrading } from './grading/vocabGrading.js'

// 教师版 · 词汇考试（出题 + 考试打分 + 历史成绩记录）：
//   1. 试卷生成：按学生全部已学单词出题、预览、保存存档、打印、历史卷管理
//   2. 考试打分：按存档试卷登记成绩、标记错词（自动回流复习队列）
//   3. 历史成绩记录：按学生筛选、补录/修改/删除、查看错词明细
const LEVELS = [0, 1, 2, 3, 4, 5, 6, 7]

export async function mountVocabExam(el) {
  const today = todayIso()
  el.innerHTML = `
    <div class="view-head">
      <h1>单词考试 · 出题与打分</h1>
      <p>按学生已学全部单词生成试卷并存档 → 按卷登记成绩与标记错词（错词自动回流复习队列）→ 查看与维护历史成绩。及格线 <b id="ve-pass">80</b> 分。单词级别若已手动锁定，分数不再改级别；没锁定过的，满 80 分才开下一级。</p>
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
  let papers = []
  try {
    const hist = await api.vocabPaperHistory(-1, { limit: 200 })
    papers = hist.papers || []
  } catch { papers = [] }
  // 及格线：优先读 GET /vocab-exams 响应里的 pass_score，取不到回退 80
  let passScore = 80

  const studentNames = students.map((u) => u.user)
  const levelOpts = LEVELS.map((l) => `<option value="${l}">L${l}</option>`).join('')
  const studentOpts = students.map((u) =>
    `<option value="${escapeHtml(u.user)}">${escapeHtml(u.user)}</option>`).join('')
  const paperOptHtml = (p) => {
    const lab = vocabPaperShort(p)
    const who = p.user ? ` · ${p.user}` : ''
    return `<option value="${escAttr(p.paper_id)}" data-lv="${p.level}" title="${escAttr(lab.full)}">${escapeHtml(lab.short)}${escapeHtml(who)}</option>`
  }

  body.innerHTML = `
    <!-- 1. 单词考试 · 试卷生成（仅按所选学生全部已学单词出题） -->
    <section class="ve-panel">
      <header class="vl-head">
        <h3>📝 单词考试 · 试卷生成</h3>
        <div class="vl-tools">
          <label class="vl-scope">学生
            <select id="vl-scope-user" title="按该学生的全部已学单词出题">
              ${studentOpts || '<option value="">（无学生账号）</option>'}
            </select>
          </label>
          <select id="vl-preset" title="卷型预设：标准卷按默认配比；强化卷/回收卷优先混入所选学生的积压错词">
            <option value="standard">标准卷</option>
            <option value="reinforce">复习强化卷</option>
            <option value="recycle">错词回收卷</option>
          </select>
          <button class="btn-primary" id="vl-gen" type="button">🎲 生成试卷</button>
        </div>
      </header>
      <p class="muted" style="margin:0 0 8px;font-size:13px;line-height:1.55">按所选学生<b>全部已学单词</b>（进度里出现过即可，含尚未掌握）跨 L0–L7 混合出题（卷号 MIX-）；随机选词并规避人名/地名，词形、拼写、中译英给词性，英译中只给英文。标准卷默认 50 题（词形 6 · 拼写 8 · 例句 12 · 中译英 12 · 英译中 12），<b>复习强化卷 / 错词回收卷</b>按所选学生的积压错词出题。流程：生成 → 预览 → 保存存档后即可在下方打分。</p>
      <details class="vl-paper-adv" style="margin:6px 0 10px">
        <summary style="cursor:pointer;user-select:none;font-size:13px;color:var(--ink)">进阶：题量与配比</summary>
        <div class="vl-adv-row" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:8px;font-size:13px;color:var(--ink)">
          <label>题量 <input id="vl-total" type="number" min="10" max="200" value="50" style="width:64px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg-soft);font-size:13px"></label>
          <label>词形 <input id="vl-r-form" type="number" min="0" value="6" style="width:52px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg-soft);font-size:13px"></label>
          <label>拼写 <input id="vl-r-spell" type="number" min="0" value="8" style="width:52px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg-soft);font-size:13px"></label>
          <label>例句 <input id="vl-r-ctx" type="number" min="0" value="12" style="width:52px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg-soft);font-size:13px"></label>
          <label>中译英 <input id="vl-r-zh2en" type="number" min="0" value="12" style="width:52px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg-soft);font-size:13px"></label>
          <label>英译中 <input id="vl-r-en2zh" type="number" min="0" value="12" style="width:52px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg-soft);font-size:13px"></label>
          <span class="muted">全部留空则不传配比，按卷型预设默认出题。</span>
        </div>
      </details>
      <div id="vl-paper-out"></div>
      <div id="vl-paper-history"></div>
    </section>

    <!-- 2. 考试打分（按存档试卷登记分数与标记错词） -->
    <div id="ve-grading-host" style="margin-bottom:18px"><p class="muted">加载待打分试卷…</p></div>

    <!-- 3. 历史考试成绩记录 -->
    <section class="ve-panel">
      <div class="ve-panel-head">
        <div>
          <h2>📈 历史成绩记录</h2>
          <p>按存档试卷登记或修改成绩。卷号太长时这里只显示短名，悬停能看到全名。</p>
        </div>
        <label class="ve-field ve-filter">查看
          <select id="ve-filter">
            <option value="">全部学生</option>
            ${studentOpts}
          </select>
        </label>
      </div>
      <p class="ve-state" id="ve-state"></p>
      <div class="ve-form">
        <label class="ve-field paper">试卷
          <select id="pv-paper">
            <option value="">选择已存档的卷</option>
            ${papers.map(paperOptHtml).join('')}
          </select>
        </label>
        <label class="ve-field">学生
          <select id="pv-user">${studentOpts || '<option value="">（无学生）</option>'}</select>
        </label>
        <label class="ve-field">级别
          <select id="pv-level">${levelOpts}</select>
        </label>
        <label class="ve-field">日期
          <input id="pv-date" type="date" value="${today}"/>
        </label>
        <label class="ve-field score">得分
          <input id="pv-score" type="number" min="0" max="100" placeholder="0–100"/>
        </label>
        <label class="ve-field note">备注
          <input id="pv-note" type="text" maxlength="200" placeholder="选填"/>
        </label>
        <div class="ve-actions">
          <button class="btn-primary" id="pv-save" type="button">登记成绩</button>
          <button class="chip" id="pv-cancel" type="button" hidden>取消修改</button>
        </div>
      </div>
      <div id="ve-list"></div>
    </section>`

  const listEl = body.querySelector('#ve-list')
  const gradingHost = body.querySelector('#ve-grading-host')
  const scopeUser = body.querySelector('#vl-scope-user')
  const presetSel = body.querySelector('#vl-preset')
  let gradingSeq = 0

  async function remountGrading() {
    const mine = ++gradingSeq
    const stage = document.createElement('div')
    await mountExamGrading(stage, {
      students: studentNames,
      student: filter,
      refresh: () => reload(),
    })
    if (mine !== gradingSeq || !gradingHost.isConnected) return
    gradingHost.replaceChildren(...stage.childNodes)
  }

  async function refreshPapersSelect() {
    try {
      const hist = await api.vocabPaperHistory(-1, { limit: 200 })
      papers = hist.papers || []
      const paperSel = body.querySelector('#pv-paper')
      if (!paperSel) return
      const cur = paperSel.value
      paperSel.innerHTML = `<option value="">选择已存档的卷</option>${papers.map(paperOptHtml).join('')}`
      if (cur && [...paperSel.options].some((o) => o.value === cur)) {
        paperSel.value = cur
      }
    } catch { /* 下拉刷新失败保留旧选项 */ }
  }

  // 五题型配比：全空 → 不传 ratios（按后端预设默认）；有填 → 只传填了的键
  function readRatios() {
    const ratios = {}
    ;['form', 'spell', 'ctx', 'zh2en', 'en2zh'].forEach((k) => {
      const v = body.querySelector(`#vl-r-${k}`).value.trim()
      if (v !== '') ratios[k] = Number(v)
    })
    return Object.keys(ratios).length ? ratios : null
  }

  // 预览行：和打印卷同一套问法（词面 + 要求 + 横线）。答案淡显示，方便教师核对。
  const PV_CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
  function pvLine(type, it) {
    const pos = it.pos_cn || (Array.isArray(it.pos) ? it.pos.join(' ') : '') || ''
    const gloss = it.gloss || ''
    const word = it.word || ''
    const blank = '<span class="vl-pv-blank"></span>'
    const ans = it.answer ? `<i class="vl-pv-ans">${escapeHtml(it.answer)}</i>` : ''
    if (type === 'form') {
      const key = it.form_key || it.form || ''
      const label = FORM_CN[key] || key || '词形'
      return `<span class="vl-pv-en">${escapeHtml(word)}</span><span class="vl-pv-ask">的${escapeHtml(label)}</span>${blank}${ans}`
    }
    if (type === 'spell')
      return `<span class="vl-pv-pos">${escapeHtml(pos)}</span><span class="vl-pv-zh">${escapeHtml(String(gloss).slice(0, 18))}</span><code>${escapeHtml(it.blanked || it.answer || '')}</code>`
    if (type === 'ctx') {
      const sent = it.sentence || it.ctx || it.prompt || ''
      return `<span class="vl-pv-en">${escapeHtml(String(sent))}</span>${ans}`
    }
    if (type === 'zh2en')
      return `<span class="vl-pv-pos">${escapeHtml(pos)}</span><span class="vl-pv-zh">${escapeHtml(gloss)}</span>${blank}`
    if (type === 'en2zh')
      return `<span class="vl-pv-en">${escapeHtml(word)}</span>${blank}`
    return escapeHtml(it.prompt || it.question || word || gloss || '（未知题型）')
  }

  async function genPaper() {
    const out = body.querySelector('#vl-paper-out')
    const scope = 'learned'
    const preset = presetSel.value
    const user = (scopeUser.value || '').trim()
    if (!user) {
      out.innerHTML = '<p style="color:#b42318">请先选择学生（试卷按所选学生的全部已学单词出题）。</p>'
      return
    }
    const totalRaw = body.querySelector('#vl-total').value.trim()
    const ratios = readRatios()
    out.innerHTML = '<p class="muted">生成中…</p>'
    try {
      // 先不存档；题量/配比留默认（空）时不传，按后端预设走
      const r = await api.vocabPaperGenerate(0, false, {
        scope, user, preset,
        ...(totalRaw !== '' ? { total: Number(totalRaw) } : {}),
        ...(ratios ? { ratios } : {}),
      })
      const p = r.paper
      const sections = p.sections || []
      let saved = false
      out.innerHTML = `
        <div class="vl-paper-card">
          <div class="vl-paper-meta">
            <b title="${escapeHtml(p.paper_id)}">${escapeHtml(vocabPaperShort(p).short)}</b>
            <span>${p.user ? `${escapeHtml(p.user)} · ` : ''}已学全部混合卷 · ${sections.map((s) => `${escapeHtml(s.title)} ${(s.items || []).length}`).join(' · ')} · 与历史重复 ${p.overlap_with_history} 词${p.seed != null && p.seed !== '' ? ` · seed ${escapeHtml(String(p.seed))}` : ''}</span>
            <i class="vl-paper-state">${saved ? '已存档' : '未存档（预览态）'}</i>
          </div>
          <details class="vl-paper-preview">
            <summary>预览试卷内容</summary>
            ${sections.map((s, si) => `
              <div class="vl-pv-sec"><b>${PV_CN[si] || (si + 1)}、${escapeHtml(s.title)}<span>${(s.items || []).length} 题</span></b>${(s.items || []).slice(0, 5).map((it, i) => `<p><em>${i + 1}.</em> ${pvLine(s.type, it)}</p>`).join('')}${(s.items || []).length > 5 ? `<p class="muted">…共 ${(s.items || []).length} 题</p>` : ''}</div>`).join('') || '<p class="muted">（本卷没有返回任何 section）</p>'}
          </details>
          <div class="vl-paper-ops">
            <button class="chip" id="vl-preview" type="button">👁 预览试卷</button>
            <button class="btn-primary" id="vl-save" type="button">💾 保存存档</button>
            <button class="chip" id="vl-print" type="button">🖨 打印</button>
          </div>
        </div>`
      const stateEl = out.querySelector('.vl-paper-state')
      out.querySelector('#vl-preview').addEventListener('click', () => {
        const w = window.open('', '_blank')
        w.document.write(r.html)
        w.document.close()
      })
      out.querySelector('#vl-save').addEventListener('click', async () => {
        try {
          await api.vocabPaperSave(p)
          saved = true
          stateEl.textContent = '已存档 ✓'
          out.querySelector('#vl-save').disabled = true
          out.querySelector('#vl-save').textContent = '已保存'
          toast(`✅ 试卷 ${p.paper_id} 已存档`)
          await Promise.all([loadHistory(), refreshPapersSelect(), remountGrading()])
        } catch (e) { alert('保存失败：' + e.message) }
      })
      out.querySelector('#vl-print').addEventListener('click', () => {
        const w = window.open('', '_blank')
        w.document.write(r.html)
        w.document.close()
        setTimeout(() => w.print(), 400)
      })
    } catch (e) {
      out.innerHTML = `<p style="color:#b42318">生成失败：${escapeHtml(e.message)}</p>`
    }
  }
  body.querySelector('#vl-gen').addEventListener('click', () => genPaper())

  async function loadHistory() {
    const host = body.querySelector('#vl-paper-history')
    try {
      const r = await api.vocabPaperHistory(-1, { limit: 80 })
      const items = r.papers || []
      const shown = items.slice(0, 16)
      host.innerHTML = items.length ? `
        <div class="vl-hist">
          <b>📚 已生成 ${items.length} 卷</b>
          ${shown.map((x) => {
            const who = x.user ? ` · ${escapeHtml(x.user)}` : ''
            return `
            <span class="vl-hist-item" title="${escapeHtml(x.paper_id)}">
              <em class="vl-hist-name">${escapeHtml(vocabPaperShort(x).short)}${who} · ${x.count} 词</em>
              <button class="chip vl-hist-op" data-hprev="${escapeHtml(x.paper_id)}" type="button">👁</button>
              <button class="chip vl-hist-op del" data-hdel="${escapeHtml(x.paper_id)}" type="button">🗑</button>
            </span>`
          }).join('')}
        </div>` : '<p class="muted">尚无生成记录。</p>'
      host.querySelectorAll('[data-hprev]').forEach((b) =>
        b.addEventListener('click', async () => {
          try {
            const r = await api.vocabPaperPreview(b.dataset.hprev)
            const w = window.open('', '_blank')
            w.document.write(r.html)
            w.document.close()
          } catch (e) { alert('预览失败：' + e.message) }
        }))
      host.querySelectorAll('[data-hdel]').forEach((b) =>
        b.addEventListener('click', async () => {
          if (!confirm(`删除试卷 ${b.dataset.hdel}？`)) return
          try {
            await api.vocabPaperDelete(b.dataset.hdel)
            toast('已删除')
            await Promise.all([loadHistory(), refreshPapersSelect(), remountGrading()])
          } catch (e) { alert(e.message) }
        }))
    } catch { host.innerHTML = '' }
  }

  function resetForm() {
    editing = null
    body.querySelector('#pv-save').textContent = '登记成绩'
    body.querySelector('#pv-cancel').hidden = true
    body.querySelector('#pv-score').value = ''
    body.querySelector('#pv-note').value = ''
    body.querySelector('#pv-date').value = today
    const paperSel = body.querySelector('#pv-paper')
    if (paperSel) paperSel.value = ''
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
    listEl.innerHTML = `<div class="ve-list">${items.map((x) => {
      const wn = wrongCountOf(x)
      const lab = x.paper_id ? vocabPaperShort(x.paper_id, x.level) : null
      return `
      <article class="ve-card ${x.score >= passScore ? 'ok' : 'fail'}" data-exam="${x.id}">
        <div class="ve-card-top">
          <b>${escapeHtml(x.user)}</b>
          <span class="ve-level">L${x.level}</span>
          <b class="ve-score">${x.score}<small>分</small></b>
        </div>
        <div class="ve-meta">
          <span>${escapeHtml(x.exam_date || '')}</span>
          ${lab ? `<span class="ve-paper" title="${escAttr(lab.full)}">${escapeHtml(lab.short)}</span>` : ''}
          ${x.note ? `<span class="ve-note">${escapeHtml(x.note)}</span>` : ''}
        </div>
        <div class="ve-ops">
          <button type="button" class="chip" data-ww="${x.id}" title="查看这场考试的错词明细">${wn ? `错 ${wn} · 明细` : '明细'}</button>
          <button type="button" class="chip" data-edit="${x.id}">修改</button>
          <button type="button" class="chip" data-del="${x.id}">删除</button>
        </div>
      </article>`
    }).join('')}</div>`
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
    const ps = Number(vex && vex.pass_score)
    if (Number.isFinite(ps) && ps > 0) passScore = Math.round(ps)
    // 及格线展示在 view-head（el）里，不在 #ve-body 内
    const passEl = el.querySelector('#ve-pass')
    if (passEl) passEl.textContent = String(passScore)
    paintList()
  }

  function readForm() {
    const user = body.querySelector('#pv-user').value
    const level = Number(body.querySelector('#pv-level').value)
    const score = Number(body.querySelector('#pv-score').value)
    const exam_date = body.querySelector('#pv-date').value
    const note = body.querySelector('#pv-note').value.trim()
    let paper_id = body.querySelector('#pv-paper').value
    if (editing) paper_id = paper_id || editing.paper_id || ''
    if (!editing && !paper_id) {
      alert('先选择已存档的试卷。还没存档的卷不能登记。')
      return null
    }
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
    return { user, level, score, exam_date, note, paper_id }
  }

  body.querySelector('#pv-paper').addEventListener('change', (e) => {
    const opt = e.target.selectedOptions && e.target.selectedOptions[0]
    const lv = Number(opt && opt.dataset.lv)
    if (Number.isFinite(lv) && lv >= 0) body.querySelector('#pv-level').value = String(lv)
  })

  body.querySelector('#ve-filter').addEventListener('change', async (e) => {
    filter = e.target.value
    if (filter && scopeUser && [...scopeUser.options].some((o) => o.value === filter)) {
      scopeUser.value = filter
    }
    remountGrading()
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
        // 本页表单不改错词，所以不传 wrong_words。
        // 后端：字段缺省 / null = 明细和复习队列都不动；[] = 清空明细。
        await api.vocabExamUpdate(editing.id, rec)
        toast('已保存修改')
      } else {
        await api.vocabExamAdd(rec)
        toast('已登记')
      }
      resetForm()
      await reload()
      remountGrading()
    } catch (e) {
      alert((editing ? '修改失败：' : '登记失败：') + e.message)
    } finally {
      btn.disabled = false
    }
  })
  listEl.addEventListener('click', async (e) => {
    const wwBtn = e.target.closest('[data-ww]')
    if (wwBtn) {
      openExamWrong(wwBtn.dataset.ww)
      return
    }
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
      const paperSel = body.querySelector('#pv-paper')
      if (exam.paper_id && ![...paperSel.options].some((o) => o.value === exam.paper_id)) {
        const opt = document.createElement('option')
        const lab = vocabPaperShort(exam.paper_id, exam.level)
        opt.value = exam.paper_id
        opt.textContent = lab.short
        opt.title = lab.full
        paperSel.append(opt)
      }
      paperSel.value = exam.paper_id || ''
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
    if (delBtn) {
      if (!confirm('删除这条考试记录？')) return
      try {
        await api.vocabExamDel(delBtn.dataset.del)
        if (editing && String(editing.id) === delBtn.dataset.del) resetForm()
        toast('已删除')
        await reload()
        remountGrading()
      } catch (err) {
        alert(err.message)
      }
      return
    }
    // 点在行内非按钮区域 → 打开错词明细（等于整行可点）
    if (e.target.closest('button')) return
    const row = e.target.closest('.ve-card')
    if (row && row.dataset.exam) openExamWrong(row.dataset.exam)
  })

  await Promise.all([loadHistory(), remountGrading(), reload()])
}

// ---- 错词明细（只读查看；错词标记在上方「考试打分」卡完成） ----

// 错词数：优先 wrong_words 数组，退回 wrong_count（后端两版字段兼容）
function wrongCountOf(e) {
  if (!e) return 0
  if (Array.isArray(e.wrong_words)) return e.wrong_words.length
  const n = Number(e.wrong_count)
  return Number.isFinite(n) && n > 0 ? Math.round(n) : 0
}

// 卷面题型 → 中文（与生成卷五段一致）。空 qtype 留空，不编造标签；未知值原样展示。
const VE_QTYPES = {
  form: '词形变化', spell: '拼写', ctx: '例句填空',
  zh2en: '中译英', en2zh: '英译中',
}
const veQtypeLabel = (q) => (q ? (VE_QTYPES[q] || String(q)) : '')

// 明细里的错词词表。exam.wrong_words 是服务端从 answers 投影的词表；
// 没有投影时从 answers 的答错行取。
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

function closeExamWrongOverlay() {
  const overlay = document.querySelector('.gd-focus-detail.ve-wwin')
  if (!overlay) return
  if (overlay._onKey) document.removeEventListener('keydown', overlay._onKey)
  overlay.remove()
}

// 某场考试的错词列表（GET /vocab-exams/{id} → {exam, answers}）。
// 复用 gd-focus-detail 弹窗骨架与 gd-focus-chip 胶囊，不另起样式。
async function openExamWrong(examId) {
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
  closeExamWrongOverlay() // 防重复叠加
  const overlay = document.createElement('div')
  overlay.className = 'gd-focus-detail ve-wwin'
  overlay.innerHTML = `
    <div class="gd-focus-detail-mask" data-close></div>
    <div class="gd-focus-detail-card">
      <div class="gd-focus-detail-head">
        <div class="gd-focus-detail-title">
          <b>错词明细</b>
          <span title="${exam.paper_id ? escAttr(exam.paper_id) : ''}">${escapeHtml(exam.user || '')} · L${escapeHtml(exam.level ?? '-')} · ${escapeHtml(exam.score ?? '-')} 分 · ${escapeHtml(exam.exam_date || '')}${exam.paper_id ? ` · ${escapeHtml(vocabPaperShort(exam.paper_id, exam.level).short)}` : ''}</span>
        </div>
        <button class="reading-btn small" data-close>关闭</button>
      </div>
      <div class="gd-focus-detail-body">
        ${wrong.length
          ? `<p class="reading-hint" style="margin:0 0 8px">共 ${wrong.length} 个错词，已自动加入背词复习队列。</p>
             <div class="gd-focus-chips">${wrong.map((w) => {
               const q = qByWord.get(w)
               return `<i class="gd-focus-chip warn">${escapeHtml(w)}${q ? ` · ${escapeHtml(veQtypeLabel(q))}` : ''}</i>`
             }).join('')}</div>`
          : '<p class="reading-hint">这场考试登记为全对，没有错词。</p>'}
      </div>
    </div>`
  document.body.append(overlay)
  const onKey = (e) => { if (e.key === 'Escape') closeExamWrongOverlay() }
  document.addEventListener('keydown', onKey)
  overlay._onKey = onKey
  overlay.querySelectorAll('[data-close]').forEach((x) =>
    x.addEventListener('click', closeExamWrongOverlay))
}

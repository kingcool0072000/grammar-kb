// FCE 真题 · 学生做题流：提交历史 → 大题列表（Part 级过滤）→ 做题渲染 → 提交
// （含听力频谱播放台、字号偏好）。入口骨架（卷列表/周计划过滤）见 ../fcePapers.js。
import { api, getAuth } from '../../api.js'
import { escapeHtml, escAttr } from '../../render.js'
import { fmtDurPad as fmtDur } from '../../fmt.js'
import { createSpectrumPlayer, getFceAudioPref } from '../fceAudio.js'
import { mountFcePapers } from '../fcePapers.js'

const TYPE_CN = {
  mcq4: '四选一', mcq3: '三选一', cloze: '完形填空', wordFormation: '词形变换',
  transform: '关键词改写', matchSentence: '句子还原', matchPerson: '人物匹配',
  matchOpinion: '观点匹配', gapFill: '句子填空', essay: '作文', essayOption: '作文（选做）',
}
const PAPER_CN = {
  'Reading and Use of English': '读写', Writing: '写作', Listening: '听力', Speaking: '口语',
}

export function doneCount(history, testId) {
  return history.filter((s) => s.test_id === testId).length
}

export function subRow(s, role) {
  const when = (s.created_at || '').slice(0, 10)
  const dur = s.duration_sec ? ` · ⏱${fmtDur(s.duration_sec)}` : ''
  const score = s.status === 'auto'
    ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b><span class="fce-his-date">${dur}</span>`
    : s.status === 'graded'
      ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b><span class="fce-his-date">${dur}</span>`
      : '<b class="fce-his-score pend">待批改</b>'
  const isAdmin = role === 'teacher'
  return `
    <div class="fce-his-row fce-sub-row" data-sub="${s.id}" data-user="${escAttr(s.user || '')}">
      <span class="fce-his-what">${isAdmin && s.user ? `${escapeHtml(s.user)} · ` : ''}T${s.test_id} ${PAPER_CN[s.paper] || s.paper} P${s.part}</span>
      ${score}
      <span class="fce-his-date">${when}</span>
      <button class="reading-btn small" data-sub-view="${s.id}" title="查看这次练习的逐题明细">📋 明细</button>
      ${isAdmin ? `<button class="reading-btn small danger" data-sub-del="${s.id}" title="删除这条练习记录">删除</button>` : ''}
    </div>`
}

// 教师版：练习记录行 明细/删除
export function bindSubRowActions(el, history) {
  el.querySelectorAll('[data-sub-del]').forEach((b) => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation()
      const id = Number(b.dataset.subDel)
      const s = history.find((x) => x.id === id)
      if (!confirm(`确定删除这条练习记录（${s ? `T${s.test_id} P${s.part}` : id}）？删除后不可恢复。`)) return
      try {
        await api.fceDeleteSubmission(id)
        const row = b.closest('.fce-sub-row')
        if (row) row.remove()
      } catch (err) {
        alert(`删除失败：${err.message}`)
      }
    })
  })
  el.querySelectorAll('[data-sub-view]').forEach((b) => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation()
      await showSubmissionDetail(Number(b.dataset.subView))
    })
  })
}

// 逐题明细弹窗：拉取提交详情（含每题作答/正确答案/对错）
async function showSubmissionDetail(subId) {
  let s
  try {
    s = await api.fceSubmission(subId)
  } catch (err) {
    alert(`加载明细失败：${err.message}`)
    return
  }
  let mask = document.querySelector('.fce-sub-detail')
  if (mask) mask.remove()
  mask = document.createElement('div')
  mask.className = 'fce-sub-detail'
  const rows = (s.detail || []).map((d) => {
    const cls = d.correct ? 'ok' : 'bad'
    const expected = d.correct ? '' : `<span class="fce-detail-expected">答案 ${escapeHtml(String(d.expected ?? '-'))}</span>`
    return `
      <div class="fce-detail-row ${cls}">
        <span class="fce-detail-q">Q${d.qnum}</span>
        <span class="fce-detail-given">${escapeHtml(String(d.given || '（未作答）'))}</span>
        ${expected}
        <b class="fce-detail-mark ${cls}">${d.correct ? '✓' : '✗'}</b>
      </div>`
  }).join('')
  const essayInfo = s.status !== 'auto' ? `
    <div class="fce-detail-essay">
      <div>状态：${s.status === 'graded' ? `已批改 · 老师 ${s.teacher_score ?? '-'} 分` : '待批改'}</div>
      ${s.teacher_comment ? `<div class="fce-detail-comment">💬 ${escapeHtml(s.teacher_comment)}</div>` : ''}
    </div>` : ''
  mask.innerHTML = `
    <div class="reading-hist-pop-mask" data-close></div>
    <div class="reading-hist-pop-body">
      <div class="reading-hist-pop-head">
        <b>${escapeHtml(s.user || '')} · T${s.test_id} ${PAPER_CN[s.paper] || s.paper} P${s.part}
          · ${s.status === 'auto' ? `${s.auto_score}/${s.total}` : s.teacher_score ?? '-'}</b>
        <button class="reading-btn small" data-close>关闭</button>
      </div>
      <p class="reading-hint">${(s.created_at || '').slice(0, 16).replace('T', ' ')}${s.duration_sec ? ` · 用时 ${fmtDur(s.duration_sec)}` : ''}</p>
      ${essayInfo}
      ${rows || '<p class="reading-hint">（作文题无逐题明细）</p>'}
    </div>`
  document.body.append(mask)
  mask.querySelectorAll('[data-close]').forEach((x) => x.addEventListener('click', () => mask.remove()))
}

// ---------- 大题列表（每次练一个大题） ----------
// 学生周计划开放的 Test/Part（mountFcePapers 设置；练习页「返回大题列表」
// renderPartListById 等跳转路径都要用——之前返回时传空数组导致过滤失效，
// 全部非授权大题重现）。
let allowedPartsCache = null
// 拆分前由 mountFcePapers 直接对本模块变量赋值，拆分后壳经此入口写入
export function setAllowedPartsCache(allowedParts) {
  allowedPartsCache = allowedParts
}

export async function renderPartList(el, testId, role, history, allowedParts = null) {
  el.innerHTML = '<div class="view-head"><h1>FCE 真题</h1><p>加载中…</p></div>'
  let data
  try {
    data = await api.fcePaper(testId)
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  // 练习单位：每个 Part（口语跳过）。学生视角后端已剥离答案；
  // 学生额外只看周计划开放的大题（allowedParts），其余 Part 隐藏。
  const openSet = role !== 'teacher' && allowedParts ? allowedParts.get(testId) : null
  const parts = data.sections.filter((s) =>
    s.paper !== 'Speaking' && s.questions.length
    && (!openSet || openSet.has(`${s.paper}|${s.part}`)))
  const hisFor = (paper, part) =>
    history.filter((s) => s.test_id === testId && s.paper === paper && s.part === part)
  const best = (paper, part) => {
    const hs = hisFor(paper, part).filter((s) => s.status === 'auto')
    if (!hs.length) return null
    return hs.reduce((a, b) => (b.auto_score / (b.total || 1) > a.auto_score / (a.total || 1) ? b : a))
  }

  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="fce-back">← 返回试卷列表</button>
      <h1>Test ${testId} · 选一个大题练习</h1>
      <p>${openSet
        ? '老师本周开放的大题在这里（其余大题未开放）。'
        : '每个大题独立练习：读写 7 个 Part、写作 2 个 Part、听力 4 个 Part。'}</p>
    </div>
    <div class="fce-part-list">
      ${parts.map((s) => {
        const b = best(s.paper, s.part)
        const n = hisFor(s.paper, s.part).length
        return `
        <button class="fce-part-card" data-idx="${parts.indexOf(s)}">
          <span class="fce-badge fce-badge-lesson">${PAPER_CN[s.paper] || s.paper}</span>
          <span class="fce-part-name">Part ${s.part}</span>
          <span class="fce-part-sub">${s.questions.length} 题${s.passage && s.passage.trim() ? ' · 含阅读' : ''}</span>
          ${b ? `<span class="fce-part-best">最好 ${b.auto_score}/${b.total}${n > 1 ? ` · 练 ${n} 次` : ''}</span>` : ''}
        </button>`
      }).join('')}
    </div>
  `

  el.querySelector('#fce-back').addEventListener('click', () => mountFcePapers(el, { role }))
  el.querySelectorAll('.fce-part-card').forEach((card) => {
    card.addEventListener('click', () => renderPractice(el, testId, parts[Number(card.dataset.idx)], role))
  })
}

// ---------- 单个大题练习 ----------
// 字号档位（px）：影响阅读原文 + 题干选项；存 localStorage
const FONT_KEY = 'gkb-fce-font-size'
const FONT_STEPS = [13, 14, 15, 16, 18, 20, 22]
const FONT_DEFAULT_IDX = 2 // 15px

function getFontIdx() {
  const v = Number(localStorage.getItem(FONT_KEY))
  return Number.isInteger(v) && v >= 0 && v < FONT_STEPS.length ? v : FONT_DEFAULT_IDX
}

function myRoleNow() {
  const a = getAuth()
  return a ? a.role : 'teacher'
}

async function renderPractice(el, testId, sec, role) {
  el.innerHTML = '<div class="view-head"><h1>FCE 真题</h1><p>加载中…</p></div>'
  let data
  try {
    data = await api.fcePaper(testId)
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  const section = data.sections.find(
    (s) => s.paper === sec.paper && s.part === sec.part
  )
  // 学生练习模式：电子书护眼背景 + 防误触（禁右键/选择/拖拽）；教师阅卷不受限。
  // class/监听挂在容器 el 上，并登记到 _cleanups：路由切换由 main.js 统一撤销，
  // 页内返回/重进也先撤销——否则护眼底色、禁选择、计时器会泄漏到其它页面
  const isStudent = myRoleNow() !== 'teacher'
  const noMenu = (e) => e.preventDefault()
  const noSelect = (e) => {
    // 输入框/文本域里正常选择作答，其余区域禁止
    if (!/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) e.preventDefault()
  }
  const noDragStart = (e) => e.preventDefault()
  if (isStudent) {
    el.classList.add('fce-reading-mode')
    el.addEventListener('contextmenu', noMenu)
    el.addEventListener('selectstart', noSelect)
    el.addEventListener('dragstart', noDragStart)
  }
  let timer = null
  // 一键收干净本练习页的全部模式态（幂等，可重复调用）
  const stopPracticeMode = () => {
    if (isStudent) {
      el.removeEventListener('contextmenu', noMenu)
      el.removeEventListener('selectstart', noSelect)
      el.removeEventListener('dragstart', noDragStart)
      el.classList.remove('fce-reading-mode')
    }
    if (timer) { clearInterval(timer); timer = null }
  }
  // 同一容器重进练习（「再练一次」不经过大题列表）先撤销上一轮，防监听/interval 叠加
  const cleanups = (el._cleanups ||= [])
  cleanups.splice(0).forEach((fn) => fn())
  cleanups.push(stopPracticeMode)
  const qs = section.questions
  const isEssay = qs.every((q) => q.type.startsWith('essay'))
  const isChoice = qs.every((q) => ['mcq3', 'mcq4', 'matchSentence', 'matchPerson', 'matchOpinion'].includes(q.type))
  const hasPassage = section.passage && section.passage.trim()

  // 听力部分：频谱播放器（音频文件到位即生效，.m4a/.mp3 均可）。
  // 学生默认隐藏（考试模式；#/config 可开），教师始终显示。
  const audioKey = `listening_p${section.part}`
  let audioHtml = ''
  let audioReady = false
  if (section.paper === 'Listening') {
    let avail = []
    try {
      avail = (await api.fceAudioStatus(testId)).available || []
    } catch { avail = [] }
    audioReady = avail.includes(audioKey)
    const show = audioReady && (myRoleNow() === 'teacher' || getFceAudioPref())
    audioHtml = show
      ? `<div class="fce-audio"><span class="fce-audio-label">🎧 听力音频</span><div id="fce-audio-host"></div></div>`
      : ''
  }

  // 听力预习词表：做题前先过一遍未掌握词（默认展开、可收起；点词看释义；
  // 教师也显示）。只显示当前 Part 的词。
  let vocabHtml = ''
  let vocabWords = []
  if (section.paper === 'Listening') {
    try {
      const parts = await api.fceListeningVocab(testId)
      const pv = (parts || []).find((x) => x.part === section.part)
      if (pv && pv.words.length) {
        vocabWords = pv.words
        vocabHtml = `
        <div class="fce-vocab" id="fce-vocab">
          <div class="fce-vocab-head">
            <b>📖 听力预习词（${pv.words.length} 词 · 本部分原文里你还没背到的）</b>
            <button type="button" class="reading-btn small" id="fce-vocab-toggle">收起</button>
          </div>
          <div class="fce-vocab-body">
            <p class="fce-vocab-note">先扫一眼这些词再听——L2 以上是重点（加粗），点词看释义；听的时候别急着听到哪个词选哪个。</p>
            <div class="fce-vocab-words" id="fce-vocab-words">
              ${pv.words.map((w) => `<button type="button" class="fce-vocab-word ${w.level != null && w.level >= 2 ? 'hard' : ''}" data-vw="${escapeHtml(w.word)}">${escapeHtml(w.word)}${w.level != null ? `<i>L${w.level}</i>` : '<i>?</i>'}${w.freq > 1 ? `<em>×${w.freq}</em>` : ''}</button>`).join('')}
            </div>
            <div class="fce-vocab-detail" id="fce-vocab-detail" hidden></div>
          </div>
        </div>`
      }
    } catch { /* 词表失败不阻塞做题 */ }
  }

  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="fce-back">← 返回大题列表</button>
      <h1>Test ${testId} ${PAPER_CN[section.paper] || section.paper} · Part ${section.part}</h1>
      <p>${qs.length} 题 · ${qs.map((q) => TYPE_CN[q.type] || q.type).filter((v, i, a) => a.indexOf(v) === i).join(' / ')}</p>
    </div>
    <div class="fce-toolbar">
      <div class="fce-timer" id="fce-timer" title="练习用时">⏱ <b id="fce-timer-num">00:00</b></div>
      <div class="fce-prog-inline" title="作答进度">
        <span id="fce-prog-text">0/${qs.length} 已作答</span>
        <div class="fce-prog-track"><i id="fce-prog-fill"></i></div>
      </div>
      <div class="fce-font-ctl" title="调节字号">
        <button class="fce-font-btn" id="font-dec">A−</button>
        <span class="fce-font-label">字号</span>
        <button class="fce-font-btn" id="font-inc">A+</button>
      </div>
    </div>
    ${audioHtml}
    ${vocabHtml}
    ${section.instruction ? `<div class="fce-instruction">${escapeHtml(section.instruction)}</div>` : ''}
    ${hasPassage ? renderPassage(section.passage) : ''}
    <form id="fce-form" class="fce-form">
      ${qs.map((q) => questionFormHtml(q, isChoice)).join('')}
      <div class="fce-submit-bar">
        <button type="submit" class="fce-submit-btn">提交答案</button>
        <span class="fce-submit-note">${isEssay ? '提交后由老师批改' : '提交后自动批改'}</span>
      </div>
    </form>
    <div id="fce-result"></div>
  `

  // ---- 听力频谱播放器（音频就绪时挂载；离开页面由 cleanups 撤销） ----
  if (audioReady) {
    const host = el.querySelector('#fce-audio-host')
    if (host) {
      const sp = createSpectrumPlayer(host, `/api/fce-papers/${testId}/audio/${audioKey}`, {
        compact: isStudent,
      })
      cleanups.push(sp.destroy)
    }
  }

  // ---- 预习词表折叠 + 点词看释义 ----
  const vocabBox = el.querySelector('#fce-vocab')
  if (vocabBox) {
    vocabBox.querySelector('#fce-vocab-toggle').addEventListener('click', () => {
      const body = vocabBox.querySelector('.fce-vocab-body')
      const hidden = body.hidden
      body.hidden = !hidden
      vocabBox.querySelector('#fce-vocab-toggle').textContent = hidden ? '收起' : '展开'
    })
    const detail = vocabBox.querySelector('#fce-vocab-detail')
    vocabBox.querySelector('#fce-vocab-words').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-vw]')
      if (!btn) return
      const word = btn.dataset.vw
      const show = (lines, note) => {
        detail.hidden = false
        detail.innerHTML = `<b>${escapeHtml(word)}</b>` +
          (lines.length ? `<p>${lines.map((l) => escapeHtml(String(l))).join('<br/>')}</p>` : '') +
          (note ? `<span class="muted">${escapeHtml(note)}</span>` : '')
      }
      // 内置 gloss 优先；没有再查 /dict 兜底
      const w = vocabWords.find((x) => x.word === word)
      if (w && w.gloss) return show([w.gloss])
      show([], '查询释义…')
      try {
        const d = await api.dict(word)
        const gl = (d && d.gloss_lines) || []
        const got = gl.length
          ? gl.map((g) => [g.pos, g.text].filter(Boolean).join('. '))
          : (d && d.gloss ? [d.gloss] : [])
        show(got, got.length ? '' : '词典未收录该词')
      } catch {
        show([], '词典未收录该词')
      }
    })
  }

  // ---- 字号调节 ----
  const root = el.querySelector('#fce-form').parentElement
  const applyFont = (idx) => {
    localStorage.setItem(FONT_KEY, String(idx))
    root.style.setProperty('--fce-fs', `${FONT_STEPS[idx]}px`)
    const dec = el.querySelector('#font-dec')
    const inc = el.querySelector('#font-inc')
    dec.disabled = idx === 0
    inc.disabled = idx === FONT_STEPS.length - 1
  }
  applyFont(getFontIdx())
  el.querySelector('#font-dec').addEventListener('click', () => {
    const i = getFontIdx(); if (i > 0) applyFont(i - 1)
  })
  el.querySelector('#font-inc').addEventListener('click', () => {
    const i = getFontIdx(); if (i < FONT_STEPS.length - 1) applyFont(i + 1)
  })

  // ---- 计时（进入练习页即开始，提交时随答案上报） ----
  const startAt = Date.now()
  const timerEl = el.querySelector('#fce-timer-num')
  timer = setInterval(() => {
    // 保险：容器已脱离文档（路由切走）时自停，避免空转（参考 manage.js 轮询）
    if (!el.isConnected) { clearInterval(timer); timer = null; return }
    const s = Math.floor((Date.now() - startAt) / 1000)
    timerEl.textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }, 1000)
  const elapsed = () => Math.round((Date.now() - startAt) / 1000)

  el.querySelector('#fce-back').addEventListener('click', async () => {
    stopPracticeMode()
    // 返回大题列表：重拉练习记录 + 传周计划开放表（学生只见授权大题——
    // 曾传空数组导致过滤失效、全部大题重现）
    let history = []
    try { history = await api.fceSubmissions() } catch { history = [] }
    renderPartList(el, testId, role, history, allowedPartsCache)
  })
  const $form = el.querySelector('#fce-form')

  // 单选大题：点选项卡片即选中
  $form.addEventListener('click', (e) => {
    const opt = e.target.closest('.fce-choice')
    if (!opt) return
    const item = opt.closest('.fce-pq')
    item.querySelectorAll('.fce-choice').forEach((c) => c.classList.remove('picked'))
    opt.classList.add('picked')
    opt.querySelector('input[type=radio]').checked = true
    item.classList.remove('unanswered')
    updateProgress(el)
  })
  $form.addEventListener('input', () => updateProgress(el))
  updateProgress(el)

  $form.addEventListener('submit', async (e) => {
    e.preventDefault()
    const answers = {}
    $form.querySelectorAll('.fce-pq').forEach((item) => {
      const n = item.dataset.qnum
      const radio = item.querySelector('input[type=radio]:checked')
      const text = item.querySelector('textarea, input[type=text]')
      answers[n] = radio ? radio.value : (text ? text.value.trim() : '')
    })
    const unanswered = Object.values(answers).filter((v) => !v).length
    if (!isEssay && unanswered && !confirm(`还有 ${unanswered} 题未作答，确定提交？`)) return

    const btn = $form.querySelector('.fce-submit-btn')
    btn.disabled = true
    btn.textContent = '批改中…'
    clearInterval(timer)
    const dur = elapsed()
    try {
      const res = await api.fceSubmit({
        test_id: testId, paper: section.paper, part: section.part, answers,
        duration_sec: dur,
      })
      renderResult(el.querySelector('#fce-result'), res, qs, isEssay, $form, dur)
      btn.textContent = '已提交'
    } catch (err) {
      alert(`提交失败：${err.message}`)
      btn.disabled = false
      btn.textContent = '提交答案'
    }
  })
}

// 阅读原文：按 OCR 行合并成段落（连续小写开头行并入上一段），不再裸 pre
function renderPassage(passage) {
  if (!passage || !passage.trim()) return ''
  const rawLines = passage.split('\n').map((l) => l.trim()).filter(Boolean)
  const paras = []
  for (const line of rawLines) {
    const isHeading = /^[A-Z0-9][^.!?]{0,40}$/.test(line) && !line.endsWith('.')
    const cont = paras.length && !isHeading && /^[a-z,"'“(]/.test(line)
        && !/^(Speaker \d|Part \d)/.test(line)
    if (cont) paras[paras.length - 1] += ' ' + line
    else paras.push(line)
  }
  return `
    <section class="fce-passage">
      <div class="fce-passage-title">📄 阅读原文</div>
      <div class="fce-passage-body">${paras.map((p) => `<p>${escapeHtml(p)}</p>`).join('')}</div>
    </section>`
}

function questionFormHtml(q, isChoice) {
  const opts = (q.options || []).filter(([, v]) => v)
  let inner = ''
  if (isChoice && opts.length) {
    inner = `<div class="fce-choices">${opts.map(([k, v]) => `
      <label class="fce-choice">
        <input type="radio" name="q${q.qnum}" value="${k}">
        <span class="fce-choice-key">${k}</span>
        <span class="fce-choice-text">${escapeHtml(v)}</span>
      </label>`).join('')}</div>`
  } else if (q.type === 'essay' || q.type === 'essayOption') {
    inner = `<textarea name="q${q.qnum}" rows="10" placeholder="Write your answer in 140-190 words…"></textarea>`
  } else if (q.keyword) {
    // 关键词改写：题面显示关键词（提示必须使用）
    inner = `<input type="text" name="q${q.qnum}" placeholder="用 ${q.keyword} 补全第二句（2-5 词）" autocomplete="off">`
  } else {
    inner = `<input type="text" name="q${q.qnum}" placeholder="输入答案" autocomplete="off">`
  }
  return `
    <div class="fce-pq unanswered" data-qnum="${q.qnum}">
      <div class="fce-pq-head">
        <span class="fce-pq-n">${q.qnum}.</span>
        <div class="fce-pq-body">
          ${q.stem ? `<p class="fce-pq-stem">${escapeHtml(q.stem)}</p>` : ''}
          ${q.stem2 ? `<p class="fce-pq-fill">${escapeHtml(q.stem2)}</p>` : ''}
          ${q.keyword && q.type === 'transform' ? `<p class="fce-q-keyword">🔑 ${escapeHtml(q.keyword)}</p>` : ''}
        </div>
        <span class="fce-type-chip">${TYPE_CN[q.type] || q.type}</span>
      </div>
      ${inner}
    </div>`
}

function updateProgress(el) {
  const items = el.querySelectorAll('.fce-pq')
  const done = [...items].filter((i) => {
    if (i.querySelector('input[type=radio]:checked')) return true
    const t = i.querySelector('textarea, input[type=text]')
    return t && t.value.trim()
  }).length
  const bar = el.querySelector('#fce-prog-fill')
  const text = el.querySelector('#fce-prog-text')
  if (text) text.textContent = `${done}/${items.length} 已作答`
  if (bar) bar.style.width = `${items.length ? Math.round((done / items.length) * 100) : 0}%`
}

function renderResult(box, res, qs, isEssay, $form, dur) {
  $form.querySelectorAll('input, textarea').forEach((i) => (i.disabled = true))
  const durLine = dur != null ? `<p>⏱ 用时 ${fmtDur(dur)}</p>` : ''
  if (isEssay || res.status === 'pending') {
    box.innerHTML = `
      <div class="fce-result-box pend">
        <h3>✅ 作文已提交</h3>
        <p>等待老师批改，批改后会出现在练习记录里。</p>
        ${durLine}
      </div>`
    box.scrollIntoView({ behavior: 'smooth' })
    return
  }
  // 客观题：逐题标对错 + 显示正确答案
  const detailByNum = new Map(res.detail.map((d) => [d.qnum, d]))
  qs.forEach((q) => {
    const d = detailByNum.get(q.qnum)
    const item = $form.querySelector(`.fce-pq[data-qnum="${q.qnum}"]`)
    if (!item || !d) return
    item.classList.add(d.correct ? 'right' : 'wrong')
    const mark = document.createElement('div')
    mark.className = 'fce-mark'
    mark.innerHTML = d.correct
      ? '<span class="ok">✔ 正确</span>'
      : `<span class="bad">✘ 正确答案：${escapeHtml(d.expected)}</span>`
    item.querySelector('.fce-pq-body').after(mark)
  })
  const pct = res.total ? Math.round((res.auto_score / res.total) * 100) : 0
  box.innerHTML = `
    <div class="fce-result-box ${pct >= 80 ? 'good' : pct >= 60 ? 'mid' : 'low'}">
      <h3>得分：${res.auto_score} / ${res.total}（${pct}%）</h3>
      <p>${pct >= 80 ? '很棒！保持这个状态。' : pct >= 60 ? '不错，错题再看看解析。' : '错题较多，建议对照原文再练一次。'}</p>
      ${durLine}
      <button class="fce-again-btn" id="fce-again">再练一次</button>
    </div>`
  box.scrollIntoView({ behavior: 'smooth' })
  box.querySelector('#fce-again').addEventListener('click', () => {
    const testId = res.test_id
    // 重新拉取当前大题（复用上游闭包参数不便，直接回到大题列表让用户重点进）
    renderPartListById(el, testId, res.paper, res.part)
  })
}

// 结果区「再练一次」：回到练习页（重新拉数据，学生视角无答案）
async function renderPartListById(el, testId, paper, part) {
  let data
  try {
    data = await api.fcePaper(testId)
  } catch (e) {
    alert(`加载失败：${e.message}`)
    return
  }
  const sec = data.sections.find((s) => s.paper === paper && s.part === part)
  const auth = getAuth()
  if (sec) renderPractice(el, testId, sec, auth ? auth.role : 'teacher')
}

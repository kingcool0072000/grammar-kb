// 微练习（学生端 #/micro）：每天 3 道小题（错题重练/变形/时态/短语动词/习语/语法），
// 完成当天点亮。数据全走后端（/micro/today、/micro/submit，提交幂等可重复调）。
// 已答过的题刷新后直接恢复结果态：拿 answered 重放一次提交取回对错与解析
// （后端幂等，不会重复计分）；没答完的继续作答，只提交新答的题。
import { api } from '../api.js'
import { finishBlessing, showBirthdayToast } from '../birthday.js'
import { escapeHtml, escAttr } from '../render.js'

// 分类 → 徽标文案与配色（颜色见 styles.css 末尾 .mc-badge.*）
const CAT = {
  wrong: '错题重练',
  variant: '变形题',
  tense: '时态',
  phrasal: '短语动词',
  idiom: '习语',
  grammar: '语法',
}
const catLabel = (c) => CAT[c] || '语法'
const letterOf = (i) => String.fromCharCode(65 + i) // 0 -> A

export async function mountMicro(el, { role } = {}) {
  el.innerHTML = `
    <button class="fce-back-btn" id="mc-back">← 返回课程表</button>
    <div class="view-head">
      <h1>✏️ 微练习</h1>
      <p id="mc-date">加载中… · 每天 3 题，完成当天点亮</p>
    </div>
    <div id="mc-body"><p class="muted">加载中…</p></div>`
  el.querySelector('#mc-back').addEventListener('click', () => {
    location.hash = '/timetable'
  })

  // ---- 页面状态 ----
  let data = null // /micro/today 原始返回
  const answered = {} // qid -> 我选的字母（含历史上报）
  const results = {} // qid -> 提交返回 {id,choice,answer,correct,explain,topic,category}
  let done = false
  let correctN = null

  const body = el.querySelector('#mc-body')

  try {
    data = await api.microToday()
  } catch (e) {
    body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
  mountHistory(el)

  const questions = Array.isArray(data.questions) ? data.questions : []
  done = !!data.done
  const dateEl = el.querySelector('#mc-date')
  if (dateEl && data.date) dateEl.innerHTML = `${escapeHtml(data.date)} · 每天 3 题，完成当天点亮`

  // 中断续答：answered 里已有记录 → 重放一次提交取回对错/解析（幂等，不重复计分）
  const known = Object.entries(data.answered || {})
    .map(([id, choice]) => ({ id: Number(id), choice }))
    .filter((a) => a.choice)
  if (known.length) {
    try {
      const res = await api.microSubmit(known)
      mergeResult(res)
    } catch { /* 结果拉不回来就先显示已选字母，答完剩下的再一起提交 */ }
  }
  render()

  function mergeResult(res) {
    data.answered = data.answered || {}
    for (const r of res.results || []) {
      if (r && r.id != null) {
        results[r.id] = r
        answered[r.id] = r.choice || answered[r.id]
        data.answered[r.id] = answered[r.id] // 已交卷：下一轮提交不再带上
      }
    }
    if (res.done) {
      done = true
      if (res.correct_n != null) correctN = res.correct_n
    }
    // 兜底：后端 done 漏翻但题已全部交卷 → 本地按完成渲染，免得卡在无按钮状态
    if (!done && questions.length && questions.every((q) => results[q.id])) done = true
  }

  function render() {
    // 空池：今天没题可做
    if (!questions.length) {
      body.innerHTML = `
        <div class="mc-card mc-empty">题池准备中，明天再来看看</div>`
      return
    }

    const total = questions.length
    const answeredN = questions.filter((q) => answered[q.id]).length
    const cards = questions.map((q, i) => questionCard(q, i)).join('')
    const doneCard = done ? doneCardHtml(total) : ''
    const submitHtml = (!done && answeredN < total) ? `
      <div class="mc-actions">
        <button class="btn-primary" id="mc-submit">提交答案</button>
        <span class="mc-actions-hint">选好就能交，${total - answeredN} 题未答</span>
      </div>` : ''

    body.innerHTML = doneCard + cards + submitHtml
    bindChoices()
    bindSubmit()
  }

  function questionCard(q, i) {
    const L = letterOf
    const r = results[q.id]
    const pick = answered[q.id]
    const opts = Array.isArray(q.options) ? q.options : []
    const optHtml = opts.map((t, j) => {
      const key = L(j)
      const cls = ['mc-opt']
      if (r) {
        cls.push('locked')
        if (key === r.answer) cls.push('right')
        if (!r.correct && key === pick) cls.push('wrong')
      } else if (key === pick) cls.push('picked')
      return `
        <div class="${cls.join(' ')}" data-opt="${key}">
          <span class="mc-opt-key">${key}</span>
          <span class="mc-opt-text">${escapeHtml(t)}</span>
          ${r && key === r.answer ? '<span class="mc-opt-tag">正确答案</span>' : ''}
        </div>`
    }).join('')

    const verdict = r
      ? `<span class="mc-verdict ${r.correct ? 'ok' : 'no'}">${r.correct ? '✅ 答对了' : '❌ 答错了'}</span>`
      : ''
    const answerRow = r && !r.correct
      ? `<p class="mc-answer">正确答案：<b>${escapeHtml(r.answer)}</b>. ${escapeHtml(opts[(r.answer || '').charCodeAt(0) - 65] || '')}</p>`
      : ''
    const explain = r && r.explain
      ? `<div class="mc-explain"><b>解析</b>${escapeHtml(r.explain)}</div>`
      : ''

    return `
      <section class="mc-card" data-qid="${q.id}">
        <div class="mc-card-head">
          <span class="mc-badge ${escAttr(q.category || 'grammar')}">${catLabel(q.category)}</span>
          ${q.topic ? `<span class="mc-topic">${escapeHtml(q.topic)}</span>` : ''}
          <span class="mc-qnum">第 ${i + 1} 题</span>
          ${verdict}
        </div>
        <p class="mc-stem">${escapeHtml(q.stem)}</p>
        <div class="mc-opts">${optHtml}</div>
        ${answerRow}
        ${explain}
      </section>`
  }

  function doneCardHtml(total) {
    const scoreLine = correctN != null
      ? `<p class="mc-done-sub">答对 ${correctN}/${total}，错题记得看解析</p>`
      : '<p class="mc-done-sub">明天还有 3 题等你</p>'
    const bday = finishBlessing('micro')
    return `
      <div class="mc-card mc-done-card">
        <b>今日微练习已完成 ✓</b>
        ${scoreLine}
        ${bday ? `<p class="bday-finish">${escapeHtml(bday)}</p>` : ''}
      </div>`
  }

  // ---- 交互：点选 / 提交（防连点：提交中禁用按钮） ----
  function bindChoices() {
    body.querySelectorAll('.mc-card').forEach((card) => {
      const q = questions.find((x) => String(x.id) === card.dataset.qid)
      if (!q || answered[q.id]) return // 已答过的题锁定，不再接收点选
      card.addEventListener('click', (e) => {
        const opt = e.target.closest('.mc-opt')
        if (!opt || opt.classList.contains('locked')) return
        card.querySelectorAll('.mc-opt').forEach((c) => c.classList.remove('picked'))
        opt.classList.add('picked')
        answered[q.id] = opt.dataset.opt
      })
    })
  }

  function bindSubmit() {
    const btn = body.querySelector('#mc-submit')
    if (!btn) return
    btn.addEventListener('click', async () => {
      // 只提交这次新答的题（answered 里已有的跳过，重复提交虽幂等但没必要）
      const answers = questions
        .filter((q) => !data.answered?.[q.id] && answered[q.id])
        .map((q) => ({ id: q.id, choice: answered[q.id] }))
      if (!answers.length) return
      btn.disabled = true
      btn.textContent = '提交中…'
      try {
        const wasDone = done
        const res = await api.microSubmit(answers)
        mergeResult(res)
        const justFinished = !wasDone && done
        render()
        if (justFinished) showBirthdayToast('micro')
      } catch (e) {
        alert(`提交失败：${e.message}`)
        btn.disabled = false
        btn.textContent = '提交答案'
      }
    })
  }
}

// ---- 📜 做题记录（学生回看历史，只读不交互） ----
// 数据 = GET /micro/history（按天倒序）。与今日答题区互不影响：
// 题卡直接渲染「我的作答 + 正确答案 + 解析」锁定态，不再接收点选。
export function historyDayHtml(day) {
  const qs = Array.isArray(day.questions) ? day.questions : []
  const cards = qs.map((q, i) => {
    const opts = Array.isArray(q.options) ? q.options : []
    const optHtml = opts.map((t, j) => {
      const key = letterOf(j)
      const cls = ['mc-opt', 'locked']
      if (key === q.answer) cls.push('right')
      if (!q.my_correct && key === q.my_choice) cls.push('wrong')
      return `
        <div class="${cls.join(' ')}">
          <span class="mc-opt-key">${key}</span>
          <span class="mc-opt-text">${escapeHtml(t)}</span>
          ${key === q.answer ? '<span class="mc-opt-tag">正确答案</span>' : ''}
        </div>`
    }).join('')
    const verdict = `<span class="mc-verdict ${q.my_correct ? 'ok' : 'no'}">${q.my_correct ? '✅ 答对了' : '❌ 答错了'}</span>`
    const answerRow = !q.my_correct
      ? `<p class="mc-answer">正确答案：<b>${escapeHtml(q.answer || '')}</b>. ${escapeHtml(opts[(q.answer || 'A').charCodeAt(0) - 65] || '')}</p>` : ''
    const explain = q.explain
      ? `<div class="mc-explain"><b>解析</b>${escapeHtml(q.explain)}</div>` : ''
    return `
      <section class="mc-card">
        <div class="mc-card-head">
          <span class="mc-badge ${escAttr(q.category || 'grammar')}">${catLabel(q.category)}</span>
          ${q.topic ? `<span class="mc-topic">${escapeHtml(q.topic)}</span>` : ''}
          <span class="mc-qnum">第 ${i + 1} 题</span>
          ${verdict}
        </div>
        <p class="mc-stem">${escapeHtml(q.stem)}</p>
        <div class="mc-opts">${optHtml}</div>
        ${answerRow}
        ${explain}
      </section>`
  }).join('')
  return cards
}

function mountHistory(el) {
  const wrap = document.createElement('div')
  wrap.className = 'mc-history'
  wrap.innerHTML = `
    <button class="mc-hist-toggle" type="button">📜 做题记录</button>
    <div class="mc-hist-body" hidden><p class="muted">加载中…</p></div>`
  el.appendChild(wrap)
  const histBody = wrap.querySelector('.mc-hist-body')
  let loaded = false
  wrap.querySelector('.mc-hist-toggle').addEventListener('click', async () => {
    const open = !histBody.hidden
    histBody.hidden = open
    wrap.classList.toggle('open', !open)
    if (open || loaded) return
    loaded = true
    let days = []
    try {
      days = await api.microHistory(14)
    } catch (e) {
      histBody.innerHTML = `<p style="color:#b42318">记录加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    if (!Array.isArray(days) || !days.length) {
      histBody.innerHTML = '<p class="muted">还没有做题记录，完成今天的 3 题就有了。</p>'
      return
    }
    histBody.innerHTML = days.map((d) => `
      <details class="mc-hist-day"${d.date === (new Date()).toISOString().slice(0, 10) ? ' open' : ''}>
        <summary>
          <b>${escapeHtml(d.date)}</b>
          <span class="mc-hist-score">${d.correct_n != null ? `答对 ${d.correct_n}/${d.total}` : `做了 ${d.n}/${d.total}`}</span>
        </summary>
        ${historyDayHtml(d)}
      </details>`).join('')
  })
}

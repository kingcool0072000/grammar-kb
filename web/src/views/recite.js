import { escapeHtml } from '../render.js'

// 背单词：全屏沉浸卡片。三种题型——认词（英→中）、拼写（中→英，打字输入）、
// 动词变形（go 的过去式？）。错词间隔重现直到答对；进度存 localStorage。
//
// 数据来自 /vocabulary（讲义词表）；特殊拼写词（special_spellings 非空，
// 即不规则动词/辅音双写/y→i 等）在拼写题里优先出。
const POS_CN = { v: '动词', n: '名词', adj: '形容词', adv: '副词', prep: '介词', conj: '连词', pron: '代词', num: '数词', proper: '专名' }
const FORM_CN = {
  past: '过去式', past_participle: '过去分词', present_participle: '现在分词',
  third_singular: '第三人称单数', plural: '复数', comparative: '比较级', superlative: '最高级',
}
const FORM_KEYS = ['past', 'past_participle', 'present_participle', 'third_singular', 'comparative', 'superlative', 'plural']
// 孩子按年级习惯用全拼，但音标键太挤；去掉重复的 ci/ck 后恰好能全放下
const KEY_ROWS = [
  'qwertyuiop'.split(''),
  'asdfghjkl'.split(''),
  ['⇧', ...'zxcvbnm'.split(''), '⌫'],
  ['我记住了', '不认识'],
]
const GROUP_SIZES = [10, 20, 30]

// ---- 出题 ----------------------------------------------------------------- //

function shuffle(arr) {
  const a = arr.slice()
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

function entryPos(e) {
  return (e.pos || []).filter((p) => POS_CN[p]).slice(0, 3)
}

function entryMeaning(e) {
  return (
    (e.gloss_lines && e.gloss_lines[0] && e.gloss_lines[0].text) ||
    e.gloss ||
    (e.meanings || [])[0] ||
    '—'
  )
}

/**
 * 生成一道题。
 * - 'en2zh'  英→中 认词
 * - 'zh2en'  中→英 拼写（打字输入）
 * - 'form'   词形变化（go 的过去式？），动词时态为主，形容词比较级也考
 */
function makeQuestion(e) {
  const forms = e.forms || {}
  const formKeys = FORM_KEYS.filter((k) => forms[k])
  const isVerb = (e.pos || []).includes('v')
  const hasAdjForm = forms.comparative || forms.superlative
  // 拼写+变形为主：认词 25% / 拼写 50% / 变形 25%（有变形可用时）
  const roll = Math.random()
  if (formKeys.length && (isVerb || hasAdjForm) && roll < 0.25) {
    const key = formKeys[Math.floor(Math.random() * formKeys.length)]
    return { type: 'form', entry: e, key, answer: forms[key], meaning: entryMeaning(e) }
  }
  if (roll < 0.75) return { type: 'zh2en', entry: e, answer: e.display || e.word, meaning: entryMeaning(e) }
  return { type: 'en2zh', entry: e, answer: '', meaning: entryMeaning(e) }
}

function buildQuiz(pool, size) {
  return shuffle(pool).slice(0, size).map((e) => makeQuestion(e))
}

// ---- 判定 ----------------------------------------------------------------- //

function eqIgnoringCase(a, b) {
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}

/** 逐字符比对：返回 HTML（错的标红，缺的补位，多的截断）。 */
function diffHtml(answer, input) {
  const a = answer.toLowerCase()
  const b = input.toLowerCase()
  let out = ''
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (i >= b.length) {
      out += `<span class="diff-ch missing">${escapeHtml(a[i])}</span>`
    } else if (i >= a.length || b[i] !== a[i]) {
      out += `<span class="diff-ch wrong">${escapeHtml(b[i])}</span>`
    } else {
      out += `<span class="diff-ch">${escapeHtml(a[i])}</span>`
    }
  }
  return out
}

// ---- 进度（localStorage）-------------------------------------------------- //

const LS_KEY = 'gkb-recite-progress-v1'
const LS_SESSIONS = 'gkb-recite-sessions-v1' // 每组一次：{t, sec, total, wrong}

function loadProgress() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY)) || {}
  } catch {
    return {}
  }
}

function loadSessions() {
  try {
    return JSON.parse(localStorage.getItem(LS_SESSIONS)) || []
  } catch {
    return []
  }
}

function saveResult(word, type, correct) {
  const p = loadProgress()
  const item = p[word] || { right: 0, wrong: 0 }
  item[correct ? 'right' : 'wrong'] += 1
  p[word] = item
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(p))
  } catch {
    /* 隐私模式等存不进就算了 */
  }
}

function saveSession(sec, total, wrongCount) {
  const list = loadSessions()
  list.push({ t: Date.now(), sec, total, wrong: wrongCount })
  try {
    localStorage.setItem(LS_SESSIONS, JSON.stringify(list.slice(-100)))
  } catch {
    /* 存不进就算了 */
  }
}

function fmtDuration(sec) {
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
  return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`
}

// ---- 视图 ----------------------------------------------------------------- //

export function mountRecite(el, { vocab, role }) {
  // 学生只能提交记录，不能清除（与成绩管理权限一致）
  const canClear = role === 'teacher'

  el.innerHTML = `
    <div class="view-head">
      <h1>背单词</h1>
      <p>基于词汇表（${vocab.length} 词）的全屏卡片：认词、中→英拼写、动词时态变形。答错的词会重复出现直到答对。</p>
    </div>
    <div class="card recite-dash" id="rc-dash"></div>
    <div class="recite-setup card">
      <div class="recite-field">
        <label>背哪些词</label>
        <div class="chip-row" id="rc-scope">
          <button class="chip active" data-scope="all">全部</button>
          <button class="chip" data-scope="verb">只动词</button>
          <button class="chip" data-scope="special">特殊拼写</button>
        </div>
      </div>
      <div class="recite-field">
        <label>每组数量</label>
        <div class="chip-row" id="rc-size">
          ${GROUP_SIZES.map((n, i) => `<button class="chip ${i === 1 ? 'active' : ''}" data-size="${n}">${n} 词</button>`).join('')}
        </div>
      </div>
      <div class="recite-field">
        <label>拼写作答方式</label>
        <div class="chip-row" id="rc-mode">
          <button class="chip active" data-mode="type">打字输入</button>
          <button class="chip" data-mode="flip">翻面自评</button>
        </div>
      </div>
      <button class="btn-primary" id="rc-start">开始背单词</button>
    </div>
    <div class="recite-stats" id="rc-stats"></div>
  `

  const state = { scope: 'all', size: 20, mode: 'type' }

  // 看板 + 统计一起渲染（哈1常见单词表进度）
  function renderBoard() {
    const $dash = el.querySelector('#rc-dash')
    const $stats = el.querySelector('#rc-stats')
    const p = loadProgress()
    const words = Object.keys(p)
    const total = vocab.length
    const learned = words.length
    const mastered = words.filter((w) => p[w].right > 0 && p[w].right >= p[w].wrong).length
    const hardN = words.filter((w) => p[w].wrong > p[w].right).length
    const pct = total ? Math.round((learned / total) * 100) : 0
    const sessions = loadSessions()
    const totalSec = sessions.reduce((s, x) => s + (x.sec || 0), 0)

    $dash.innerHTML = `
      <div class="recite-dash-head">
        <h3>哈1常见单词表</h3>
        <span class="recite-dash-pct">${pct}%</span>
      </div>
      <div class="dash-bar"><i style="width:${pct}%"></i></div>
      <p class="muted">共 ${total} 词 · 已背 ${learned} 词 · 掌握 ${mastered} 词 · 易错 ${hardN} 词${sessions.length ? ` · 累计 ${sessions.length} 组 / 用时 ${fmtDuration(totalSec)}` : ''}</p>
    `

    if (!words.length && !sessions.length) {
      $stats.innerHTML = ''
      return
    }
    const totalRight = words.reduce((s, w) => s + p[w].right, 0)
    const totalWrong = words.reduce((s, w) => s + p[w].wrong, 0)
    const hard = words
      .filter((w) => p[w].wrong >= p[w].right)
      .sort((a, b) => p[b].wrong - p[a].wrong)
      .slice(0, 20)
    $stats.innerHTML = `
      <div class="card recite-stats-card">
        <h3>已背 ${words.length} 词 · 累计答对 ${totalRight} 次 / 答错 ${totalWrong} 次</h3>
        ${hard.length ? `<p class="muted">易错词：${hard.map((w) => `<span class="vocab-form">${escapeHtml(w)}</span>`).join(' ')}</p>` : ''}
        ${canClear ? '<button class="chip" id="rc-clear">清除记录</button>' : ''}
      </div>
    `
    const $clear = $stats.querySelector('#rc-clear')
    if ($clear)
      $clear.addEventListener('click', () => {
        localStorage.removeItem(LS_KEY)
        localStorage.removeItem(LS_SESSIONS)
        renderBoard()
      })
  }

  function pool() {
    if (state.scope === 'verb') return vocab.filter((e) => (e.pos || []).includes('v'))
    if (state.scope === 'special') return vocab.filter((e) => (e.special_spellings || []).length)
    return vocab
  }

  el.querySelectorAll('.chip-row').forEach((row) => {
    row.addEventListener('click', (e) => {
      const chip = e.target.closest('.chip')
      if (!chip) return
      row.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c === chip))
      const d = chip.dataset
      if (d.scope) state.scope = d.scope
      if (d.size) state.size = Number(d.size)
      if (d.mode) state.mode = d.mode
    })
  })

  el.querySelector('#rc-start').addEventListener('click', () => {
    const p = pool()
    if (!p.length) return
    startSession(el, {
      pool: p,
      size: Math.min(state.size, p.length),
      mode: state.mode,
      scope: state.scope,
      onFinish: () => renderBoard(),
    })
  })

  renderBoard()
}

// ---- 全屏会话 -------------------------------------------------------------- //

function startSession(rootEl, { pool, size, mode, scope = '', onFinish }) {
  // 播放队列：初始题 + 错词重现（答错后间隔 3 题再插一次）
  let queue = buildQuiz(pool, size)
  let done = 0
  const wrongEntries = []
  const totalFirst = queue.length
  let idx = 0
  let overlay = null
  const t0 = Date.now() // 本组计时（结算页与累计时长都用）

  function currentQ() {
    return queue[idx]
  }

  function requeue(entry) {
    const at = Math.min(idx + 4, queue.length) // 间隔 3 题重现
    queue.splice(at, 0, makeQuestion(entry))
  }

  function finish() {
    const total = done
    // 注意：wrongEntries 只记首答错；重现答对不影响它
    const uniqWrong = [...new Set(wrongEntries.map((e) => e.word))]
    const sec = Math.round((Date.now() - t0) / 1000)
    saveSession(sec, total, uniqWrong.length)
    // 成绩静默上报教师端（尽力而为：失败不影响本地流程）
    uploadSession(total, uniqWrong, sec)
    overlay.remove()
    overlay = null
    mountResult(rootEl, {
      total,
      uniqWrong,
      acc: Math.round(((total - uniqWrong.length) / total) * 100),
      duration: sec,
      pool,
      size,
      mode,
      scope,
    })
    if (onFinish) onFinish()
  }

  function uploadSession(total, uniqWrong, sec) {
    import('../api.js').then(({ api }) => {
      return api.reciteSubmit({
        total,
        wrong: uniqWrong.length,
        acc: Math.round(((total - uniqWrong.length) / total) * 100),
        duration_sec: sec,
        wrong_words: uniqWrong,
        mode,
        scope,
      })
    }).catch(() => { /* 离线/未登录时静默丢弃 */ })
  }

  overlay = document.createElement('div')
  overlay.className = 'recite-overlay'
  document.body.appendChild(overlay)

  // 卡片切换/退出前，清掉挂到 document 上的键盘监听
  const cleanupKeys = () => {
    if (overlay.__removeKey) {
      overlay.__removeKey()
      overlay.__removeKey = null
    }
  }

  function renderQ() {
    const q = currentQ()
    cleanupKeys()
    if (!q) return finish()
    renderCard(overlay, {
      q,
      idx: done + 1,
      total: totalFirst + (queue.length - totalFirst),
      mode,
      onAnswer(correct) {
        saveResult(q.entry.word, q.type, correct)
        done += 1
        if (!correct) {
          if (!wrongEntries.some((e) => e.word === q.entry.word)) wrongEntries.push(q.entry)
          requeue(q.entry)
        }
        idx += 1
        renderQ()
      },
      onQuit() {
        cleanupKeys()
        overlay.remove()
        overlay = null
      },
    })
  }

  renderQ()
}

// ---- 单张卡片 -------------------------------------------------------------- //

function renderCard(overlay, { q, idx, total, mode, onAnswer, onQuit }) {
  const e = q.entry
  const posTags = entryPos(e).map((p) => `<span class="vocab-pos">${POS_CN[p]}</span>`).join('')
  const specials = (e.special_spellings || [])
    .map((s) => `<span class="rc-special">${escapeHtml(s)}</span>`)
    .join('')
  const example = (e.examples || [])[0]

  const promptHtml =
    q.type === 'en2zh'
      ? `<div class="rc-word">${escapeHtml(e.display || e.word)}</div>
         <div class="rc-phonetic">${escapeHtml(e.phonetic || '')} ${posTags}</div>`
      : q.type === 'zh2en'
        ? `<div class="rc-meaning">${escapeHtml(q.meaning)}</div>
           <div class="rc-phonetic">${posTags}${e.phonetic ? ` 音标稍后揭晓` : ''}</div>`
        : `<div class="rc-meaning"><b>${escapeHtml(e.display || e.word)}</b> 的${FORM_CN[q.key]}</div>
           <div class="rc-phonetic">${posTags} ${escapeHtml(entryMeaning(e))}</div>`

  overlay.innerHTML = `
    <div class="rc-top">
      <button class="rc-quit" title="退出">✕ 退出</button>
      <div class="rc-progress">${idx} / ${total}</div>
    </div>
    <div class="rc-card" data-state="front">
      <div class="rc-prompt">${promptHtml}</div>
      <div class="rc-answer-area"></div>
      <div class="rc-extra"></div>
    </div>
    <div class="rc-kb">${KEY_ROWS.map((row) => `<div class="rc-kb-row">${row.map((k) => `<button class="rc-key" data-key="${k}">${k}</button>`).join('')}</div>`).join('')}</div>
  `

  const $card = overlay.querySelector('.rc-card')
  const $area = overlay.querySelector('.rc-answer-area')
  const $extra = overlay.querySelector('.rc-extra')
  const $kb = overlay.querySelector('.rc-kb')

  overlay.querySelector('.rc-quit').addEventListener('click', onQuit)

  const revealDetail = () => {
    $extra.innerHTML = `
      <div class="rc-detail">
        ${specials ? `<div class="rc-special-row">${specials}</div>` : ''}
        ${Object.entries(e.forms || {})
          .filter(([k]) => FORM_CN[k])
          .map(
            ([k, v]) =>
              `<span class="rc-form"><i>${FORM_CN[k]}</i> ${k === q.key && q.type === 'form' ? `<b>${escapeHtml(v)}</b>` : escapeHtml(v)}</span>`,
          )
          .join('')}
        ${example ? `<div class="rc-example">${escapeHtml(example.en)}<br/><span class="muted">${escapeHtml(example.zh)}</span></div>` : ''}
      </div>
    `
  }

  if (q.type === 'en2zh' || (q.type !== 'form' && mode === 'flip')) {
    // 认词 / 翻面自评：先看正面，点击卡片或按空格翻面自评
    $card.dataset.state = 'front'
    $area.innerHTML = '<div class="rc-hint">点击卡片或按空格键翻面</div>'
    $kb.style.display = 'none'
    const flip = () => {
      if ($card.dataset.state !== 'front') return
      $card.dataset.state = 'back'
      revealDetail()
      $area.innerHTML = `
        <div class="rc-self">
          <button class="rc-btn wrong" data-r="0">不认识</button>
          <button class="rc-btn fuzzy" data-r="1">模糊</button>
          <button class="rc-btn right" data-r="2">认识</button>
        </div>`
      $area.querySelectorAll('.rc-btn').forEach((b) =>
        b.addEventListener('click', () => onAnswer(b.dataset.r === '2')),
      )
    }
    $card.addEventListener('click', flip)
    const onKey = (ev) => {
      if ($card.dataset.state !== 'front') {
        if (ev.key === '1') onAnswer(false)
        if (ev.key === '3') onAnswer(true)
        return
      }
      if (ev.key === ' ' || ev.key === 'Enter') {
        ev.preventDefault()
        flip()
      }
    }
    document.addEventListener('keydown', onKey)
    // 会话结束后移除监听：卡片被替换前挂到 overlay 生命周期
    overlay.__removeKey = () => document.removeEventListener('keydown', onKey)
  } else {
    // 拼写 / 变形：打字输入判定
    let input = ''
    const answer = q.answer
    $kb.style.display = ''
    let shift = false

    const paint = () => {
      const shown = input || ''
      $area.innerHTML = `<div class="rc-input">${escapeHtml(shown) || '<span class="muted">输入答案…</span>'}</div>`
    }
    paint()

    const submit = (force = false) => {
      const val = input.trim()
      if (!val && !force) return
      const correct = val ? eqIgnoringCase(val, answer) : false
      // 判分后立即摘掉打字监听：回车交给"下一题"，字母不再改写判分展示
      document.removeEventListener('keydown', onPhys)
      $kb.style.display = 'none'
      revealDetail()
      $area.innerHTML = `
        <div class="rc-input final">${diffHtml(answer, input)}</div>
        <div class="rc-verdict ${correct ? 'ok' : 'no'}">${correct ? '✓ 正确' : '✗ 正确答案：' + escapeHtml(answer)}</div>
        <button class="rc-btn ${correct ? 'right' : 'wrong'}" id="rc-next">下一题（回车）</button>`
      overlay.querySelector('#rc-next').addEventListener('click', next)
      const onEnter = (ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault()
          next()
        }
      }
      document.addEventListener('keydown', onEnter)
      overlay.__removeKey = () => {
        document.removeEventListener('keydown', onEnter)
      }
      function next() {
        if (overlay.__removeKey) overlay.__removeKey()
        onAnswer(correct)
      }
    }

    const typeKey = (k) => {
      if (k === '⌫') input = input.slice(0, -1)
      else if (k === '⇧') {
        shift = !shift
        $kb.querySelectorAll('.rc-key[data-key]').forEach((b) => {
          if (/^[a-z]$/.test(b.dataset.key)) b.textContent = shift ? b.dataset.key.toUpperCase() : b.dataset.key
        })
        return
      } else if (k === '我记住了' || k === '不认识') {
        // 自评放弃：记住了=直接判对；不认识=强制交卷展示正确答案
        input = k === '我记住了' ? answer : ''
        submit(true)
        return
      } else input += k
      paint()
    }

    $kb.addEventListener('click', (ev) => {
      const key = ev.target.closest('.rc-key')
      if (key) typeKey(key.dataset.key)
    })

    const onPhys = (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault()
        submit()
      } else if (ev.key === 'Backspace') {
        ev.preventDefault()
        input = input.slice(0, -1)
        paint()
      } else if (/^[a-zA-Z]$/.test(ev.key)) {
        input += ev.key
        paint()
      }
    }
    document.addEventListener('keydown', onPhys)
    overlay.__removeKey = () => document.removeEventListener('keydown', onPhys)
  }
}

// ---- 结算页 ---------------------------------------------------------------- //

function mountResult(el, { total, uniqWrong, acc, duration, pool, size, mode, scope = '', onFinish }) {
  const wrap = document.createElement('div')
  wrap.className = 'recite-result card'
  wrap.innerHTML = `
    <h2>本组完成 🎉</h2>
    <p>共 ${total} 题 · 首答正确率 <b>${acc}%</b> · 用时 ${fmtDuration(duration)}</p>
    ${uniqWrong.length ? `<h3>错词（${uniqWrong.length}）</h3><p>${uniqWrong.map((w) => `<span class="vocab-form">${escapeHtml(w)}</span>`).join(' ')}</p>` : '<p>全对，太棒了！</p>'}
    <div class="chip-row">
      <button class="btn-primary" id="rr-again">再背一组</button>
      ${uniqWrong.length ? '<button class="btn-primary ghost" id="rr-wrong">只练错词</button>' : ''}
    </div>
  `
  el.appendChild(wrap)
  wrap.querySelector('#rr-again').addEventListener('click', () => {
    wrap.remove()
    startSession(el, { pool, size, mode, scope, onFinish })
  })
  const $wrong = wrap.querySelector('#rr-wrong')
  if ($wrong)
    $wrong.addEventListener('click', () => {
      const entries = pool.filter((e) => uniqWrong.includes(e.word))
      wrap.remove()
      startSession(el, { pool: entries, size: Math.min(size, entries.length), mode, scope, onFinish })
    })
  wrap.scrollIntoView({ behavior: 'smooth' })
}

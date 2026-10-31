import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import {
  atlasFigure, bindAtlas, fillAtlasHost, getAccent, phoneticHtml,
  playPronunciation, rememberAtlas, speakWord, stopAtlasAudio, wordAudioUrl,
} from '../atlas.js'
import { getWordCard } from '../wordcard.js'

// 背单词：全屏沉浸卡片。三种题型——中译英（看释义拼单词）、英译中（认词）、
// 拼写挖空（缺字母补全），统一「翻面自评」作答；错词间隔重现直到答对。
// 翻面揭晓答案时自动 TTS 发音一次（右上角 🔊 可静音）。
//
// 错题本走艾宾浩斯记忆周期（D+1/2/4/7/15/30 六节点）：到期词优先复习，
// 六节点全过 → 已攻克词本；任何会话再答错则回炉重计。
//
// 进度不再存 localStorage：逐词对错实时上报云端（recite_word_progress），
// 看板/出题「未掌握词池」/历史记录全部走云端 API。首次进入时若检测到
// 旧版本遗留的本地进度（gkb-recite-progress-v1），一次性合并上云后清除。
//
// 词库来自 /vocab-levels（L0 哈一课本语料词，L1-L5 分层词表，教师解锁）。

const POS_CN = { v: '动词', n: '名词', adj: '形容词', adv: '副词', prep: '介词', conj: '连词', pron: '代词', num: '数词', proper: '专名' }
const FORM_CN = {
  past: '过去式', past_participle: '过去分词', present_participle: '现在分词',
  third_singular: '第三人称单数', plural: '复数', comparative: '比较级', superlative: '最高级',
}
const FORM_KEYS = ['past', 'past_participle', 'present_participle', 'third_singular', 'comparative', 'superlative', 'plural']
const GROUP_SIZES = [20, 30]
const TTS_KEY = 'gkb-rc-tts-v1' // 翻面自动发音开关（默认开）

function ttsOn() {
  try {
    return localStorage.getItem(TTS_KEY) !== 'off'
  } catch {
    return true
  }
}

function setTtsOn(on) {
  try {
    if (on) localStorage.removeItem(TTS_KEY)
    else localStorage.setItem(TTS_KEY, 'off')
  } catch { /* 忽略 */ }
}

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

/** 拼写挖空的遮罩：遮约 40% 字母（≥1 个），非字母字符（- ' 空格）保留。 */
function maskWord(word) {
  const w = (word || '').toLowerCase()
  const letters = [...w].map((c, i) => (/[a-z]/.test(c) ? i : -1)).filter((i) => i >= 0)
  if (!letters.length) return w
  const maskN = Math.max(1, Math.round(letters.length * 0.4))
  const pick = new Set(shuffle(letters).slice(0, maskN))
  return [...w].map((c, i) => (pick.has(i) ? '_' : c)).join('')
}

/**
 * 生成一道题。
 * - 'zh2en'  中译英（看中文释义，拼出单词）
 * - 'en2zh'  英译中（认词）
 * - 'cloze'  拼写挖空（缺字母补全）
 */
function makeQuestion(e) {
  const answer = e.display || e.word
  const roll = Math.random()
  if (roll < 0.5) return { type: 'zh2en', entry: e, answer, meaning: entryMeaning(e) }
  if (roll < 0.75) return { type: 'en2zh', entry: e, answer: '', meaning: entryMeaning(e) }
  return { type: 'cloze', entry: e, answer, masked: maskWord(answer), meaning: entryMeaning(e) }
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

// ---- 进度（云端 recite_word_progress；本地仅遗留数据一次性迁移）---------- //

const LS_KEY = 'gkb-recite-progress-v1' // 旧版遗留，迁移后清除
const LS_SESSIONS = 'gkb-recite-sessions-v1' // 旧版遗留，迁移后清除（历史已上云）

/**
 * 旧本地进度一次性合并上云（max 语义幂等），成功后清掉遗留 key。
 * 返回是否执行了迁移（用于提示）；失败静默（下次进来再试）。
 */
async function migrateLegacyProgress() {
  let legacy = null
  try {
    legacy = JSON.parse(localStorage.getItem(LS_KEY))
  } catch {
    legacy = null
  }
  if (!legacy || typeof legacy !== 'object' || !Object.keys(legacy).length) {
    // 无逐词遗留也清掉旧 sessions key（数据早已在云端）
    try {
      localStorage.removeItem(LS_SESSIONS)
    } catch { /* 忽略 */ }
    return false
  }
  try {
    await api.reciteProgressSync(legacy)
    localStorage.removeItem(LS_KEY)
    localStorage.removeItem(LS_SESSIONS)
    return true
  } catch {
    return false // 离线/接口异常：保留本地数据，下次再迁移
  }
}

function fmtDuration(sec) {
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
  return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`
}

// ---- 视图 ----------------------------------------------------------------- //

export async function mountRecite(el, { vocab, role }) {
  // 学生只能提交记录，不能清除（与成绩管理权限一致）
  const canClear = role === 'teacher'

  el.innerHTML = `
    <div class="view-head">
      <h1>背单词</h1>
      <p>基于词汇表（${vocab.length} 词）的全屏卡片：中译英、英译中、拼写挖空。翻面自动发音，答错的词按艾宾浩斯记忆法复习（1、2、4、7、15、30 天），直到攻克。</p>
      <p class="atlas-footnote">部分单词的插画和录音来自 <a href="https://github.com/designfuchen/word-atlas" target="_blank" rel="noopener">词光 Word Atlas</a>，<a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener">CC BY 4.0</a>，署名 Word Atlas contributors（词光）。没有录音时仍用浏览器语音。<a href="#/config">发音设置</a></p>
    </div>
    <div id="rc-loop"></div>
    <div class="card recite-dash recite-dash-secondary" id="rc-dash"></div>
    <div class="recite-setup card">
      <div class="recite-field">
        <label>词库</label>
        <div class="chip-row" id="rc-scope">词库加载中…</div>
      </div>
      <div class="recite-field">
        <label>每组数量</label>
        <div class="chip-row" id="rc-size">
          ${GROUP_SIZES.map((n, i) => `<button class="chip ${i === 0 ? 'active' : ''}" data-size="${n}">${n} 词</button>`).join('')}
        </div>
      </div>
      <button class="btn-primary" id="rc-start">开始背单词</button>
    </div>
    <div class="recite-stats" id="rc-stats"></div>
    <div id="rc-history"></div>
    <div id="rc-wrongbook"></div>
  `

  const state = { scope: 'all', size: 20, mode: 'flip' }
  let reviewSummary = null
  // 词库默认激活已解锁的最新级别（L0 只是兜底；解锁信息加载后校正）
  // 词库/云端进度加载完成前禁用开始钮：pool 为空时点击会静默无反应
  const startBtn = el.querySelector('#rc-start')
  startBtn.disabled = true

  // ---- 旧本地进度一次性迁移上云（malin 的 559 词次历史无缝衔接）----
  const migrated = await migrateLegacyProgress()
  if (migrated) console.info('[recite] 本地历史进度已合并上云')

  // ---- 云端逐词进度（看板与出题「未掌握词池」的唯一数据源）----
  let cloudProgress = { total_seen: 0, mastered: 0, levels: {}, words: [] }
  try {
    cloudProgress = await api.reciteProgress({ includeWords: true })
  } catch {
    // 接口不可用（离线）：进度看板为空态，仍可背词但不上进度
  }

  const wordProgress = new Map((cloudProgress.words || []).map((w) => [w.word, w]))
  const masteredSet = new Set(
    (cloudProgress.words || []).filter((w) => w.mastered).map((w) => w.word),
  )

  // ---- 词库层级选择（/vocab-levels 按解锁截断：拿不到的层=锁定） ----
  const LEVEL_MAX = 5
  let levelCounts = {}
  let unlocked = 0
  try {
    const d = await api.vocabLevels({ maxLevel: LEVEL_MAX })
    levelCounts = d.counts || {}
    unlocked = Number(d.unlocked ?? 0)
  } catch { /* 兜底：chips 退化为基础一项 */ }

  function renderScopeChips() {
    const row = el.querySelector('#rc-scope')
    if (!row) return
    if (!Object.keys(levelCounts).length) {
      row.innerHTML = `<button class="chip active" data-scope="all">基础（哈一 ${vocab.length} 词）</button>`
      state.scope = 'all'
      return
    }
    // 默认激活已解锁的最新级别（首访；用户手动切过就不再自动跳）
    if (!state.scopeTouched) {
      state.scope = unlocked > 0 ? String(unlocked) : 'all'
    }
    const cur = state.scope === 'all' ? 0 : Number(state.scope)
    row.innerHTML = [0, 1, 2, 3, 4, 5]
      .map((lv) => {
        const n = levelCounts[String(lv)] || 0
        const locked = lv > unlocked
        const label = lv === 0 ? 'L0 课本' : `L${lv}`
        return `<button class="chip ${lv === cur ? 'active' : ''} ${locked ? 'locked' : ''}"
          data-scope="${lv === 0 ? 'all' : lv}" ${locked ? 'disabled title="这一级还没开放，请老师在单词级别里设置"' : ''}>${locked ? '🔒 ' : ''}${label} · ${n} 词</button>`
      })
      .join('')
  }
  renderScopeChips()

  // 看板 + 统计一起渲染（云端口径：当前层词进度）
  function renderBoard() {
    const $dash = el.querySelector('#rc-dash')
    const $stats = el.querySelector('#rc-stats')
    const scopeLevel = state.scope === 'all' ? 0 : Number(state.scope)
    const lvWords = (cloudProgress.words || []).filter((w) => w.level === scopeLevel)
    const total = pool().length
    const learned = lvWords.length
    const mastered = lvWords.filter((w) => w.mastered).length
    const hardN = lvWords.filter((w) => w.wrong > w.right).length
    const pct = total ? Math.round((learned / total) * 100) : 0
    const sessions = cloudSessions
    const totalSec = sessions.reduce((s, x) => s + (x.duration_sec || 0), 0)

    $dash.innerHTML = `
      <div class="recite-dash-head">
        <h3>${state.scope === 'all' ? 'L0 课本单词' : 'L' + state.scope + ' 单词'}</h3>
        <span class="recite-dash-pct">${pct}%</span>
      </div>
      <div class="dash-bar"><i style="width:${pct}%"></i></div>
      <p class="muted">共 ${total} 词 · 已背 ${learned} 词 · 掌握 ${mastered} 词 · 易错 ${hardN} 词${sessions.length ? ` · 累计 ${sessions.length} 组 / 用时 ${fmtDuration(totalSec)}` : ''}</p>
    `

    if (!learned && !sessions.length) {
      $stats.innerHTML = ''
      return
    }
    const totalRight = lvWords.reduce((s, w) => s + w.right, 0)
    const totalWrong = lvWords.reduce((s, w) => s + w.wrong, 0)
    const hard = lvWords
      .filter((w) => w.wrong >= w.right)
      .sort((a, b) => b.wrong - a.wrong)
      .slice(0, 20)
    $stats.innerHTML = `
      <div class="card recite-stats-card">
        <h3>已背 ${learned} 词 · 累计答对 ${totalRight} 次 / 答错 ${totalWrong} 次</h3>
        ${hard.length ? `<p class="muted">易错词：${hard.map((w) => `<span class="vocab-form">${escapeHtml(w.word)}</span>`).join(' ')}</p>` : ''}
        ${canClear ? '<button class="chip" id="rc-clear">清除云端进度</button>' : ''}
      </div>
    `
    const $clear = $stats.querySelector('#rc-clear')
    if ($clear)
      $clear.addEventListener('click', async () => {
        if (!confirm('确认清空云端逐词进度？（练习记录保留，仅重置掌握状态）')) return
        try {
          await api.reciteProgressClear(role === 'teacher' ? (cloudProgress.user || '') : '')
        } catch { /* 失败静默 */ }
        try {
          cloudProgress = await api.reciteProgress({ includeWords: true })
        } catch { /* 忽略 */ }
        refreshFromCloud()
      })
  }

  // 分层词库词条（/vocab-levels 与上面 chips 共用同一次加载思路；
  // （独立请求 items，与 chips 那次分开，避免耦合渲染顺序）
  let levelVocab = []
  try {
    const items = (await api.vocabLevels({ maxLevel: LEVEL_MAX })).items || []
    // 标准化：/vocab-levels 把 forms/phonetic/special_spellings 放在
    // extra 里，卡片与出题读顶层——这里提升一次（历史 bug：顶层读
    // 拿不到 forms，词形变化题从未出过、音标不显示）
    levelVocab = items.map((it) => {
      const ex = it.extra || {}
      if (it.atlas) rememberAtlas(it.word, it.atlas)
      return {
        ...it,
        forms: it.forms || ex.forms || {},
        phonetic: it.phonetic || ex.phonetic || '',
        special_spellings: it.special_spellings || ex.special_spellings || [],
      }
    })
  } catch {
    levelVocab = [] // 接口失败时仍可用基础词表（下方兜底）
  } finally {
    startBtn.disabled = false
  }
  const byLevel = (lv) => levelVocab.filter((e) => e.level === lv)

  function pool() {
    if (state.scope === 'all') {
      // 兜底：接口失败时用传入的哈一基础表（与 L0 同源）
      return levelVocab.length ? byLevel(0) : vocab
    }
    return byLevel(Number(state.scope))
  }

  /** 出题池：当前层中「云端未掌握」的词。逾期未清时，首次见到的新词受 new_cap 限制。 */
  function freshPool() {
    const all = pool().filter((e) => !masteredSet.has((e.word || '').toLowerCase()))
    const cap = reviewSummary ? reviewSummary.new_cap : null
    if (cap == null) return all
    let room = Math.max(0, Number(cap) - Number(reviewSummary.new_used || 0))
    const seen = new Set((cloudProgress.words || []).map((w) => (w.word || '').toLowerCase()))
    const kept = []
    for (const e of all) {
      const w = (e.word || '').toLowerCase()
      if (seen.has(w)) kept.push(e)
      else if (room > 0) {
        kept.push(e)
        room -= 1
      }
    }
    return kept
  }

  function entryFor(word) {
    const e = levelVocab.find((x) => x.word === word)
      || vocab.find((x) => x.word === word)
      || { word, pos: [], meanings: [], gloss: '', level: -1, example: {}, extra: {} }
    const gloss = e.gloss || ''
    const meanings = (e.meanings && e.meanings.length) ? e.meanings : []
    return {
      ...e,
      gloss,
      meanings,
      display: '',
      examples: e.example && e.example.en ? [e.example] : (e.examples || []),
    }
  }

  // 词库里没有释义的错词，开练时再补最多 20 条，不在进页时拉整本错题。
  async function hydrateGloss(entries) {
    const missing = entries.filter((e) => !e.gloss && !(e.meanings && e.meanings.length))
    if (!missing.length) return
    try {
      const rows = await api.reciteGloss(missing.map((e) => e.word))
      const byWord = new Map((rows || []).map((r) => [String(r.word || '').toLowerCase(), r]))
      for (const e of missing) {
        const g = byWord.get(String(e.word || '').toLowerCase())
        if (!g) continue
        if (g.gloss) e.gloss = g.gloss
        if (g.meanings && g.meanings.length) e.meanings = g.meanings
        if (g.example && g.example.en) e.examples = [g.example]
      }
    } catch { /* 没有释义也能开练，卡片只显示单词 */ }
  }

  // 攻克：只背错词本（到期 / 逾期 / 在周期），不掺新词。答对走原有艾宾浩斯节点。
  async function startConquer(words) {
    const ordered = (words || []).filter(Boolean).slice(0, 20)
    const entries = shuffle(ordered.map((w) => entryFor(w)))
    if (!entries.length) return
    await hydrateGloss(entries)
    startSession(el, {
      pool: entries,
      size: entries.length,
      mode: 'conquer',
      scope: 'wrongbook',
      onFinish: () => { refreshFromCloud() },
    })
  }

  function renderReviewLoop() {
    const host = el.querySelector('#rc-loop')
    if (!host) return
    const s = reviewSummary
    if (!s) {
      host.innerHTML = ''
      return
    }
    const due = Number(s.due_today || 0)
    const over = Number(s.overdue || 0)
    const d7 = Number((s.conquered_n && s.conquered_n.d7) || 0)
    const totalCq = Number((s.conquered_n && s.conquered_n.total) || 0)
    const cap = s.new_cap
    const used = Number(s.new_used || 0)
    const capNote = cap == null
      ? ''
      : Number(cap) === 0
        ? ` · 逾期还没清完，今天先不背新词（已见 ${used}）`
        : ` · 逾期未清，今日新词 ${used}/${cap}`
    host.innerHTML = `
      <div class="rc-loop">
        <button type="button" class="rc-loop-card due" id="rc-loop-due" ${due ? '' : 'disabled'}>
          <b>${due}</b>
          <span>到期</span>
          <small>今天该复习</small>
        </button>
        <button type="button" class="rc-loop-card over" id="rc-loop-over" ${over ? '' : 'disabled'}>
          <b>${over}</b>
          <span>逾期</span>
          <small>过了复习日</small>
        </button>
        <button type="button" class="rc-loop-card cq" id="rc-loop-cq">
          <b>${d7}</b>
          <span>近7日攻克</span>
          <small>累计 ${totalCq} 词</small>
        </button>
      </div>
      <p class="rc-loop-sub">在周期 ${Number(s.in_cycle || 0)} 词${capNote}</p>`
    const dueBtn = host.querySelector('#rc-loop-due')
    if (dueBtn) dueBtn.addEventListener('click', () => startConquer(s.due_words || []))
    const overBtn = host.querySelector('#rc-loop-over')
    if (overBtn) overBtn.addEventListener('click', () => startConquer(s.overdue_words || []))
    const cqBtn = host.querySelector('#rc-loop-cq')
    if (cqBtn) cqBtn.addEventListener('click', () => { location.hash = '/conquered' })
  }

  function maybeAutoReview() {
    const jump = sessionStorage.getItem('gkb-recite-review')
    if (!jump || !reviewSummary) return
    sessionStorage.removeItem('gkb-recite-review')
    const words = jump === 'overdue'
      ? (reviewSummary.overdue_words || [])
      : (reviewSummary.due_words || [])
    if (words.length) startConquer(words)
  }

  // 完成一组后重拉云端进度并刷新看板（单词词状态已由上报更新）
  const refreshFromCloud = async () => {
    try {
      cloudProgress = await api.reciteProgress({ includeWords: true })
      wordProgress.clear()
      ;(cloudProgress.words || []).forEach((w) => wordProgress.set(w.word, w))
      masteredSet.clear()
      ;(cloudProgress.words || []).filter((w) => w.mastered).forEach((w) => masteredSet.add(w.word))
    } catch { /* 离线：保留旧快照 */ }
    try {
      reviewSummary = await api.reviewSummary()
    } catch { /* 摘要失败时沿用上一次 */ }
    renderBoard()
    renderReviewLoop()
    renderWrongbook()
  }


  // ---- 我的练习记录（云端；时长汇总也来自这里）----
  let cloudSessions = []
  try {
    cloudSessions = await api.reciteSessions({ limit: 100 })
  } catch {
    cloudSessions = []
  }
  const renderHistory = async () => {
    const $h = el.querySelector('#rc-history')
    if (!$h) return
    let recs
    try {
      recs = await api.reciteSessions({ limit: 50 })
    } catch {
      $h.innerHTML = ''
      return // 接口不可用（离线等）静默隐藏
    }
    if (!recs.length) {
      $h.innerHTML = `
        <section class="fce-group">
          <div class="fce-group-title">📝 我的练习记录</div>
          <p class="reading-hint">完成一组练习后，记录会出现在这里（老师也能看到你的成绩）。</p>
        </section>`
      return
    }
    const avgAcc = Math.round(recs.reduce((s, x) => s + (x.acc || 0), 0) / recs.length)
    const totalWords = recs.reduce((s, x) => s + (x.total || 0), 0)
    const totalMin = Math.round(recs.reduce((s, x) => s + (x.duration_sec || 0), 0) / 60)
    // 明细只列最近 3 次（汇总仍按全量口径）；易错词频次取最近 20 组
    const recent = recs.slice(0, 3)
    const freq = new Map()
    for (const s of recs.slice(0, 20)) {
      for (const w of s.wrong_words || []) freq.set(w, (freq.get(w) || 0) + 1)
    }
    const hardWords = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
    $h.innerHTML = `
      <section class="fce-group">
        <div class="fce-group-title">📝 我的练习记录（${recs.length} 组 · 平均正确率 ${avgAcc}% · 累计 ${totalWords} 词次${totalMin ? ` / ${totalMin} 分钟` : ''}）</div>
        ${recent.map((s) => `
          <div class="fce-his-row">
            <span class="fce-his-what">${scopeCn(s.scope)}${s.mode === 'conquer' ? '（攻克）' : (s.mode === 'flip' ? '（自评）' : '')}</span>
            <b class="fce-his-score ${s.acc >= 80 ? 'ok' : ''}">${s.acc}%</b>
            <span class="fce-his-date">${s.total} 词 · 错 ${s.wrong} · ${(s.created_at || '').slice(5, 10)}</span>
          </div>`).join('')}
        ${hardWords.length ? `<p class="reading-hint">易错词（最近 20 组）：${hardWords.map(([w, n]) => `${escapeHtml(w)}×${n}`).join('、')}</p>` : ''}
      </section>`
  }
  renderHistory()

  // ---- 错题本：只留计数和开练入口。词列表和逐词详情不再拉、不再画。 ----
  function renderWrongbook() {
    const host = el.querySelector('#rc-wrongbook')
    if (!host) return
    const s = reviewSummary
    if (!s) {
      host.innerHTML = ''
      return
    }
    const inCycle = Number(s.in_cycle || 0)
    const due = Number(s.due_today || 0)
    const over = Number(s.overdue || 0)
    const dueNow = due + over
    const conqueredN = Number((s.conquered_n && s.conquered_n.total) || 0)
    if (!inCycle && !conqueredN) {
      host.innerHTML = `
        <section class="fce-group">
          <div class="fce-group-title">📕 错题本</div>
          <p class="reading-hint">答错的单词会自动收进来，按艾宾浩斯记忆法安排复习（1、2、4、7、15、30 天）。错了不可怕，六轮复习全过它就变成你的词。</p>
        </section>`
      return
    }
    const practiceN = Math.min(20, dueNow)
    const drillN = Math.min(20, inCycle)
    host.innerHTML = `
      <section class="fce-group">
        <div class="fce-group-title">📕 错题本 · 艾宾浩斯复习（在记 ${inCycle} 词 · 今日到期 <b class="rc-due-n">${dueNow}</b>）</div>
        <p class="reading-hint">今天到期 ${due} · 逾期 ${over}${conqueredN ? ` · 已攻克 ${conqueredN}` : ''}。点下面直接开练，不再展开错词列表。</p>
        <div class="chip-row" style="margin-top:10px">
          ${dueNow ? `<button class="btn-primary" id="rc-wb-due">复习今日到期（${practiceN} 词）</button>` : ''}
          ${inCycle ? `<button class="btn-primary ghost" id="rc-wb-drill">全部错词轮一遍（${drillN} 词）</button>` : ''}
          ${conqueredN ? `<button class="chip" id="rc-wb-cq">✅ 已攻克题本（${conqueredN} 词）›</button>` : ''}
        </div>
      </section>`
    const cqBtn = host.querySelector('#rc-wb-cq')
    if (cqBtn) cqBtn.addEventListener('click', () => { location.hash = '/conquered' })
    const dueBtn = host.querySelector('#rc-wb-due')
    if (dueBtn) dueBtn.addEventListener('click', () => {
      startConquer([...(s.due_words || []), ...(s.overdue_words || [])])
    })
    const drill = host.querySelector('#rc-wb-drill')
    if (drill) drill.addEventListener('click', () => startConquer(s.conquer_pool || []))
  }

  el.querySelectorAll('.chip-row').forEach((row) => {
    row.addEventListener('click', (e) => {
      const chip = e.target.closest('.chip')
      if (!chip) return
      row.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c === chip))
      const d = chip.dataset
      if (d.scope) { state.scope = d.scope; state.scopeTouched = true }
      if (d.size) state.size = Number(d.size)
      if (d.scope) { renderScopeChips(); renderBoard() } // 切词库：chips 与看板随之切换
    })
  })

  el.querySelector('#rc-start').addEventListener('click', () => {
    // 出题池=当前层「云端未掌握」的词；逾期未清时新词被额度截断
    const uncapped = pool().filter((e) => !masteredSet.has((e.word || '').toLowerCase()))
    const p = freshPool()
    if (!p.length) {
      const $hint = el.querySelector('#rc-stats')
      const blocked = uncapped.length && reviewSummary && reviewSummary.new_cap != null
      if ($hint)
        $hint.innerHTML = blocked
          ? `<div class="card recite-stats-card">
              <h3>今日新词额度已用完</h3>
              <p class="muted">逾期还没清。先点上面的「逾期 / 到期」把复习做完，再背新词。</p>
            </div>`
          : `<div class="card recite-stats-card">
              <h3>当前词库已全部背完 🎉</h3>
              <p class="muted">可以换更高一级词库，或到下方错题本巩固易错词。</p>
            </div>`
      return
    }
    startSession(el, {
      pool: p,
      size: Math.min(state.size, p.length),
      mode: state.mode,
      scope: state.scope,
      onFinish: () => {
        refreshFromCloud()
        // 上报有网络延迟，稍等再拉历史
        setTimeout(renderHistory, 800)
      },
    })
  })

  try {
    reviewSummary = await api.reviewSummary()
  } catch {
    reviewSummary = null
  }
  renderReviewLoop()
  renderBoard()
  renderWrongbook()
  maybeAutoReview()
}

// ---- 全屏会话 -------------------------------------------------------------- //

function startSession(rootEl, { pool, size, mode, scope = '', onFinish }) {
  // 播放队列：初始题 + 错词重现（答错后间隔 3 题再插一次）
  let queue = buildQuiz(pool, size)
  let done = 0
  const wrongEntries = []
  const details = [] // 逐词对错明细（首答），随组上报云端逐词进度
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

  async function finish() {
    const total = done
    // 注意：wrongEntries 只记首答错；重现答对不影响它
    const uniqWrong = [...new Set(wrongEntries.map((e) => e.word))]
    const sec = Math.round((Date.now() - t0) / 1000)
    // 先上报再刷新摘要，新词额度才对得上这一组
    try {
      await uploadSession(total, uniqWrong, sec, details)
    } catch { /* 离线/未登录时静默丢弃 */ }
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

  function uploadSession(total, uniqWrong, sec, wordDetails) {
    return import('../api.js').then(({ api }) => api.reciteSubmit({
      total,
      wrong: uniqWrong.length,
      acc: Math.round(((total - uniqWrong.length) / total) * 100),
      duration_sec: sec,
      wrong_words: uniqWrong,
      mode,
      scope,
      details: wordDetails,
    }))
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
    stopAtlasAudio()
    const q = currentQ()
    cleanupKeys()
    if (!q) return finish()
    renderCard(overlay, {
      q,
      idx: done + 1,
      total: totalFirst + (queue.length - totalFirst),
      mode,
      onAnswer(correct) {
        // 首答逐词明细（重现答题不计）：云端累计 right/wrong
        const w = q.entry.word
        if (!details.some((d) => d.word === w)) details.push({ word: w, correct })
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
        // 中途退出：已作答部分照常上报（答对计云端进度、答错进艾宾浩斯
        // 周期、按已答题数记一组部分成绩），避免白背。
        if (done > 0) {
          const uniqWrong = [...new Set(wrongEntries.map((e) => e.word))]
          uploadSession(done, uniqWrong, Math.round((Date.now() - t0) / 1000), details)
          if (onFinish) onFinish()
        }
      },
    })
  }

  renderQ()
}

// ---- 单张卡片 -------------------------------------------------------------- //

function renderCard(overlay, { q, idx, total, mode, onAnswer, onQuit }) {
  stopAtlasAudio()
  const e = q.entry
  const posTags = entryPos(e).map((p) => `<span class="vocab-pos">${POS_CN[p]}</span>`).join('')
  const specials = (e.special_spellings || [])
    .map((s) => `<span class="rc-special">${escapeHtml(s)}</span>`)
    .join('')
  const example = (e.examples || [])[0] || (e.example && e.example.en ? e.example : null)
  const card = getWordCard('recite')
  // 英译中正面已经露出单词，插画和录音按钮可以一起放。
  // 中译英 / 挖空正面不放插画和音标，避免把答案画出来。
  // 题干释义是题目本身，不跟「词卡释义」开关走；插画/例句/词光释义才受开关控制。
  const promptHtml =
    q.type === 'en2zh'
      ? `${card.image ? atlasFigure(e.atlas) : ''}
         <div class="rc-word">${escapeHtml(e.display || e.word)}</div>
         <div class="rc-phonetic">${phoneticHtml(e)} ${posTags}</div>`
      : q.type === 'cloze'
        ? `<div class="rc-word rc-cloze">${escapeHtml(q.masked)}</div>
           <div class="rc-phonetic">${posTags} ${escapeHtml(q.meaning)}</div>`
        : `<div class="rc-meaning">${escapeHtml(q.meaning)}</div>
           <div class="rc-phonetic">${posTags}${e.phonetic ? ' 音标稍后揭晓' : ''}</div>`

  overlay.innerHTML = `
    <div class="rc-top">
      <button class="rc-quit" title="退出">✕ 退出</button>
      <div class="rc-progress">${idx} / ${total}</div>
      <button class="rc-tts" title="${ttsOn() ? '翻面自动发音中，点击静音' : '已静音，点击开启自动发音'}">${ttsOn() ? '🔊' : '🔇'}</button>
    </div>
    <div class="rc-card" data-state="front">
      <div class="rc-prompt">${promptHtml}</div>
      <div class="rc-answer-area"></div>
      <div class="rc-extra"></div>
    </div>
  `

  const $card = overlay.querySelector('.rc-card')
  const $area = overlay.querySelector('.rc-answer-area')
  const $extra = overlay.querySelector('.rc-extra')

  bindAtlas($card)
  overlay.querySelector('.rc-quit').addEventListener('click', onQuit)
  const $tts = overlay.querySelector('.rc-tts')
  $tts.addEventListener('click', () => {
    const on = !ttsOn()
    setTtsOn(on)
    $tts.textContent = on ? '🔊' : '🔇'
    $tts.title = on ? '翻面自动发音中，点击静音' : '已静音，点击开启自动发音'
    if (on && $card.dataset.state === 'back') {
      playPronunciation(wordAudioUrl(e.atlas, getAccent()), e.display || e.word)
    }
  })

  // 释义区：gloss 逗号长串拆成前 6 条分行（每行一条义项，可读）；
  // meanings 是语料例句中文，不混进释义。
  const glossLines = () => {
    const g = e.gloss || ''
    if (!g) return []
    return g.split(/[,，/]+/).map((s) => s.trim()).filter(Boolean).slice(0, 6)
  }

  const revealDetail = () => {
    const gl = glossLines()
    const forms = Object.entries(e.forms || {}).filter(([k]) => FORM_CN[k])
    const ex = example
    const backMedia = q.type === 'en2zh' || !card.image
      ? ''
      : `${atlasFigure(e.atlas)}${e.atlas ? `<div class="rc-phonetic">${phoneticHtml(e)}</div>` : ''}`
    const glossBlock = card.def && q.type === 'en2zh' && gl.length
      ? `<div class="rc-gloss">${gl.map((l) => `<p>${escapeHtml(l)}</p>`).join('')}</div>`
      : ''
    const exBlock = card.example && ex
      ? `<div class="rc-example">${escapeHtml(ex.en)}<br/><span class="muted">${escapeHtml(ex.zh || '')}</span></div>`
      : ''
    $extra.innerHTML = `
      <div class="rc-detail">
        ${backMedia}
        ${glossBlock}
        ${specials ? `<div class="rc-special-row">${specials}</div>` : ''}
        ${forms.length ? `<div class="rc-forms">${forms.map(
          ([k, v]) => `<span class="rc-form"><i>${FORM_CN[k]}</i>${escapeHtml(v)}</span>`,
        ).join('')}</div>` : ''}
        ${exBlock}
        <div class="atlas-host"></div>
      </div>
    `
    bindAtlas($extra)
    fillAtlasHost($extra.querySelector('.atlas-host'), e.word, e.atlas, {
      feature: 'recite', image: false,
    })
  }

  {
    // 统一翻面自评：正面看提示（释义/词/挖空），翻面看答案 + 自动发音 + 三键自评
    $card.dataset.state = 'front'
    $area.innerHTML = '<div class="rc-hint">点击卡片或按空格键翻面</div>'
    const flip = () => {
      if ($card.dataset.state !== 'front') return
      $card.dataset.state = 'back'
      revealDetail()
      // 翻面揭晓答案：自动 TTS 发音一次（🔇 可关）
      if (ttsOn()) playPronunciation(wordAudioUrl(e.atlas, getAccent()), e.display || e.word)
      $area.innerHTML = `
        ${q.type === 'en2zh'
          ? `<div class="rc-answer-gloss">${glossLines().slice(0, 3).map((l) => escapeHtml(l)).join('；') || '—'}</div>`
          : `<div class="rc-word">${escapeHtml(q.answer)}${q.type === 'cloze' ? `<span class="rc-cloze-key">${escapeHtml(q.masked)} → ${escapeHtml(q.answer)}</span>` : ''}</div>`}
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
  }
}

// ---- 结算页 ---------------------------------------------------------------- //

function mountResult(el, { total, uniqWrong, acc, duration, pool, size, mode, scope = '', onFinish }) {
  const wrap = document.createElement('div')
  wrap.className = 'recite-result card'
  wrap.innerHTML = `
    <h2>本组完成 🎉</h2>
    <p>共 ${total} 题 · 首答正确率 <b>${acc}%</b> · 用时 ${fmtDuration(duration)}</p>
    ${uniqWrong.length ? `<h3>错词（${uniqWrong.length}）· 点击发音</h3><p>${uniqWrong.map((w) => `<span class="vocab-form rc-say" data-say="${escapeHtml(w)}">${escapeHtml(w)}</span>`).join(' ')}</p>` : '<p>全对，太棒了！</p>'}
    <div class="chip-row">
      <button class="btn-primary" id="rr-again">再背一组</button>
      ${uniqWrong.length ? '<button class="btn-primary ghost" id="rr-wrong">只练错词</button>' : ''}
    </div>
  `
  el.appendChild(wrap)
  wrap.querySelectorAll('.rc-say').forEach((s) =>
    s.addEventListener('click', () => { if (ttsOn()) speakWord(s.dataset.say) }),
  )
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

function scopeCn(scope) {
  return { all: '基础词表', 0: '基础词表', advanced: '进阶词表', verb: '只动词', special: '特殊拼写', wrongbook: '错题本' }[scope] || (String(scope).match(/^\d+$/) ? 'L' + scope + ' 词库' : scope) || '基础词表'
}

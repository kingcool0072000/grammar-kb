import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { speak } from '../tts.js'

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
const WB_COLLAPSE_N = 60 // 错题本超过此数折叠，点「展开全部」看所有词
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
    </div>
    <div class="card recite-dash" id="rc-dash"></div>
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
    const cur = state.scope === 'all' ? 0 : Number(state.scope)
    row.innerHTML = [0, 1, 2, 3, 4, 5]
      .map((lv) => {
        const n = levelCounts[String(lv)] || 0
        const locked = lv > unlocked
        const label = lv === 0 ? 'L0 课本' : `L${lv}`
        return `<button class="chip ${lv === cur ? 'active' : ''} ${locked ? 'locked' : ''}"
          data-scope="${lv === 0 ? 'all' : lv}" ${locked ? 'disabled title="通过上一级的词汇考试（80 分）后解锁"' : ''}>${locked ? '🔒 ' : ''}${label} · ${n} 词</button>`
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

  /** 出题池：当前层中「云端未掌握」的词（新词优先；已掌握的不再进组）。 */
  function freshPool() {
    return pool().filter((e) => !masteredSet.has(e.word.toLowerCase()))
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
    renderBoard()
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
    // 易错词频次（最近 20 组）
    const freq = new Map()
    for (const s of recs.slice(0, 20)) {
      for (const w of s.wrong_words || []) freq.set(w, (freq.get(w) || 0) + 1)
    }
    const hardWords = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
    $h.innerHTML = `
      <section class="fce-group">
        <div class="fce-group-title">📝 我的练习记录（${recs.length} 组 · 平均正确率 ${avgAcc}% · 累计 ${totalWords} 词次${totalMin ? ` / ${totalMin} 分钟` : ''}）</div>
        ${recs.slice(0, 8).map((s) => `
          <div class="fce-his-row">
            <span class="fce-his-what">${scopeCn(s.scope)}${s.mode === 'flip' ? '（自评）' : ''}</span>
            <b class="fce-his-score ${s.acc >= 80 ? 'ok' : ''}">${s.acc}%</b>
            <span class="fce-his-date">${s.total} 词 · 错 ${s.wrong} · ${(s.created_at || '').slice(5, 10)}</span>
          </div>`).join('')}
        ${hardWords.length ? `<p class="reading-hint">易错词（最近 20 组）：${hardWords.map(([w, n]) => `${escapeHtml(w)}×${n}`).join('、')}</p>` : ''}
      </section>`
  }
  renderHistory()

  // ---- 错题本（艾宾浩斯周期：在周期词 + 已攻克 + 复习日历） ----
  const renderWrongbook = async () => {
    const host = el.querySelector('#rc-wrongbook')
    if (!host) return
    let data
    try {
      data = await api.wrongbook()
    } catch {
      host.innerHTML = ''
      return
    }
    const inCycle = (data && data.in_cycle) || []
    const dueToday = (data && data.due_today) || []
    const conquered = (data && data.conquered) || []
    const stats = (data && data.stats) || {}
    if (!inCycle.length && !conquered.length) {
      host.innerHTML = `
        <section class="fce-group">
          <div class="fce-group-title">📕 错题本</div>
          <p class="reading-hint">答错的单词会自动收进来，按艾宾浩斯记忆法安排复习（1、2、4、7、15、30 天）。错了不可怕，六轮复习全过它就变成你的词。</p>
        </section>`
      return
    }
    const stages = stats.stages || [1, 2, 4, 7, 15, 30]
    const stageDist = stats.stage_dist || []
    const today = new Date()
    const dueByDay = new Map()
    for (const w of inCycle) {
      if (w.next_review) dueByDay.set(w.next_review, (dueByDay.get(w.next_review) || 0) + 1)
    }
    // 未来 14 天到期分布（含今天；过去顺延的也计入今天口径由后端 due 标记）
    const horizon = [...Array(14)].map((_, i) => {
      const d = new Date(today)
      d.setDate(d.getDate() + i)
      return d.toISOString().slice(0, 10)
    })
    const fmtDay = (iso) => {
      const d = new Date(iso + 'T00:00:00')
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
      return `${Number(iso.slice(8, 10))}日·周${wk}`
    }
    const todayIso = today.toISOString().slice(0, 10)
    let overdue = 0
    for (const w of inCycle) {
      if (w.next_review && w.next_review < todayIso) overdue++
    }
    // 14 天日历之外的排期（>13 天后）合并展示
    let beyond = 0
    for (const [iso, n] of dueByDay) {
      if (!horizon.includes(iso) && iso >= todayIso) beyond += n
    }
    const findEntry = (w) =>
      levelVocab.find((x) => x.word === w) ||
      vocab.find((x) => x.word === w) || {
        word: w, pos: [], meanings: [], gloss: '', level: -1,
        // 最小可出题兜底：认词题至少有词本身
        example: {}, extra: {},
      }
    // 优先后端聚合的 gloss/meanings（vocab_word 表，不受解锁层截断影响）
    const itemOf = (w) => inCycle.find((x) => x.word === w) || conquered.find((x) => x.word === w) || {}
    const glossOf = (w) => {
      const item = itemOf(w)
      if (item && (item.gloss || (item.meanings || []).length)) {
        return item.meanings?.length ? item.meanings : [item.gloss].filter(Boolean)
      }
      const e = findEntry(w)
      const m = e.meanings || []
      if (m.length) return m
      return e.gloss ? [e.gloss] : []
    }
    const stageLabel = (s) => `D+${stages[Math.min(s, stages.length - 1)]}`
    const chipHtml = (w, kind) => {
      const e = findEntry(w.word)
      const tip = kind === 'done'
        ? `${(w.conquered_at || '').slice(0, 10)} 攻克`
        : `${stageLabel(w.stage || 0)} 轮 · 下次 ${w.next_review || '—'}${w.due ? '（已到期）' : ''}`
      return `<span class="rc-wb-word ${kind} ${w.due ? 'due' : ''}" data-word="${escapeHtml(w.word)}" title="${escapeHtml(tip)}｜${e && e.gloss ? escapeHtml(e.gloss.slice(0, 40)) : ''}">${escapeHtml(w.word)}<sup>${w.wrong_count || 1}</sup></span>`
    }
    host.innerHTML = `
      <section class="fce-group">
        <div class="fce-group-title">📕 错题本 · 艾宾浩斯复习（在记 ${inCycle.length} 词 · 今日到期 <b class="rc-due-n">${dueToday.length + overdue}</b> · 已攻克 ${conquered.length}）</div>
        <div class="rc-eb-stages">
          ${stages.map((d, i) => `<span class="rc-eb-stage ${stageDist[i] ? 'has' : ''}"><i>D+${d}</i><b>${stageDist[i] || 0}</b></span>`).join('')}
        </div>
        <div class="rc-eb-cal">
          ${horizon.map((iso) => {
            const n = iso === todayIso ? dueToday.length + overdue : (dueByDay.get(iso) || 0)
            return `<div class="rc-eb-day ${n ? 'has' : ''} ${iso === todayIso ? 'today' : ''}">${n ? `<b>${n}</b>` : ''}<span>${fmtDay(iso)}</span></div>`
          }).join('')}
          ${beyond ? `<div class="rc-eb-day more" title="14 天以后的复习">${beyond}+<span>更远</span></div>` : ''}
        </div>
        <div class="chip-row" style="margin-top:10px">
          ${dueToday.length + overdue ? `<button class="btn-primary" id="rc-wb-due">复习今日到期（${Math.min(20, dueToday.length + overdue)} 词）</button>` : ''}
          ${inCycle.length ? `<button class="btn-primary ghost" id="rc-wb-drill">全部错词轮一遍（${Math.min(20, inCycle.length)} 词）</button>` : ''}
        </div>
        <div class="rc-wb-words">
          ${inCycle.slice(0, WB_COLLAPSE_N).map((w) => chipHtml(w, 'in')).join('')}
          ${inCycle.length > WB_COLLAPSE_N ? `
            <button class="reading-btn small" id="rc-wb-toggle">展开全部（${inCycle.length} 词）⌄</button>
            <div class="rc-wb-rest" hidden>
              ${inCycle.slice(WB_COLLAPSE_N).map((w) => chipHtml(w, 'in')).join('')}
            </div>` : ''}
        </div>
      </section>
      ${conquered.length ? `
      <section class="fce-group" id="rc-wb-cq">
        <div class="fce-group-title">✅ 已攻克（${conquered.length} 词）· 六轮复习全过</div>
        <div class="rc-wb-words">
          ${conquered.slice(0, WB_COLLAPSE_N).map((w) => chipHtml(w, 'done')).join('')}
          ${conquered.length > WB_COLLAPSE_N ? `
            <button class="reading-btn small" id="rc-wb-toggle2">展开全部（${conquered.length} 词）⌄</button>
            <div class="rc-wb-rest" hidden>
              ${conquered.slice(WB_COLLAPSE_N).map((w) => chipHtml(w, 'done')).join('')}
            </div>` : ''}
        </div>
      </section>` : ''}`
    const bindToggle = (btnId, sel) => {
      const toggle = host.querySelector(btnId)
      if (!toggle) return
      toggle.addEventListener('click', () => {
        const rest = host.querySelector(sel)
        const open = !rest.hidden
        rest.hidden = open
        toggle.textContent = open ? `展开全部⌄` : '收起 ⌃'
      })
    }
    bindToggle('#rc-wb-toggle', '.rc-wb-rest:not(#rc-wb-cq .rc-wb-rest)')
    bindToggle('#rc-wb-toggle2', '#rc-wb-cq .rc-wb-rest')
    // 点击错词：行内展开中文释义 + 发音（无释义时查 /dict 兜底）
    host.querySelectorAll('.rc-wb-word').forEach((chip) => {
      chip.addEventListener('click', async () => {
        const w = chip.dataset.word
        if (ttsOn()) speak(w)
        const show = (lines, note) => {
          host.querySelectorAll('.rc-wb-detail').forEach((b) => b.remove())
          const box = document.createElement('div')
          box.className = 'rc-wb-detail'
          box.innerHTML = `
            <b>${escapeHtml(w)}</b>
            ${lines.length ? `<p>${lines.map((l) => escapeHtml(String(l))).join('<br/>')}</p>` : ''}
            ${note ? `<span class="muted">${escapeHtml(note)}</span>` : ''}`
          chip.after(box)
        }
        let lines = glossOf(w)
        if (lines.length) return show(lines)
        show([], '查询释义…')
        try {
          const d = await api.dict(w)
          const gl = (d && d.gloss_lines) || []
          const got = gl.length ? gl.map((g) => [g.pos, g.text].filter(Boolean).join('. ')) : (d && d.gloss ? [d.gloss] : [])
          show(got, got.length ? '' : '词典未收录该词')
        } catch {
          show([], '词典未收录该词')
        }
      })
    })
    const startReview = (list, label) => {
      const entries = shuffle(list.map((w) => findEntry(w.word))).slice(0, 20).map((e) => ({
        ...e,
        display: '',
        examples: e.example && e.example.en ? [e.example] : (e.examples || []),
      }))
      if (!entries.length) return
      startSession(el, {
        pool: entries,
        size: entries.length,
        mode: 'flip',
        scope: 'wrongbook', // 复习会话：答对推进艾宾浩斯节点
        onFinish: () => {
          refreshFromCloud()
          setTimeout(renderWrongbook, 800)
        },
      })
    }
    const dueBtn = host.querySelector('#rc-wb-due')
    if (dueBtn) {
      dueBtn.addEventListener('click', () => {
        const overdueSet = new Set(
          inCycle.filter((w) => w.next_review && w.next_review < todayIso).map((w) => w.word),
        )
        const list = [
          ...inCycle.filter((w) => w.due && !overdueSet.has(w.word)),
          ...inCycle.filter((w) => overdueSet.has(w.word)),
        ]
        startReview(list, '到期')
      })
    }
    const drill = host.querySelector('#rc-wb-drill')
    if (drill) drill.addEventListener('click', () => startReview(inCycle, '全部'))
  }
  renderWrongbook()

  el.querySelectorAll('.chip-row').forEach((row) => {
    row.addEventListener('click', (e) => {
      const chip = e.target.closest('.chip')
      if (!chip) return
      row.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c === chip))
      const d = chip.dataset
      if (d.scope) state.scope = d.scope
      if (d.size) state.size = Number(d.size)
      if (d.scope) { renderScopeChips(); renderBoard() } // 切词库：chips 与看板随之切换
    })
  })

  el.querySelector('#rc-start').addEventListener('click', () => {
    // 出题池=当前层「云端未掌握」的词；全部掌握时提示去错题本/换层级
    const p = freshPool()
    if (!p.length) {
      const $hint = el.querySelector('#rc-stats')
      if ($hint)
        $hint.innerHTML = `
          <div class="card recite-stats-card">
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

  renderBoard()
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

  function finish() {
    const total = done
    // 注意：wrongEntries 只记首答错；重现答对不影响它
    const uniqWrong = [...new Set(wrongEntries.map((e) => e.word))]
    const sec = Math.round((Date.now() - t0) / 1000)
    // 成绩 + 逐词明细一并静默上报云端（尽力而为：失败不影响本地流程）
    uploadSession(total, uniqWrong, sec, details)
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
    import('../api.js').then(({ api }) => {
      return api.reciteSubmit({
        total,
        wrong: uniqWrong.length,
        acc: Math.round(((total - uniqWrong.length) / total) * 100),
        duration_sec: sec,
        wrong_words: uniqWrong,
        mode,
        scope,
        details: wordDetails,
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
  const e = q.entry
  const posTags = entryPos(e).map((p) => `<span class="vocab-pos">${POS_CN[p]}</span>`).join('')
  const specials = (e.special_spellings || [])
    .map((s) => `<span class="rc-special">${escapeHtml(s)}</span>`)
    .join('')
  const example = (e.examples || [])[0] || (e.example && e.example.en ? e.example : null)

  const promptHtml =
    q.type === 'en2zh'
      ? `<div class="rc-word">${escapeHtml(e.display || e.word)}</div>
         <div class="rc-phonetic">${escapeHtml(e.phonetic || '')} ${posTags}</div>`
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

  overlay.querySelector('.rc-quit').addEventListener('click', onQuit)
  const $tts = overlay.querySelector('.rc-tts')
  $tts.addEventListener('click', () => {
    const on = !ttsOn()
    setTtsOn(on)
    $tts.textContent = on ? '🔊' : '🔇'
    $tts.title = on ? '翻面自动发音中，点击静音' : '已静音，点击开启自动发音'
    if (on && $card.dataset.state === 'back') speak(e.display || e.word) // 开启后立即补一次
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
    $extra.innerHTML = `
      <div class="rc-detail">
        ${q.type !== 'en2zh' ? '' : gl.length ? `<div class="rc-gloss">${gl.map((l) => `<p>${escapeHtml(l)}</p>`).join('')}</div>` : ''}
        ${specials ? `<div class="rc-special-row">${specials}</div>` : ''}
        ${forms.length ? `<div class="rc-forms">${forms.map(
          ([k, v]) => `<span class="rc-form"><i>${FORM_CN[k]}</i>${escapeHtml(v)}</span>`,
        ).join('')}</div>` : ''}
        ${ex ? `<div class="rc-example">${escapeHtml(ex.en)}<br/><span class="muted">${escapeHtml(ex.zh || '')}</span></div>` : ''}
      </div>
    `
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
      if (ttsOn()) speak(e.display || e.word)
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
    s.addEventListener('click', () => { if (ttsOn()) speak(s.dataset.say) }),
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

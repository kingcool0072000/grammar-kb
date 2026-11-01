import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { fmtDuration } from '../fmt.js'
import { rememberAtlas } from '../atlas.js'
import { migrateLegacyProgress, shuffle, startSession } from './recite/engine.js'

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
//
// 结构：本文件是页面壳——页面骨架、词库 scope 选择、复习环摘要、
// 看板/历史/错题本渲染、进度拉取与页面级事件编排。会话状态机
// （出题队列/逐题推进/结算上传）见 ./recite/engine.js，翻面卡片见
// ./recite/cards.js，共用开关见 ./recite/shared.js。

const GROUP_SIZES = [20, 30]

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

function scopeCn(scope) {
  return { all: '基础词表', 0: '基础词表', advanced: '进阶词表', verb: '只动词', special: '特殊拼写', wrongbook: '错题本' }[scope] || (String(scope).match(/^\d+$/) ? 'L' + scope + ' 词库' : scope) || '基础词表'
}

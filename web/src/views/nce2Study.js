import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { fmtDur as fmtSec, fmtDurCn as fmtDur } from '../fmt.js'
import { createLookupLayer } from './library/lookup.js'
import { createFocusTracker } from './library/focus.js'

// NCE2 听力跟读（学生·任务制）：清单按 本周安排 / 待批改 / 已完成 分组；
// 详情页 = mp3 播放器 + 双语逐句高亮（timeupdate 驱动、点句 seek）+
// 划词/双击查词（复用泛读馆 lookup 层）+ 专注力采集（module='nce2'）+
// 底部动作区（今日计划=精听 → 「我听完了」打卡；跟读/自由练习 → 整课
// 录音提交，≤300 秒，静音拦截）。教师进入为只读预览。
const MAX_RECORD_SEC = 300
// 「隐藏中文」自测开关：localStorage 记住，详情页之间共享
const HIDE_ZH_KEY = 'gkb-nce2-hide-zh'

function loadHideZh() {
  try { return localStorage.getItem(HIDE_ZH_KEY) === '1' } catch { return false }
}
function saveHideZh(v) {
  try { localStorage.setItem(HIDE_ZH_KEY, v ? '1' : '0') } catch { /* ignore */ }
}

// ---- 课状态归类（后端契约：lessons 行自带 week/planned/listen_today/
// listen_done_ever/shadow 状态字段；教师全量清单无状态字段） ----
const isPending = (l) => !!(l.shadow && l.shadow.status === 'pending')
const isGraded = (l) => !!(l.shadow && l.shadow.status === 'graded')
const isDone = (l) => !!l.listen_done_ever || isGraded(l)

export async function mountNce2Study(el, { role } = {}) {
  const auth = getAuth()
  const myRole = role || (auth && auth.role) || 'student'
  el.innerHTML = '<div class="view-head"><h1>听力跟读</h1><p>加载中…</p></div>'
  let lessons
  try {
    const data = await api.nce2Lessons()
    lessons = data && data.lessons ? data.lessons : (Array.isArray(data) ? data : [])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  renderNce2List(el, lessons, myRole)
  // 课程表/计划卡直达：#/nce2?lid=ID（与 videoStudy 的 ?vid= 同模式）
  const m = location.hash.match(/[?&]lid=(\d+)/)
  if (m && lessons.some((l) => l.id === Number(m[1])))
    renderNce2Detail(el, Number(m[1]), myRole)
}

// ---------- 列表（学生：任务制分组；教师：全量单列只读） ----------
function renderNce2List(el, lessons, role) {
  if (!lessons.length) {
    el.innerHTML = `
      <div class="view-head"><h1>听力跟读</h1></div>
      <p style="color:var(--ink-soft)">还没有可学的课程。老师把这周的听力排进计划后，这里就会出现。</p>`
    return
  }
  const byId = new Map(lessons.map((l) => [l.id, l]))
  const card = (l) => {
    const badge = isGraded(l)
      ? `<b class="fce-his-score ${l.shadow.teacher_score >= 7 ? 'ok' : ''}">${l.shadow.teacher_score}/10${l.shadow.teacher_score >= 7 ? ' 过关' : ''}</b>`
      : isPending(l)
        ? '<b class="fce-his-score pend">待批改</b>'
        : l.listen_done_ever ? '<b class="fce-his-score ok">✅ 已精听</b>' : ''
    return `
      <div class="reading-card nce2-card ${l.week ? 'rd-week' : ''}" data-lid="${l.id}" role="button">
        <span class="reading-card-title">🎧 Lesson ${l.id} · ${escapeHtml(l.title || '')}</span>
        <span class="reading-card-meta">
          ${l.week ? `<b class="rd-week-tag">📌 本周任务${l.nce2_mode === 'listen' ? ' · 精听' : ' · 跟读'}</b>` : ''}
          <span class="tag" style="--cat-color:#4a6fa5">${l.n_sentences ?? '—'} 句 · ${l.n_words ?? '—'} 词</span>
          ${l.has_audio === false ? '<span class="tag" style="--cat-color:#b42318">音频未就位</span>' : ''}
          ${badge}
        </span>
      </div>`
  }
  const section = (title, list, fold = false) => {
    if (!list.length) return ''
    const inner = `<div class="reading-art-list">${list.map(card).join('')}</div>`
    return fold
      ? `<details class="rd-done-fold"><summary>✅ 已完成（${list.length} 课）<i>点开可回听录音、看评语、再读一遍</i></summary>${inner}</details>`
      : `<section class="fce-group"><div class="fce-group-title">${title}</div>${inner}</section>`
  }
  let bodyHtml
  if (role === 'teacher') {
    bodyHtml = section(`🎧 NCE2 全 96 课（教师预览 · 只读）`,
      lessons.slice().sort((a, b) => a.id - b.id))
  } else {
    const weekAll = lessons.filter((l) => l.week)
    const week = weekAll.filter((l) => !isDone(l) && !isPending(l))
    const pend = lessons.filter(isPending)
    const done = lessons.filter(isDone)
    const extra = lessons.filter((l) => !l.week && !isDone(l) && !isPending(l))
    bodyHtml = `
      ${section(`📌 本周安排 · 已完成 ${weekAll.filter((l) => isDone(l) || isPending(l)).length}/${weekAll.length}`, week)}
      ${pend.length ? section('🎙 待批改（跟读录音等老师批）', pend) : ''}
      ${section('', done, true)}
      ${extra.length ? `
      <details class="rd-done-fold">
        <summary>📎 计划外可练（${extra.length}）</summary>
        <div class="reading-art-list">${extra.map(card).join('')}</div>
      </details>` : ''}
    `
  }
  el.innerHTML = `
    <div class="view-head">
      <h1>听力跟读</h1>
      <p>${role === 'teacher'
        ? `教师预览：新概念二全部 ${lessons.length} 课（学生视角含本周安排/批改状态）。`
        : '听一遍 → 逐句对照 → 精听打卡或跟读录音，老师批改后在这里看分。'}</p>
    </div>
    ${bodyHtml}
  `
  el.querySelectorAll('.nce2-card').forEach((c) =>
    c.addEventListener('click', () => renderNce2Detail(el, Number(c.dataset.lid), role)))
}

// ---------- 详情 ----------
async function renderNce2Detail(el, lid, role) {
  el.innerHTML = '<div class="view-head"><h1>听力跟读</h1><p>加载中…</p></div>'
  let l, subs
  try {
    ;[l, subs] = await Promise.all([
      api.nce2Lessons({ withSentences: true }).then((d) => {
        const list = d && d.lessons ? d.lessons : (Array.isArray(d) ? d : [])
        return list.find((x) => x.id === lid) || null
      }),
      role === 'teacher' ? Promise.resolve([]) : api.nce2Queue('all').catch(() => []),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  if (!l) {
    el.innerHTML = `
      <div class="view-head"><button class="fce-back-btn" id="nce2-back">← 返回列表</button><h1>课程不存在</h1></div>`
    el.querySelector('#nce2-back').addEventListener('click', () => mountNce2Study(el, { role }))
    return
  }
  const sents = Array.isArray(l.sentences) ? l.sentences : []
  // 提交兜底：lessons 行的 shadow 万一缺分数明细，从自己的提交列表里找
  if (!isGraded(l) && !isPending(l)) {
    const mine = subs.filter((s) => s.lesson_id === lid && s.kind === 'shadow')
      .sort((a, b) => ((a.created_at || '') < (b.created_at || '') ? 1 : -1))
    if (mine.length) l.shadow = { status: mine[0].status, teacher_score: mine[0].teacher_score, teacher_comment: mine[0].teacher_comment }
  }
  // 状态做成首屏可见的醒目徽标（评分/完成态原来只是副标题里的灰字，
  // 沉在两屏句子列表下方的状态卡孩子第一眼看不到——评审 P1）
  const stateBadge = isGraded(l)
    ? `<b class="fce-his-score ${l.shadow.teacher_score >= 7 ? 'ok' : ''}">跟读 ${l.shadow.teacher_score}/10${l.shadow.teacher_score >= 7 ? ' · 过关' : ''}</b>`
    : isPending(l) ? '<b class="fce-his-score pend">🎙 跟读待批改</b>'
      : l.listen_today ? '<b class="fce-his-score ok">✅ 今日精听已完成</b>'
        : l.listen_done_ever ? '<b class="fce-his-score ok">精听过</b>' : ''
  const hideZh = loadHideZh()
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="nce2-back">← 返回列表</button>
      <h1>🎧 Lesson ${l.id} · ${escapeHtml(l.title || '')}</h1>
      <p>${l.n_sentences ?? '—'} 句 · ${l.n_words ?? '—'} 词${stateBadge ? ` ${stateBadge}` : ''}${role === 'teacher' ? ' · 教师只读预览' : ''}</p>
    </div>
    <div class="nce2-player-wrap">
      <audio controls preload="metadata" class="nce2-audio" id="nce2-audio"></audio>
      <div class="nce2-player-bar">
        <button class="reading-btn small" id="nce2-rew" title="后退 5 秒重听这句">↺ −5s</button>
        <button class="reading-btn small" id="nce2-fwd" title="前进 5 秒">↻ +5s</button>
        <button class="reading-btn small ${hideZh ? 'primary' : ''}" id="nce2-hidezh" title="隐藏中文：只看英文自测听懂没有">中文：${hideZh ? '隐藏中' : '显示'}</button>
        <span class="reading-pick-info">点句子跳到那句；拖选/双击英文可查词</span>
      </div>
    </div>
    <ol class="nce2-sents ${hideZh ? 'hide-zh' : ''}" id="nce2-sents">
      ${sents.length ? sents.map((s, i) => `
        <li class="nce2-sent" data-t="${Number(s.t) || 0}">
          <span class="nce2-sent-idx">${i + 1}</span>
          <div class="nce2-sent-body">
            <p class="nce2-en">${escapeHtml(s.en || '')}</p>
            ${s.zh ? `<p class="nce2-zh">${escapeHtml(s.zh)}</p>` : ''}
          </div>
        </li>`).join('') : '<p class="reading-hint">本课暂无逐句字幕。</p>'}
    </ol>
    <div id="nce2-action"></div>
  `
  el.querySelector('#nce2-back').addEventListener('click', () => mountNce2Study(el, { role }))

  // ---- 播放器：逐句高亮 + 自动滚动 + 点句 seek + ±5s ----
  const audio = el.querySelector('#nce2-audio')
  const listEl = el.querySelector('#nce2-sents')
  const rows = [...listEl.querySelectorAll('.nce2-sent')]
  const token = getAuth()?.token || ''
  audio.src = `/api/nce2/lessons/${lid}/audio?token=${encodeURIComponent(token)}`
  let curIdx = -1
  const syncHighlight = () => {
    if (!sents.length || !rows.length) return
    const t = audio.currentTime || 0
    let idx = -1
    for (let i = 0; i < sents.length; i++) {
      if ((Number(sents[i].t) || 0) <= t + 0.01) idx = i
      else break
    }
    if (idx === curIdx) return
    curIdx = idx
    rows.forEach((r, i) => r.classList.toggle('current', i === idx))
    if (idx >= 0 && !audio.paused) rows[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
  audio.addEventListener('timeupdate', () => {
    syncHighlight()
    const d = audio.duration || 0
    const pct = d ? Math.min(100, (audio.currentTime / d) * 100) : 0
    focusTracker.hooks.onReloc(pct)
  })
  listEl.addEventListener('click', (e) => {
    const li = e.target.closest('.nce2-sent')
    if (!li || !audio) return
    // 查词浮层里的点击不算 seek（lookup 层已 stopPropagation，这里再兜一层）
    if (e.target.closest('.lib-selpop,.lib-dictpop')) return
    audio.currentTime = Number(li.dataset.t) || 0
  })
  el.querySelector('#nce2-rew').addEventListener('click', () => {
    audio.currentTime = Math.max(0, (audio.currentTime || 0) - 5)
  })
  el.querySelector('#nce2-fwd').addEventListener('click', () => {
    audio.currentTime = Math.min(audio.duration || 0, (audio.currentTime || 0) + 5)
  })
  const hideBtn = el.querySelector('#nce2-hidezh')
  hideBtn.addEventListener('click', () => {
    const next = !listEl.classList.contains('hide-zh')
    listEl.classList.toggle('hide-zh', next)
    hideBtn.classList.toggle('primary', next)
    hideBtn.textContent = `中文：${next ? '隐藏中' : '显示'}`
    saveHideZh(next)
  })

  // ---- 专注力采集（module='nce2'，范围=整课 0→100）+ 划词查词 ----
  const rangeLabel = `NCE2 L${l.id} ${l.title || ''}(${l.n_words ?? '?'}词)`.slice(0, 120)
  const focusTracker = createFocusTracker({
    readerRoot: el, viewerEl: listEl, bookId: lid,
    bookTitle: `Lesson ${l.id} ${l.title || ''}`.slice(0, 200),
    enabled: role !== 'teacher', module: 'nce2',
    initialRangeLabel: rangeLabel,
  })
  focusTracker.hooks.onReloc(0, rangeLabel) // 进入详情即落位 0（无书内章节概念）
  const lookup = createLookupLayer(listEl, {
    feature: 'nce2',
    onLookup: focusTracker.hooks.onLookup,
    onSpeak: focusTracker.hooks.onSpeak,
    onSelect: focusTracker.hooks.onSelect,
    directLookupOnDouble: true, // 双击单词：直接查词+发音一次
  })
  setupSentSelectionLookup(listEl, lookup, () => focusTracker.destroy())
  // 教师开关：student.focus=false 时静默停采（与泛读馆/精读同一开关）
  if (role !== 'teacher') {
    api.librarySettings().then((cfg) => {
      if (cfg && cfg.student && cfg.student.focus === false) focusTracker.disable()
    }).catch(() => {})
  }

  // ---- 底部动作区：精听打卡 / 跟读录音 / 批改结果 ----
  renderActionArea(el, l, role, focusTracker)
}

/**
 * 动作区决策（后端契约字段）：
 *  - 有跟读提交 → 状态卡（待批改 / 分数+评语，≥7 过关），均可再录刷新
 *  - listen_today=true → 今日精听已完成态（附自愿跟读录音器）
 *  - planned 且 nce2_mode='listen'（本周精听行且未打卡）→「✓ 我听完了」
 *  - 其余（shadow 任务行 / 无计划行=自由练习）→ 跟读录音器（文案区分）
 * 提交/重录后只重绘动作区，不打断播放。
 */
function renderActionArea(el, l, role, focusTracker) {
  const host = el.querySelector('#nce2-action')
  if (!host) return
  if (role === 'teacher') {
    host.innerHTML = '<p class="reading-hint">教师账号打开为预览：打卡与录音提交请用学生账号。</p>'
    return
  }
  const sh = l.shadow || null
  if (sh) {
    const graded = sh.status === 'graded'
    const score = graded ? Number(sh.teacher_score) : null
    host.innerHTML = `
      <div class="nce2-action-card ${graded && score >= 7 ? 'pass' : ''}">
        ${graded
          ? `<b class="fce-his-score ${score >= 7 ? 'ok' : ''}">${score}/10</b>
             ${score >= 7 ? '<i class="nce2-pass-tag">过关</i>' : ''}
             ${sh.teacher_comment ? `<div class="reading-graded-comment">💬 ${escapeHtml(sh.teacher_comment)}</div>` : ''}`
          : '<b class="fce-his-score pend">🎙 跟读已提交 · 待批改</b>'}
      </div>
      <div class="reading-toolbar" id="nce2-toolbar">
        <div class="reading-tool-row">
          <span class="reading-tool-label">🎙 ${graded ? '再录一次（刷新成绩）' : '再录一遍'}</span>
          <span class="reading-pick-info">整课跟读一遍，≤ ${Math.round(MAX_RECORD_SEC / 60)} 分钟</span>
        </div>
        <div class="reading-tool-row">
          <button class="reading-btn primary" id="nce2-record">开始录音</button>
          <span class="reading-pick-info" id="nce2-rectime"></span>
          <span class="reading-level-wrap" title="录音音量：说话时绿色条应跳动；不动=麦克风没声音">
            <span class="reading-level-bar"><i id="nce2-level"></i></span>
          </span>
          <span class="reading-pick-info" id="nce2-recmsg"></span>
        </div>
      </div>
    `
    setupShadowRecorder(el, host, l, focusTracker)
    return
  }
  if (l.listen_today) {
    host.innerHTML = `
      <div class="nce2-action-card pass">
        <b class="fce-his-score ok">✅ 今日精听已完成</b>
        <span class="reading-pick-info">想加深印象？随时可以再放一遍、跟读模仿。</span>
      </div>
      <div class="reading-toolbar" id="nce2-toolbar">
        <div class="reading-tool-row">
          <span class="reading-tool-label">🎙 自愿跟读（不加任务）</span>
          <span class="reading-pick-info">整课跟读一遍，≤ ${Math.round(MAX_RECORD_SEC / 60)} 分钟，老师会批改</span>
        </div>
        <div class="reading-tool-row">
          <button class="reading-btn primary" id="nce2-record">开始录音</button>
          <span class="reading-pick-info" id="nce2-rectime"></span>
          <span class="reading-level-wrap" title="录音音量：说话时绿色条应跳动；不动=麦克风没声音">
            <span class="reading-level-bar"><i id="nce2-level"></i></span>
          </span>
          <span class="reading-pick-info" id="nce2-recmsg"></span>
        </div>
      </div>
    `
    setupShadowRecorder(el, host, l, focusTracker)
    return
  }
  if (l.planned && l.nce2_mode === 'listen') {
    host.innerHTML = `
      <div class="nce2-action-card">
        <span class="reading-pick-info">听懂这课了吗？听完后点下面的按钮打卡。</span>
        <button class="reading-btn primary" id="nce2-listen-done">✓ 我听完了</button>
        <span class="reading-pick-info" id="nce2-listen-msg"></span>
      </div>
    `
    host.querySelector('#nce2-listen-done').addEventListener('click', async (e) => {
      const btn = e.currentTarget
      const msg = host.querySelector('#nce2-listen-msg')
      btn.disabled = true
      try {
        await api.nce2Submit({ lesson_id: l.id, kind: 'listen' })
        l.listen_today = true
        l.listen_done_ever = true
        renderActionArea(el, l, role, focusTracker)
      } catch (err) {
        if (msg) msg.textContent = `打卡失败：${err.message}`
        btn.disabled = false
      }
    })
    return
  }
  // 跟读：本周 shadow 任务行或自由练习，都给录音器（文案区分任务/自愿）
  const isTask = !!l.planned
  host.innerHTML = `
    <div class="reading-toolbar" id="nce2-toolbar">
      <div class="reading-tool-row">
        <span class="reading-tool-label">🎙 ${isTask ? '本周跟读任务' : '自由跟读'}</span>
        <span class="reading-pick-info">${isTask
          ? `这课在本周计划里：整课跟读一遍（≤ ${Math.round(MAX_RECORD_SEC / 60)} 分钟），提交后老师批改`
          : `这课不在今日计划里，可自愿跟读整课一遍（≤ ${Math.round(MAX_RECORD_SEC / 60)} 分钟），提交后老师批改`}</span>
      </div>
      <div class="reading-tool-row">
        <button class="reading-btn primary" id="nce2-record">开始录音</button>
        <span class="reading-pick-info" id="nce2-rectime"></span>
        <span class="reading-level-wrap" title="录音音量：说话时绿色条应跳动；不动=麦克风没声音">
          <span class="reading-level-bar"><i id="nce2-level"></i></span>
        </span>
        <span class="reading-pick-info" id="nce2-recmsg"></span>
      </div>
    </div>
  `
  setupShadowRecorder(el, host, l, focusTracker)
}

// ---------- 跟读录音机（克隆 reading.js 全链路：getUserMedia →
// MediaRecorder → 音量条 → 静音检测 → base64 提交） ----------
function setupShadowRecorder(el, host, lesson, focusTracker) {
  const btn = host.querySelector('#nce2-record')
  if (!btn) return
  const timeEl = host.querySelector('#nce2-rectime')
  const msgEl = host.querySelector('#nce2-recmsg')
  let recorder = null
  let chunks = []
  let timer = null
  let startedAt = 0
  let recording = false
  let meterStop = null

  // 录音实时音量条：麦克风被系统静音/未授权时 Chrome 仍返回全零数据，
  // 孩子察觉不到——音量条不动就是最好的即时提示
  const startMeter = (stream) => {
    const bar = host.querySelector('#nce2-level')
    if (!bar) return null
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const src = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      src.connect(analyser)
      const buf = new Uint8Array(analyser.fftSize)
      let raf = 0
      const tick = () => {
        analyser.getByteTimeDomainData(buf)
        let peak = 0
        for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128))
        const level = Math.min(1, peak / 60)
        bar.style.width = `${Math.round(level * 100)}%`
        bar.classList.toggle('silent', level < 0.03)
        raf = requestAnimationFrame(tick)
      }
      tick()
      return () => {
        cancelAnimationFrame(raf)
        src.disconnect()
        ctx.close().catch(() => {})
        bar.style.width = '0%'
      }
    } catch {
      return null
    }
  }

  // 提交前检测静音：解码录音取峰值，全零则拦截（不给老师交白卷）
  const blobIsSilent = async (blob) => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const ab = await blob.arrayBuffer()
      const audio = await ctx.decodeAudioData(ab)
      let peak = 0
      for (let c = 0; c < audio.numberOfChannels; c++) {
        const d = audio.getChannelData(c)
        for (let i = 0; i < d.length; i += 16) peak = Math.max(peak, Math.abs(d[i]))
      }
      ctx.close().catch(() => {})
      return peak < 0.01
    } catch {
      return false // 解码失败（如浏览器不支持该格式）不拦截
    }
  }

  btn.addEventListener('click', async () => {
    if (!recording) {
      if (btn.disabled) return
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        recorder = new MediaRecorder(stream)
        chunks = []
        recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data)
        recorder.onstop = () => {
          stream.getTracks().forEach((t) => t.stop())
          if (meterStop) { meterStop(); meterStop = null }
          focusTracker.hooks.onRecord() // 录音结束
          submit()
        }
        recorder.start()
        recording = true
        focusTracker.hooks.onRecord() // 专注力：录音开始记入活动时间线
        btn.dataset.recording = '1'
        startedAt = Date.now()
        btn.textContent = '停止录音'
        btn.classList.add('danger')
        msgEl.textContent = ''
        meterStop = startMeter(stream)
        timer = setInterval(() => {
          const sec = Math.floor((Date.now() - startedAt) / 1000)
          timeEl.textContent = `⏱ ${fmtSec(sec)} / ${fmtSec(MAX_RECORD_SEC)}`
          if (sec >= MAX_RECORD_SEC) recorder.stop()
        }, 500)
      } catch (e) {
        msgEl.textContent = `无法访问麦克风：${e.message}`
      }
    } else {
      recorder.stop()
    }
  })

  async function submit() {
    recording = false
    delete btn.dataset.recording
    clearInterval(timer)
    btn.textContent = '开始录音'
    btn.classList.remove('danger')
    const duration = Math.round((Date.now() - startedAt) / 1000)
    timeEl.textContent = ''
    const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' })
    if (blob.size < 200) {
      msgEl.textContent = '录音太短，请重试'
      return
    }
    msgEl.textContent = '检查录音…'
    if (await blobIsSilent(blob)) {
      msgEl.innerHTML = '⚠️ 录音里没有任何声音，已取消提交。请检查：<br>' +
        '① 电脑是否静音 / 麦克风被挡；② 系统设置 → 隐私与安全性 → 麦克风，是否允许 Chrome；<br>' +
        '③ 换一个麦克风设备后重录。录音时上方的音量条应随说话跳动。'
      return
    }
    msgEl.textContent = '提交中…'
    const b64 = await blobToB64(blob)
    try {
      await api.nce2Submit({
        lesson_id: lesson.id, kind: 'shadow',
        audio_b64: b64, mime: blob.type || 'audio/webm', duration_sec: duration,
      })
      lesson.shadow = { status: 'pending' }
      // 动作区换成「待批改」卡（重绘后本录音器随旧节点一起卸掉）
      if (el.querySelector('#nce2-action')) renderActionArea(el, lesson, 'student', focusTracker)
      else if (msgEl) msgEl.innerHTML = `✅ 已提交（${fmtDur(duration)}），等待老师批改`
    } catch (e) {
      msgEl.textContent = `提交失败：${e.message}`
    }
  }
}

function blobToB64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result.split(',')[1])
    r.onerror = reject
    r.readAsDataURL(blob)
  })
}

/**
 * 划词查词（reading.js setupSelectionLookup 同款）：拖选结束后读选区 →
 * 交给 lookup 工具条。onUnmount：容器被卸时摘 document 监听并销毁
 * focusTracker（文档级监听不随视图节点回收，必须显式清）。
 */
function setupSentSelectionLookup(listEl, lookup, onUnmount) {
  let timer = null
  const handle = () => {
    if (timer) clearTimeout(timer)
    // 250ms debounce（与泛读馆 epubjs selected 的节奏一致）
    timer = setTimeout(() => {
      const sel = window.getSelection()
      const text = (sel && sel.toString().trim()) || ''
      if (!sel || !sel.rangeCount) return
      const range = sel.getRangeAt(0)
      if (range.collapsed) {
        lookup.hideAll()
        return
      }
      if (!listEl.contains(range.commonAncestorContainer)) return
      if (!text || text.length > 80) return
      const word = text.replace(/[^A-Za-z'’-]/g, '')
      if (!word) return
      const context = text.replace(/\s+/g, ' ').trim().slice(0, 200)
      const rect = range.getBoundingClientRect()
      lookup.showSelection({
        word,
        context,
        range: range.cloneRange(),
        rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      })
    }, 250)
  }
  document.addEventListener('selectionchange', handle)
  // 离开详情页时移除（nce2 视图重挂载会重建）
  const mo = new MutationObserver(() => {
    if (!listEl.isConnected) {
      document.removeEventListener('selectionchange', handle)
      if (timer) clearTimeout(timer)
      if (onUnmount) onUnmount()
      mo.disconnect()
    }
  })
  mo.observe(document.getElementById('app') || document.body, { childList: true, subtree: true })
}

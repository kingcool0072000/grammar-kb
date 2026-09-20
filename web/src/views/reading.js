import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { createLookupLayer } from './library/lookup.js'
import { applyReadPref } from '../readpref.js'
import { createFocusTracker } from './library/focus.js'

// 阅读练习：派生文章列表 → 详情（阅读 + 选段录音提交 + 点词查词典）。
// 学生视角：列表只见派生文（后端已过滤），显示字数；
// 详情页可朗读录音（一段 ≤300 词、≤5 分钟），提交后待教师 10 分制批改。
// 查词/发音完全复用泛读馆的划词浮层（lookup.js）：点词 → 工具条（发音/查词）→ 查词浮层。
const MAX_PICK_WORDS = 300
const MAX_RECORD_SEC = 300

export async function mountReading(el, { role } = {}) {
  const auth = getAuth()
  const myRole = role || (auth && auth.role) || 'student'
  el.innerHTML = '<div class="view-head"><h1>阅读练习</h1><p>加载中…</p></div>'
  let arts, recs
  try {
    ;[arts, recs] = await Promise.all([
      api.readingArticles(),
      api.readingRecordings(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  renderList(el, arts, recs, myRole)
}

// ---------- 列表 ----------
function renderList(el, arts, recs, role) {
  if (!arts.length) {
    el.innerHTML = `
      <div class="view-head"><h1>阅读练习</h1></div>
      <p style="color:var(--ink-soft)">还没有派生文章。老师添加后会出现在这里。</p>`
    return
  }
  // 按 base_key 分组展示
  const groups = new Map()
  for (const a of arts) {
    if (!groups.has(a.base_key)) groups.set(a.base_key, [])
    groups.get(a.base_key).push(a)
  }
  el.innerHTML = `
    <div class="view-head">
      <h1>阅读练习</h1>
      <p>选择一篇派生文章开始练习：阅读 → 选段录音 → 提交老师批改。共 ${arts.length} 篇。</p>
    </div>
    ${[...groups.entries()].map(([key, list]) => `
      <section class="fce-group">
        <div class="fce-group-title">${escapeHtml(keyLabel(key))}（${list.length} 篇）</div>
        <div class="reading-art-list">
          ${list.map((a) => artCard(a, recs)).join('')}
        </div>
      </section>`).join('')}
    <div class="reading-hist-pop" id="rd-hist-pop" hidden>
      <div class="reading-hist-pop-mask" id="rd-hist-mask"></div>
      <div class="reading-hist-pop-body">
        <div class="reading-hist-pop-head">
          <b id="rd-hist-title"></b>
          <button class="reading-btn small" id="rd-hist-close">关闭</button>
        </div>
        <div id="rd-hist-list"></div>
      </div>
    </div>
  `
  // 弹窗：展示某篇文章的历次批改
  const pop = el.querySelector('#rd-hist-pop')
  const openHist = (art) => {
    const mine = recs.filter((r) => r.article_id === art.id)
    el.querySelector('#rd-hist-title').textContent = art.title || '未命名'
    el.querySelector('#rd-hist-list').innerHTML = mine.length
      ? mine.slice().reverse().map((r) => `
          <div class="reading-graded-row">
            <div class="fce-his-row">
              <span class="fce-his-date">${(r.created_at || '').slice(0, 16).replace('T', ' ')} · ${fmtSec(r.duration_sec || 0)}</span>
              ${r.status === 'graded'
                ? `<b class="fce-his-score ok">老师 ${r.teacher_score}/10</b>`
                : '<b class="fce-his-score pend">待批改</b>'}
            </div>
            ${r.teacher_comment ? `<div class="reading-graded-comment">💬 ${escapeHtml(r.teacher_comment)}</div>` : ''}
          </div>`).join('')
      : '<p class="reading-hint">还没有朗读记录</p>'
    pop.hidden = false
  }
  el.querySelector('#rd-hist-close').addEventListener('click', () => (pop.hidden = true))
  el.querySelector('#rd-hist-mask').addEventListener('click', () => (pop.hidden = true))

  el.querySelectorAll('.reading-card').forEach((card) => {
    card.addEventListener('click', () =>
      renderDetail(el, Number(card.dataset.id), role),
    )
  })
  el.querySelectorAll('[data-hist]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation() // 不触发卡片进入详情
      const a = arts.find((x) => x.id === Number(btn.dataset.hist))
      if (a) openHist(a)
    })
  })
}

function artCard(a, recs) {
  const mine = recs.filter((r) => r.article_id === a.id)
  const best = mine
    .filter((r) => r.status === 'graded')
    .reduce((x, y) => (y.teacher_score > (x?.teacher_score ?? -1) ? y : x), null)
  const badge = best
    ? `<b class="fce-his-score ok">最高 ${best.teacher_score}/10</b>`
    : mine.length
      ? '<b class="fce-his-score pend">待批改</b>'
      : ''
  return `
    <div class="reading-card" data-id="${a.id}" role="button">
      <span class="reading-card-title">${escapeHtml(a.title || '未命名')}</span>
      <span class="reading-card-meta">
        <span class="tag" style="--cat-color:#8a6d3b">${a.words} 词</span>
        ${a.source ? `<span class="reading-card-src">${escapeHtml(a.source)}</span>` : ''}
        ${badge}
        ${mine.length ? `<button class="reading-btn small" data-hist="${a.id}" title="查看批改记录">📋 记录</button>` : ''}
      </span>
    </div>`
}

function keyLabel(key) {
  // T1P5 / T1P7-A → Test 1 · Part 5 / Test 1 · Part 7 · 人物 A
  const m = key.match(/^T(\d)P(\d)(?:-([A-D]))?$/)
  if (!m) return key
  return `Test ${m[1]} · Part ${m[2]}${m[3] ? ` · 人物 ${m[3]}` : ''} 派生`
}

// ---------- 详情 ----------
async function renderDetail(el, id, role) {
  el.innerHTML = '<div class="view-head"><h1>阅读练习</h1><p>加载中…</p></div>'
  let art, myRecs
  try {
    ;[art, myRecs] = await Promise.all([
      api.readingArticle(id),
      api.readingRecordings(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  // 本篇的批改历史（学生自己在本篇的全部录音与批改结果）
  const recsForArt = myRecs.filter((r) => r.article_id === id)
  const gradedHist = recsForArt.filter((r) => r.status === 'graded')
  const pendingN = recsForArt.length - gradedHist.length
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="rd-back">← 返回列表</button>
      <h1>${escapeHtml(art.title || '未命名')}</h1>
      <p>${art.words} 词 · ${escapeHtml(keyLabel(art.base_key))}${art.source ? ` · 来源：${escapeHtml(art.source)}` : ''}</p>
    </div>
    ${recsForArt.length ? `
    <section class="fce-group">
      <div class="fce-group-title">📝 我的朗读记录（本篇）</div>
      ${gradedHist.slice(0, 5).reverse().map((r) => `
        <div class="reading-graded-row">
          <div class="fce-his-row">
            <span class="fce-his-date">${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
            <b class="fce-his-score ok">老师 ${r.teacher_score}/10</b>
          </div>
          ${r.teacher_comment ? `<div class="reading-graded-comment">💬 ${escapeHtml(r.teacher_comment)}</div>` : ''}
        </div>`).join('')}
      ${pendingN > 0 ? `<div class="fce-his-row"><span class="fce-his-what">最新一次录音</span><b class="fce-his-score pend">待批改</b></div>` : ''}
    </section>` : ''}
    <div class="reading-article" id="rd-text">${renderText(art.text)}</div>
    <div class="reading-toolbar">
      ${art.has_audio ? `
      <div class="reading-tool-row">
        <span class="reading-tool-label">🎧 试听原文：</span>
        <button class="reading-btn" id="rd-a-play">播放</button>
        <button class="reading-btn" id="rd-a-pause">暂停</button>
        <button class="reading-btn" id="rd-a-stop">停止</button>
        <span class="reading-pick-info" id="rd-a-info">先听一遍范读，再自己朗读</span>
      </div>` : ''}
      <div class="reading-tool-row">
        <span class="reading-tool-label">🎙 全文朗读：</span>
        <span class="reading-pick-info" id="rd-pickinfo">共 ${art.words} 词，录音将提交整篇内容</span>
      </div>
      <div class="reading-tool-row">
        <button class="reading-btn primary" id="rd-record" ${art.words > MAX_PICK_WORDS ? 'disabled' : ''}>开始录音</button>
        <span class="reading-pick-info" id="rd-rectime"></span>
        <span class="reading-level-wrap" title="录音音量：朗读时绿色条应跳动；不动=麦克风没声音">
          <span class="reading-level-bar"><i id="rd-level"></i></span>
        </span>
        <span class="reading-pick-info" id="rd-recmsg"></span>
      </div>
      <p class="reading-hint">选中（拖选/双击）单词 → 工具条里可发音、查词（ECDICT · AI 兜底）。</p>
    </div>
  `

  // ---- 划词查词（完全复刻泛读馆：selectionchange 式取词 → 塌缩 → 工具条） ----
  const articleEl = el.querySelector('.reading-article')

  // ---- 专注力采集（精读场景：module=reading，范围=本篇文章） ----
  const focusTracker = createFocusTracker({
    readerRoot: articleEl, viewerEl: articleEl, bookId: id, bookTitle: art.title || '',
    enabled: role !== 'teacher', module: 'reading',
    initialRangeLabel: `${art.title || ''}(${art.words}词)`.slice(0, 120),
  })
  // 精读无「书内进度」概念：整篇即范围 0 → 100
  focusTracker.hooks.onReloc(0, `${art.title || ''}(${art.words}词)`.slice(0, 120))
  focusTracker.hooks.onReloc(100)
  const lookup = createLookupLayer(articleEl, {
    onLookup: focusTracker.hooks.onLookup,
    onSpeak: focusTracker.hooks.onSpeak,
    onSelect: focusTracker.hooks.onSelect,
  })
  // 教师开关：student.focus=false 时静默停采（与泛读馆同一开关）
  if (role !== 'teacher') {
    api.librarySettings().then((cfg) => {
      if (cfg && cfg.student && cfg.student.focus === false) focusTracker.disable()
    }).catch(() => {})
  }
  setupSelectionLookup(articleEl, lookup, () => focusTracker.destroy())

  // ---- 正文主题跟随教师配置（与泛读馆学生端同一份 student 主题） ----
  applyStudentTheme(articleEl, role)

  // ---- 试听原文（范读 WAV：播放/暂停/停止三键） ----
  const aPlay = el.querySelector('#rd-a-play')
  if (aPlay && art.has_audio) setupAudioPlayer(el, art, aPlay)

  // 录音范围 = 全文（派生文 120-300 词）；超上限（理论不该发生）禁用按钮
  if (art.words > MAX_PICK_WORDS) {
    el.querySelector('#rd-pickinfo').textContent =
      `全文 ${art.words} 词，超过 ${MAX_PICK_WORDS} 词上限，请联系老师`
  }

  // ---- 录音 ----
  // focusTracker 是本函数的局部采集器，必须显式传入：曾按名直接引用
  //（setupRecorder 与本函数作用域不同），点「开始录音」即 ReferenceError，
  // 录音功能自 8ec47ff 起损坏
  setupRecorder(el, art, focusTracker)
  el.querySelector('#rd-back').addEventListener('click', () => mountReading(el, { role }))
}

/** 学生端：正文底色/文字色用教师统一配置的主题（与泛读馆同源；教师默认纸白）。 */
const RD_THEMES = {
  paper: { bg: '#FFFFFF', fg: '#1F2933' },
  sepia: { bg: '#F5ECD9', fg: '#5B4636' },
  night: { bg: '#22272E', fg: '#C9CDD3' },
  contrast: { bg: '#000000', fg: '#FFFFFF' },
}
/** 学生端：正文底色/文字色用教师统一配置的主题（与泛读馆同源；教师默认纸白）。 */
function applyStudentTheme(articleEl, role) {
  applyReadPref(articleEl) // 排版偏好（#/readconf）：字号/栏宽走 CSS 变量
  if (role === 'teacher') {
    articleEl.style.background = RD_THEMES.paper.bg
    articleEl.style.color = RD_THEMES.paper.fg
    return
  }
  api.librarySettings()
    .then((s) => {
      const t = s && s.student && RD_THEMES[s.student.preset]
      if (!t) return
      articleEl.style.background = t.bg
      articleEl.style.color = t.fg
    })
    .catch(() => { /* 取不到配置则保持默认护眼底 */ })
}

/**
 * 划词查词（泛读馆 epubcore.onSelected 的主文档版）：
 * 拖选/双击结束后读选区 → 清洗提取单词（>80 字符忽略）→ 交给 lookup
 * （showSelection 内部塌缩选区防系统菜单，并用 range 做 mark 锚点高亮）。
 */
function setupSelectionLookup(articleEl, lookup, onUnmount) {
  let timer = null
  const handle = () => {
    if (timer) clearTimeout(timer)
    // 250ms debounce（与 epubjs selected 事件的节奏一致）
    timer = setTimeout(() => {
      const sel = window.getSelection()
      const text = (sel && sel.toString().trim()) || ''
      if (!sel || !sel.rangeCount) return
      const range = sel.getRangeAt(0)
      if (range.collapsed) {
        // 选区被清空（点了空白）→ 收起浮层
        lookup.hideAll()
        return
      }
      if (!articleEl.contains(range.commonAncestorContainer)) return
      if (!text || text.length > 80) return
      const word = text.replace(/[^A-Za-z'’-]/g, '')
      if (!word) return
      const context = text.replace(/\s+/g, ' ').trim().slice(0, 200)
      const rect = range.getBoundingClientRect()
      const host = articleEl.getBoundingClientRect()
      lookup.showSelection({
        word,
        context,
        range: range.cloneRange(),
        x: rect.left - host.left + rect.width / 2,
        y: rect.top - host.top,
      })
    }, 250)
  }
  // 与 epubjs 同源：selectionchange（拖选/双击/触屏长按/编程选区全覆盖）
  document.addEventListener('selectionchange', handle)
  // 离开详情页时移除（reading 视图重挂载会重建）
  const mo = new MutationObserver(() => {
    if (!articleEl.isConnected) {
      document.removeEventListener('selectionchange', handle)
      if (timer) clearTimeout(timer)
      if (onUnmount) onUnmount()
      mo.disconnect()
    }
  })
  mo.observe(document.getElementById('app') || document.body, { childList: true, subtree: true })
}

function renderText(text) {
  // 段落分行（与泛读馆正文同构的纯 <p> 结构）；**加粗**（填空答案）保留 strong 标记
  return (text || '')
    .split(/\n+/)
    .filter((p) => p.trim())
    .map((p, i) => {
      const html = escapeHtml(p).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      return `<p data-pidx="${i}">${html}</p>`
    })
    .join('')
}

// ---------- 范读播放器（播放/暂停/停止） ----------
function setupAudioPlayer(el, art, playBtn) {
  const pauseBtn = el.querySelector('#rd-a-pause')
  const stopBtn = el.querySelector('#rd-a-stop')
  const info = el.querySelector('#rd-a-info')
  let audio = null        // Audio 元素（首次点播放时懒加载）
  let objUrl = null
  let loading = false

  const setInfo = (t, active = false) => {
    info.textContent = t
    info.classList.toggle('active', active)
  }

  const syncButtons = () => {
    const playing = audio && !audio.paused && !audio.ended
    playBtn.disabled = playing || loading
    pauseBtn.disabled = loading || !audio || audio.paused
    stopBtn.disabled = loading || !audio || audio.ended
  }

  const ensureAudio = async () => {
    if (audio || loading) return audio
    loading = true
    syncButtons()
    setInfo('范读加载中…')
    try {
      const blob = await api.readingAudioBlob(art.id)
      objUrl = URL.createObjectURL(blob)
      audio = new Audio(objUrl)
      audio.addEventListener('timeupdate', () => {
        if (!audio.paused) setInfo(`播放中 ${fmtSec(Math.floor(audio.currentTime))} / ${fmtSec(Math.ceil(audio.duration || 0))}`, true)
      })
      audio.addEventListener('ended', () => {
        setInfo('播放完毕，轮到你读一遍吧')
        syncButtons()
      })
      audio.addEventListener('pause', () => {
        if (!audio.ended && audio.currentTime > 0) setInfo(`已暂停 ${fmtSec(Math.floor(audio.currentTime))} / ${fmtSec(Math.ceil(audio.duration || 0))}`, true)
        syncButtons()
      })
      audio.addEventListener('canplay', syncButtons)
    } catch {
      setInfo('范读加载失败，请稍后重试')
    }
    loading = false
    syncButtons()
    return audio
  }

  playBtn.addEventListener('click', async () => {
    const a = audio || (await ensureAudio())
    if (!a) return
    try { await a.play() } catch { /* 浏览器自动播放策略拦截，用户手点一般不会 */ }
    setInfo(`播放中 ${fmtSec(Math.floor(a.currentTime))} / ${fmtSec(Math.ceil(a.duration || 0))}`, true)
    syncButtons()
  })
  pauseBtn.addEventListener('click', () => {
    if (audio && !audio.paused) audio.pause()
    syncButtons()
  })
  stopBtn.addEventListener('click', () => {
    if (!audio) return
    audio.pause()
    audio.currentTime = 0   // 停止 = 回到开头
    setInfo('已停止，可重新播放')
    syncButtons()
  })
  syncButtons()

  // 离开详情页时释放资源（返回列表按钮在 renderDetail 里绑定）
  el.querySelector('#rd-back').addEventListener('click', () => {
    if (audio) { audio.pause(); audio.src = '' }
    if (objUrl) URL.revokeObjectURL(objUrl)
  }, { once: true })
}

// ---------- 录音机 ----------
// focusTracker：专注力采集器（renderArticle 传入；录音开始/结束记入活动时间线）
function setupRecorder(el, art, focusTracker) {
  const btn = el.querySelector('#rd-record')
  const timeEl = el.querySelector('#rd-rectime')
  const msgEl = el.querySelector('#rd-recmsg')
  let recorder = null
  let chunks = []
  let timer = null
  let startedAt = 0
  let recording = false
  let pickedText = ''
  let meterStop = null // 音量条 rAF 停止器

  // 录音范围 = 全文：正文段落文本即提交内容
  const allParas = () => [...el.querySelectorAll('#rd-text p')]
  const fullText = () =>
    allParas().map((p) => p.textContent.replace(/\s+/g, ' ').trim()).join('\n\n')

  // 录音实时音量条：麦克风被系统静音/未授权时 Chrome 仍返回全零数据，
  // 孩子察觉不到——音量条不动就是最好的即时提示
  const startMeter = (stream) => {
    const bar = el.querySelector('#rd-level')
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
        const level = Math.min(1, peak / 60) // 说话通常 >0.3
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
      // 录音内容 = 全文（提交时随音频一并上送，供教师对照检查）
      pickedText = fullText()
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
    btn.disabled = art.words > MAX_PICK_WORDS
    const duration = Math.round((Date.now() - startedAt) / 1000)
    timeEl.textContent = ''
    const blob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' })
    if (blob.size < 200) {
      msgEl.textContent = '录音太短，请重试'
      return
    }
    // 静音拦截：麦克风被静音/未授权时 Chrome 录出的就是全零数据
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
      const rec = await api.readingSubmitRecording({
        article_id: art.id, audio_b64: b64,
        mime: blob.type || 'audio/webm', duration_sec: duration,
        selected_text: pickedText,
      })
      msgEl.innerHTML = `✅ 已提交（${fmtSec(duration)} · 全文 ${art.words} 词），等待老师批改`
      console.debug('recording submitted', rec.id)
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

function fmtSec(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

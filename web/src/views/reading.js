import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'

// 阅读练习：派生文章列表 → 详情（阅读 + 选段录音提交 + 选中单词查 ECDICT）。
// 学生视角：列表只见派生文（后端已过滤），显示字数；
// 详情页可朗读录音（一段 ≤300 词、≤5 分钟），提交后待教师 10 分制批改。
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
      <p class="reading-hint">点击单词可查词典（ECDICT）。一次只查一个单词。</p>
      <div class="reading-dict" id="rd-dict" hidden></div>
    </div>
  `

  // 录音范围 = 全文（派生文 120-300 词）；超上限（理论不该发生）禁用按钮
  if (art.words > MAX_PICK_WORDS) {
    el.querySelector('#rd-pickinfo').textContent =
      `全文 ${art.words} 词，超过 ${MAX_PICK_WORDS} 词上限，请联系老师`
  }

  // ---- 查词（一次一个单词；点击的单词高亮） ----
  el.querySelector('#rd-text').addEventListener('click', async (e) => {
    const w = e.target.closest('w')
    if (!w) return
    // 单词高亮：清除旧高亮，点亮当前词（面板关闭时一并清除）
    el.querySelectorAll('#rd-text w.active').forEach((x) => x.classList.remove('active'))
    w.classList.add('active')
    // 所有格/复数所有格还原：children's → children、dogs' → dogs
    const raw = w.dataset.w
    const word = raw.endsWith("s'") ? raw.slice(0, -1) : raw.replace(/'s$/, '').replace(/’s$/, '')
    const box = el.querySelector('#rd-dict')
    box.hidden = false
    box.innerHTML = `<div class="reading-dict-word">${escapeHtml(raw)}</div><p class="reading-dict-body">查询中…</p>`
    try {
      const entry = await api.dict(word)
      const lines = (entry.gloss_lines || [])
        .map((g) => `<div class="reading-dict-line">${g.pos ? `<i>${escapeHtml(g.pos)}</i>` : ''} ${escapeHtml(g.text)}</div>`)
        .join('')
      const forms = Object.entries(entry.forms || {})
        .map(([k, v]) => `${formCn(k)} ${escapeHtml(v)}`)
        .join(' · ')
      box.innerHTML = `
        <div class="reading-dict-word">${escapeHtml(entry.word)} <span class="reading-dict-ph">${escapeHtml(entry.phonetic)}</span> <button class="reading-btn small" data-tts="${escapeHtml(entry.word)}" title="播放发音">🔊 发音</button></div>
        <div class="reading-dict-body">${lines || escapeHtml(entry.gloss || '（无释义）')}</div>
        ${forms ? `<div class="reading-dict-forms">${forms}</div>` : ''}
        <button class="reading-btn small" id="rd-dict-close">关闭</button>`
      box.querySelector('#rd-dict-close').addEventListener('click', () => {
        box.hidden = true
        el.querySelectorAll('#rd-text w.active').forEach((x) => x.classList.remove('active'))
      })
    } catch {
      box.innerHTML = `<div class="reading-dict-word">${escapeHtml(raw)} <button class="reading-btn small" data-tts="${escapeHtml(word)}" title="播放发音">🔊 发音</button></div>
        <p class="reading-dict-body">词典未收录该词。</p>
        <button class="reading-btn small" id="rd-dict-close">关闭</button>`
      box.querySelector('#rd-dict-close').addEventListener('click', () => {
        box.hidden = true
        el.querySelectorAll('#rd-text w.active').forEach((x) => x.classList.remove('active'))
      })
    }
  })
  // TTS 发音（事件委托，词典框内所有 🔊 按钮共用）
  el.querySelector('#rd-dict').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-tts]')
    if (!b) return
    try {
      const u = new SpeechSynthesisUtterance(b.dataset.tts)
      u.lang = 'en-US'
      u.rate = 0.9
      speechSynthesis.cancel()
      speechSynthesis.speak(u)
    } catch { /* 浏览器不支持 TTS 时静默 */ }
  })

  // ---- 录音 ----
  setupRecorder(el, art)
  el.querySelector('#rd-back').addEventListener('click', () => mountReading(el, { role }))
}

function renderText(text) {
  // 段落分行；单词包 <w> 供查词。**加粗**（填空答案）保留 strong 标记
  return (text || '')
    .split(/\n+/)
    .filter((p) => p.trim())
    .map((p, i) => {
      const html = escapeHtml(p)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/[A-Za-z][A-Za-z'-]*/g, (w) => `<w data-w="${w.toLowerCase()}">${w}</w>`)
      return `<p data-pidx="${i}">${html}</p>`
    })
    .join('')
}

function formCn(k) {
  return {
    past: '过去式', past_participle: '过去分词', present_participle: '现在分词',
    third_singular: '三单', plural: '复数', comparative: '比较级', superlative: '最高级',
  }[k] || k
}

// ---------- 录音机 ----------
function setupRecorder(el, art) {
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
          submit()
        }
        recorder.start()
        recording = true
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

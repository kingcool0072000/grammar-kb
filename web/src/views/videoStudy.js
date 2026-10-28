import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { speak } from '../tts.js'
import { loadYouTubeApi } from '../youtube.js'

// 视频学习（学生·任务制）：只看被安排的视频，按 本周任务 / 待完成 /
// 已完成 分组；已完成的深度交互＝回听自己的复述录音 + 看老师评语 +
// 随时重录刷新成绩。详情页：iframe 看视频 → 生僻词卡（TTS）+ 复述提示
// → 复述录音（音量条/静音拦截/自动提交）。教师进入为只读预览。
const MAX_RECORD_SEC = 600

export async function mountVideoStudy(el, { role } = {}) {
  const auth = getAuth()
  const myRole = role || (auth && auth.role) || 'student'
  el.innerHTML = '<div class="view-head"><h1>视频学习</h1><p>加载中…</p></div>'
  let vids, retells, cats
  try {
    ;[vids, retells] = await Promise.all([
      api.videoStudyList(),
      api.videoRetells(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  // 教师走全量清单（数组）；学生返回 {videos, categories}
  if (Array.isArray(vids)) {
    cats = null
  } else {
    cats = vids.categories || {}
    vids = vids.videos || []
  }
  renderVideoList(el, vids, retells, myRole, cats)
  // 课程表/计划卡/教师预览直达：#/videoStudy?vid=ID
  const m = location.hash.match(/[?&]vid=([A-Za-z0-9_-]{11})/)
  if (m && vids.some((v) => v.vid === m[1]))
    renderVideoDetail(el, m[1], myRole)
}

// ---------- 列表（学生：任务制分组；教师：全量单列） ----------
function renderVideoList(el, vids, retells, role, cats) {
  if (!vids.length) {
    el.innerHTML = `
      <div class="view-head"><h1>视频学习</h1></div>
      <p style="color:var(--ink-soft)">还没有安排视频。老师把这周的视频排进计划后，这里就会出现。</p>`
    return
  }
  const byVid = new Map(vids.map((v) => [v.vid, v]))
  const card = (v) => {
    const mine = retells.filter((r) => r.vid === v.vid)
    const best = v.best_score != null ? v.best_score
      : mine.filter((r) => r.status === 'graded')
        .reduce((x, y) => Math.max(x ?? -1, y.teacher_score), null)
    const badge = best != null
      ? `<b class="fce-his-score ok">最高 ${best}/10</b>`
      : v.pending || mine.some((r) => r.status === 'pending')
        ? '<b class="fce-his-score pend">待批改</b>'
        : ''
    const nTag = (v.retell_n || mine.length)
      ? `<span class="tag" style="--cat-color:#7a5ba6">复述 ${v.retell_n || mine.length} 次</span>`
      : ''
    return `
      <div class="reading-card ${v.week ? 'rd-week' : ''}" data-vid="${v.vid}" role="button">
        <span class="reading-card-title">🎬 ${escapeHtml(v.title)}</span>
        <span class="reading-card-meta">
          ${v.week ? '<b class="rd-week-tag">📌 本周任务</b>' : ''}
          <span class="tag" style="--cat-color:#8a6d3b">${escapeHtml(v.category || '综合')}</span>
          <span class="tag" style="--cat-color:#4a6fa5">${fmtDur(v.duration_sec || 0)}</span>
          ${nTag}${badge}
        </span>
      </div>`
  }
  const section = (title, list, cls = '') => list.length ? `
    <section class="fce-group ${cls}">
      <div class="fce-group-title">${title}（${list.length}）</div>
      <div class="reading-art-list">${list.map(card).join('')}</div>
    </section>` : ''
  let bodyHtml
  if (!cats) {
    // 教师：全量单列
    bodyHtml = section('🎞 视频清单', vids.slice().sort((a, b) => a.ord - b.ord))
  } else {
    const inCats = (c) => (cats[c] || []).map((vid) => byVid.get(vid)).filter(Boolean)
    const weekAll = inCats('week')
    const weekDone = weekAll.filter((v) => v.done)
    const todoOld = inCats('todo').filter((v) => !v.week)
    const doneAll = inCats('done')
    const doneFold = doneAll.filter((v) => !v.week)
    const extra = inCats('extra')
    bodyHtml = `
      ${section(`📌 本周任务 · 已完成 ${weekDone.length}/${weekAll.length}`, weekAll)}
      ${section('🗓 待完成（之前安排的）', todoOld)}
      ${doneFold.length ? `
      <details class="rd-done-fold">
        <summary>✅ 已完成有成绩（${doneFold.length} 篇）<i>点开可回听录音、看评语、再录一次</i></summary>
        <div class="reading-art-list">${doneFold.map(card).join('')}</div>
      </details>` : ''}
      ${extra.length ? `
      <details class="rd-done-fold">
        <summary>📎 计划外做过（${extra.length}）</summary>
        <div class="reading-art-list">${extra.map(card).join('')}</div>
      </details>` : ''}
    `
  }
  el.innerHTML = `
    <div class="view-head">
      <h1>视频学习</h1>
      <p>${cats
        ? '看视频 → 用自己的话复述 → 提交录音等老师批改。完成的可回听录音、看评语、再录刷新成绩。'
        : `教师预览：全部上架视频 ${vids.length} 个。`}</p>
    </div>
    ${bodyHtml}
  `
  el.querySelectorAll('.reading-card').forEach((c) =>
    c.addEventListener('click', () => renderVideoDetail(el, c.dataset.vid, role)),
  )
}

// ---------- 详情 ----------
async function renderVideoDetail(el, vid, role) {
  el.innerHTML = '<div class="view-head"><h1>视频学习</h1><p>加载中…</p></div>'
  let v, myRetells
  try {
    ;[v, myRetells] = await Promise.all([
      api.videoStudyItem(vid),
      api.videoRetells(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  const recs = myRetells.filter((r) => r.vid === vid)
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1))
  const graded = recs.filter((r) => r.status === 'graded')
  const best = graded.reduce((x, y) => Math.max(x ?? -1, y.teacher_score), null)
  const pendingN = recs.length - graded.length
  const token = getAuth()?.token || ''
  const audioUrl = (id) => `/api/video-study/retells/${id}/audio?token=${encodeURIComponent(token)}`
  el.innerHTML = `
    <div class="view-head">
      <button class="fce-back-btn" id="vs-back">← 返回列表</button>
      <h1>🎬 ${escapeHtml(v.title)}</h1>
      <p>${escapeHtml(v.category || '')}${v.duration_sec ? ` · ${fmtDur(v.duration_sec)}` : ''}${best != null ? ` · 最高 ${best}/10` : ''}${pendingN ? ' · 最新待批改' : ''}</p>
    </div>
    ${recs.length ? `
    <section class="fce-group">
      <div class="fce-group-title">📝 我的复述记录（${recs.length} 次）</div>
      ${recs.slice(-5).reverse().map((r) => `
        <div class="reading-graded-row">
          <div class="fce-his-row">
            <span class="fce-his-date">${(r.created_at || '').slice(0, 16).replace('T', ' ')} · ${fmtSec(r.duration_sec || 0)}</span>
            ${r.status === 'graded'
              ? `<b class="fce-his-score ${r.teacher_score >= 8 ? 'ok' : ''}">老师 ${r.teacher_score}/10</b>`
              : '<b class="fce-his-score pend">待批改</b>'}
          </div>
          ${r.teacher_comment ? `<div class="reading-graded-comment">💬 ${escapeHtml(r.teacher_comment)}</div>` : ''}
          <audio controls preload="metadata" style="width:100%;height:36px;margin:4px 0 10px" src="${audioUrl(r.id)}"></audio>
        </div>`).join('')}
    </section>` : ''}
    <div class="vs-video-wrap">
      <div id="vs-yt-host"></div>
      <div class="vs-shield" title="受限播放器：请用下方控制条操作"></div>
      <div class="vs-ended-mask" id="vs-ended" hidden>
        <b id="vs-ended-title">✅ 看完了！</b>
        <p id="vs-ended-sub">去下面用英文把这个故事复述出来吧</p>
        <button class="reading-btn primary" id="vs-replay">↻ 重新播放</button>
      </div>
    </div>
    <div class="vs-bar" id="vs-bar">
      <button class="reading-btn small" id="vs-play">▶ 播放</button>
      <button class="reading-btn small" id="vs-back10" title="后退 10 秒重听">↺ 10s</button>
      <button class="reading-btn small" id="vs-fwd10" title="前进 10 秒">↻ 10s</button>
      <input type="range" id="vs-seek" min="0" max="1000" value="0" aria-label="播放进度" />
      <span class="vs-time" id="vs-time">0:00 / 0:00</span>
      <button class="reading-btn small" id="vs-cc" title="开关英文字幕">字幕：关</button>
    </div>
    <p class="reading-hint">受限播放器：只有播放/进度/字幕可控，不能点设置。视频加载不出来时：检查网络（需要能访问 YouTube）；也可以先看下面的生僻词和提示。</p>
    <div class="vs-side">
      <section class="vs-vocab">
        <div class="fce-group-title">📖 生僻词（复述前先过一遍）</div>
        ${(v.vocab || []).length ? (v.vocab || []).map((w) => `
          <div class="vs-vocab-row">
            <b class="vs-vocab-word" data-say="${escAttr(w.word)}">${escapeHtml(w.word)} <span class="vs-tts" title="读这个词">🔊</span></b>
            <span class="vs-vocab-gloss">${escapeHtml(w.gloss || '')}</span>
          </div>`).join('') : '<p class="reading-hint">这个词单没有生僻词。</p>'}
      </section>
      <section class="vs-hints">
        <div class="fce-group-title">💡 复述提示（按这个顺序讲）</div>
        ${(v.hints || '').split(/\n+/).filter(Boolean).map((h) => `<p class="vs-hint-line">${escapeHtml(h)}</p>`).join('')
          || '<p class="reading-hint">用自己的话讲讲这个故事：开头发生了什么、中间怎样发展、最后结果如何。</p>'}
      </section>
    </div>
    <div class="reading-toolbar" id="vs-toolbar">
      <div class="reading-tool-row">
        <span class="reading-tool-label">🎙 ${recs.length ? '再录一次（刷新成绩）' : '复述录音'}：</span>
        <span class="reading-pick-info">看完视频，合着提示用自己的话讲一遍（≤10 分钟）${best != null ? ` · 当前最高 ${best}/10` : ''}</span>
      </div>
      <div class="reading-tool-row">
        <button class="reading-btn primary" id="vs-record">开始录音</button>
        <span class="reading-pick-info" id="vs-rectime"></span>
        <span class="reading-level-wrap" title="录音音量：说话时绿色条应跳动；不动=麦克风没声音">
          <span class="reading-level-bar"><i id="vs-level"></i></span>
        </span>
        <span class="reading-pick-info" id="vs-recmsg"></span>
      </div>
      ${role === 'teacher' ? '<p class="reading-hint">教师账号打开为预览：录音提交请用学生账号。</p>' : ''}
    </div>
  `
  // 生僻词 TTS：点词行朗读（复述前练耳朵）
  el.querySelectorAll('[data-say]').forEach((row) => {
    row.addEventListener('click', () => {
      try { speak(row.dataset.say) } catch { /* TTS 不可用静默 */ }
    })
  })
  const backBtn = el.querySelector('#vs-back')
  const player = setupRestrictedPlayer(el, v)
  backBtn.addEventListener('click', () => {
    try { player && player.destroy() } catch { /* 已销毁 */ }
    mountVideoStudy(el, { role })
  })
  if (role !== 'teacher') setupRetellRecorder(el, v)
}

// ---------- 受限 YouTube 播放器 ----------
// controls=0 收掉原生控制条（齿轮随之消失），iframe 上盖透明盾板拦截
// 一切点击（标题外链、暂停/结束页「More videos」都点不到）；自绘控制条
// 只留 播放/进度/±10s/字幕开关；ENDED 与 PAUSED 都弹不透明遮罩盖住
// 推荐层。清晰度不锁（YouTube 已废弃强制档位接口，自动码率）。
// API 脚本加载失败时降级为普通 iframe（原生控制条，功能可用优先）。
function setupRestrictedPlayer(el, video) {
  const host = el.querySelector('#vs-yt-host')
  if (!host) return null
  let player = null
  let timer = null
  let seeking = false
  let ccOn = false

  loadYouTubeApi().then((YT) => {
    if (!host.isConnected) return // 已离开详情页
    player = new YT.Player(host, {
      videoId: video.vid,
      playerVars: {
        controls: 0,        // 无原生控制条：齿轮/清晰度/全屏都不存在
        rel: 0,             // 相关推荐限同频道（结束页另有遮罩兜底）
        iv_load_policy: 3,  // 不加载视频内注释层
        disablekb: 1,       // 禁键盘快捷键
        fs: 0,              // 禁全屏按钮
        playsinline: 1,
        modestbranding: 1,
        origin: location.origin,
      },
      events: {
        onReady: () => {
          const d = player.getDuration() || 0
          el.querySelector('#vs-time').textContent = `0:00 / ${fmtSec(Math.floor(d))}`
        },
        onStateChange: (e) => {
          const YT_PLAYING = 1, YT_PAUSED = 2, YT_ENDED = 0
          const playBtn = el.querySelector('#vs-play')
          if (!playBtn) return
          if (e.data === YT_PLAYING) {
            playBtn.textContent = '⏸ 暂停'
            clearInterval(timer)
            timer = setInterval(tick, 500)
            // 播放中收掉遮罩（暂停层/结束层都不该留）
            el.querySelector('#vs-ended').hidden = true
          } else {
            playBtn.textContent = '▶ 播放'
            clearInterval(timer)
          }
          if (e.data === YT_ENDED) {
            showMask('✅ 看完了！', '去下面用英文把这个故事复述出来吧', '↻ 重新播放')
          } else if (e.data === YT_PAUSED && !seeking) {
            // 暂停时 YouTube 会叠「More videos」推荐层——遮住，只留继续入口
            showMask('⏸ 已暂停', '想重听某句？用控制条 ↺ 10s 多听几遍', '▶ 继续播放')
          }
        },
      },
    })
    // 调试/浏览器验证钩子（不进业务逻辑）
    window.__vsPlayer = player
  }).catch(() => {
    // 降级：普通 iframe（有原生控制条）——受限模式不可用时可用性优先
    if (!host.isConnected) return
    const ifr = document.createElement('iframe')
    ifr.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(video.vid)}?rel=0&iv_load_policy=3`
    ifr.title = video.title || 'video'
    ifr.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture'
    ifr.allowFullscreen = false
    host.replaceWith(ifr)
    const bar = el.querySelector('#vs-bar')
    if (bar) bar.innerHTML = '<span class="reading-hint">受限模式加载失败，已退回普通播放器（可能有设置按钮）。</span>'
  })

  function tick() {
    if (!player || !player.getDuration) return
    // 离开详情页后自清理（视图被整棵换掉时没有 destroy 时机）
    if (!el.querySelector('#vs-yt-host') && !document.querySelector('#vs-yt-host')) {
      clearInterval(timer)
      try { player.destroy() } catch { /* 已销毁 */ }
      return
    }
    const d = player.getDuration() || 0
    const c = player.getCurrentTime() || 0
    if (!seeking) el.querySelector('#vs-seek').value = String(d ? Math.round(c / d * 1000) : 0)
    el.querySelector('#vs-time').textContent = `${fmtSec(Math.floor(c))} / ${fmtSec(Math.floor(d))}`
  }

  function showMask(title, sub, btn) {
    const mask = el.querySelector('#vs-ended')
    if (!mask) return
    mask.querySelector('#vs-ended-title').textContent = title
    mask.querySelector('#vs-ended-sub').textContent = sub
    mask.querySelector('#vs-replay').textContent = btn
    mask.dataset.mode = btn.includes('继续') ? 'resume' : 'replay'
    mask.hidden = false
  }

  const playBtn = el.querySelector('#vs-play')
  if (playBtn) playBtn.addEventListener('click', () => {
    if (!player || !player.getPlayerState) return
    // 1=PLAYING 3=BUFFERING 都算在放
    const st = player.getPlayerState()
    if (st === 1 || st === 3) player.pauseVideo()
    else {
      el.querySelector('#vs-ended').hidden = true
      player.playVideo()
    }
  })
  const back10 = el.querySelector('#vs-back10')
  if (back10) back10.addEventListener('click', () => {
    if (player && player.seekTo) player.seekTo(Math.max(0, (player.getCurrentTime() || 0) - 10), true)
  })
  const fwd10 = el.querySelector('#vs-fwd10')
  if (fwd10) fwd10.addEventListener('click', () => {
    if (player && player.seekTo) {
      const d = player.getDuration() || 0
      player.seekTo(Math.min(d - 1, (player.getCurrentTime() || 0) + 10), true)
    }
  })
  const seek = el.querySelector('#vs-seek')
  if (seek) {
    seek.addEventListener('pointerdown', () => (seeking = true))
    seek.addEventListener('pointerup', () => (seeking = false))
    seek.addEventListener('input', () => {
      if (!player || !player.getDuration) return
      const d = player.getDuration() || 0
      player.seekTo((Number(seek.value) / 1000) * d, true)
    })
  }
  const replay = el.querySelector('#vs-replay')
  if (replay) replay.addEventListener('click', () => {
    if (!player) return
    el.querySelector('#vs-ended').hidden = true
    if (el.querySelector('#vs-ended').dataset.mode === 'resume')
      player.playVideo()               // 暂停继续：原地接着放
    else {
      player.seekTo(0, true)           // 看完重播：回开头
      player.playVideo()
    }
  })
  const ccBtn = el.querySelector('#vs-cc')
  if (ccBtn) ccBtn.addEventListener('click', () => {
    if (!player) return
    ccOn = !ccOn
    try {
      if (ccOn) {
        player.loadModule('captions')
        player.setOption('captions', 'track', { languageCode: 'en' })
      } else {
        player.setOption('captions', 'track', {})
        player.unloadModule('captions')
      }
    } catch { /* 该视频无字幕轨道时静默 */ }
    ccBtn.textContent = `字幕：${ccOn ? '开' : '关'}`
    ccBtn.classList.toggle('primary', ccOn)
  })

  return {
    destroy() {
      clearInterval(timer)
      try { player && player.destroy() } catch { /* 已销毁 */ }
    },
  }
}

// ---------- 复述录音机（交互对齐精读录音：音量条/静音拦截/自动提交） ----------
function setupRetellRecorder(el, video) {
  const btn = el.querySelector('#vs-record')
  const timeEl = el.querySelector('#vs-rectime')
  const msgEl = el.querySelector('#vs-recmsg')
  if (!btn) return
  let recorder = null
  let chunks = []
  let timer = null
  let startedAt = 0
  let recording = false
  let meterStop = null

  const startMeter = (stream) => {
    const bar = el.querySelector('#vs-level')
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
      return false
    }
  }

  btn.addEventListener('click', async () => {
    if (!recording) {
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
      await api.videoRetellSubmit({
        vid: video.vid, audio_b64: b64,
        mime: blob.type || 'audio/webm', duration_sec: duration,
      })
      msgEl.innerHTML = `✅ 复述已提交（${fmtSec(duration)}），等待老师批改`
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
function fmtDur(sec) {
  const m = Math.floor(sec / 60)
  return m >= 1 ? `${m} 分${sec % 60 ? ` ${sec % 60} 秒` : ''}` : `${sec} 秒`
}
function escAttr(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

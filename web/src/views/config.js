// 隐藏配置中心 #/config（不进 Tab）：合并原 #/ttsconf（发音）与
// #/readconf（阅读练习排版），并新增泛读馆宽度偏好（标准/宽）。
// 旧 hash 由 main.js 路由重定向到本页，不留独立页面。
import { speak, speakWithVoice, getTtsPref, setTtsPref, waitForVoices, ttsDiagnostics } from '../tts.js'
import { aboutHtml, getAccent, setAccent } from '../atlas.js'
import { api, getAuth } from '../api.js'
import {
  getWordCard, loadWordCards, setWordCardCache, wordCardSnapshot,
  WORD_CARD_FEATURES, WORD_CARD_FIELDS,
} from '../wordcard.js'
import { APP_GIT_SHA, APP_VERSION } from '../version.js'
import { getReadPref, setReadPref, resetReadPref } from '../readpref.js'
import { getFceAudioPref, setFceAudioPref } from './fceAudio.js'
import { escapeHtml, escAttr } from '../render.js'

const LS_LIB_PREF = 'gkb-lib-pref-v1'

function getLibPref() {
  try {
    return JSON.parse(localStorage.getItem(LS_LIB_PREF)) || {}
  } catch {
    return {}
  }
}

function setLibPref(patch) {
  localStorage.setItem(LS_LIB_PREF, JSON.stringify({ ...getLibPref(), ...patch }))
}

function langOf(v) {
  return (v.lang || '').replace('_', '-')
}
const isEn = (v) => langOf(v).toLowerCase().startsWith('en')

function voiceRow(v, selected) {
  const tags = [
    v.default ? '系统默认' : '',
    v.localService === false ? '在线' : '本地',
    isEn(v) ? '' : '非英语',
  ].filter(Boolean)
  return `
  <button class="tts-voice ${selected ? 'selected' : ''} ${isEn(v) ? '' : 'dim'}"
          data-uri="${escAttr(v.voiceURI)}" type="button">
    <span class="tts-radio"></span>
    <span class="tts-vname">${escapeHtml(v.name)}</span>
    <span class="tts-vlang">${escapeHtml(langOf(v))}</span>
    ${tags.map((t) => `<span class="tts-vtag">${escapeHtml(t)}</span>`).join('')}
    <span class="tts-vtest" data-test="${escAttr(v.voiceURI)}" title="试听">🔊</span>
  </button>`
}

export async function mountConfig(el) {
  const isTeacher = ((getAuth() || {}).role) === 'teacher'
  // 旧入口（#/ttsconf #/readconf）归一地址栏（不触发额外渲染）
  if (/^#\/(ttsconf|readconf)\??$/.test(location.hash)) {
    history.replaceState(null, '', '#/config')
  }
  const tts = getTtsPref()
  const rd = getReadPref()
  const lib = getLibPref()
  const fceAudioPref = getFceAudioPref()
  el.innerHTML = `
  <div class="ttsconf-page">
    <button class="fce-back-btn" id="cfg-back">← 返回</button>
    <div class="tts-hero" style="background:linear-gradient(140deg,#3d4a5a 0%,#4c5f80 50%,#4a719e 100%)">
      <p class="eyebrow">和爸学 · 隐藏设置</p>
      <h1>⚙️ 设置中心</h1>
      <p class="sub">发音、阅读排版这些不常用的偏好都收在这里。全部只影响本设备。</p>
    </div>

    <h2 class="cfg-section">🏷️ 版本</h2>
    <div class="tts-box" id="cfg-version">
      <p>前端 <b id="cfg-web-ver"></b></p>
      <p>服务端 <b id="cfg-srv-ver">读取中…</b></p>
      <span class="tts-vtag">改版本只改 grammar_kb/VERSION，再重新构建前端并重启服务。两边不一致时，说明 web/dist 和 Python 不是同一次发布。</span>
    </div>

    ${isTeacher ? `
    <h2 class="cfg-section">🃏 词卡内容</h2>
    <div class="tts-box">
      <p class="tts-vtag">各功能里单词卡片显示释义、插图、例句。保存在服务器，学生各端一起生效。泛读馆和阅读练习默认只开释义；背单词默认都开。</p>
      <div id="cfg-wc">读取中…</div>
      <p class="cfg-wc-status" id="cfg-wc-status"></p>
    </div>` : ''}

    <!-- ====== 泛读馆 ====== -->
    <h2 class="cfg-section">📚 泛读馆</h2>
    <div class="tts-box">
      <div class="rdc-row">
        <label>正文宽度</label>
        <div class="lib-seg" id="cfg-libwidth">
          <button type="button" data-v="standard" class="${!lib.libWidth || lib.libWidth === 'standard' ? 'active' : ''}">标准</button>
          <button type="button" data-v="semiwide" class="${lib.libWidth === 'semiwide' ? 'active' : ''}">稍宽（60%）</button>
          <button type="button" data-v="wide" class="${lib.libWidth === 'wide' ? 'active' : ''}">宽（80%）</button>
        </div>
        <span class="tts-vtag">下次打开书籍生效；标准=620px 窄栏，稍宽/宽按屏宽百分比</span>
      </div>
    </div>

    <!-- ====== 阅读练习 ====== -->
    <h2 class="cfg-section">📖 阅读练习</h2>
    <div class="tts-box">
      <div class="rdc-row">
        <label for="cfg-fs">字号</label>
        <input type="range" id="cfg-fs" min="15" max="28" step="1">
        <b id="cfg-fs-val"></b>
      </div>
      <div class="rdc-preview" id="cfg-preview">
        <p>The little boy opened his eyes and saw the sea for the first time. Waves rolled in, one after another, under the morning sun.</p>
      </div>
    </div>
    <div class="tts-box">
      <div class="rdc-row">
        <label for="cfg-w">每行宽度</label>
        <input type="range" id="cfg-w" min="480" max="980" step="20">
        <b id="cfg-w-val"></b>
      </div>
      <div class="rdc-widthbar"><i id="cfg-widthbar-i"></i></div>
    </div>
    <div class="tts-box tts-note">
      行太宽容易串行，推荐 560~680；字号推荐 18~22。
      <button class="tts-mini-btn" id="cfg-rd-reset" type="button">恢复默认</button>
    </div>

    <!-- ====== FCE 真题 ====== -->
    <h2 class="cfg-section">🎧 FCE 真题</h2>
    <div class="tts-box">
      <div class="rdc-row">
        <label>听力播放器</label>
        <div class="lib-seg" id="cfg-fce-audio">
          <button type="button" data-v="off" class="${!fceAudioPref ? 'active' : ''}">隐藏（考试模式）</button>
          <button type="button" data-v="on" class="${fceAudioPref ? 'active' : ''}">显示</button>
        </div>
        <span class="tts-vtag">做题时是否显示听力音频播放器；老师端始终显示</span>
      </div>
    </div>

    <!-- ====== 发音 ====== -->
    <h2 class="cfg-section">🗣️ 发音</h2>
    <div class="tts-box">
      <div class="rdc-row">
        <label>词光录音</label>
        <div class="lib-seg" id="cfg-atlas-accent">
          <button type="button" data-v="uk" class="${getAccent() === 'uk' ? 'active' : ''}">英式</button>
          <button type="button" data-v="us" class="${getAccent() === 'us' ? 'active' : ''}">美式</button>
        </div>
        <span class="tts-vtag">有词光录音时优先播放这一套；没有文件或播放失败时，仍用下面的浏览器语音。</span>
      </div>
      <p class="atlas-footnote" id="cfg-atlas-count"></p>
      ${aboutHtml()}
    </div>
    <div class="tts-box" id="cfg-tts-status">正在读取本设备的音色列表…</div>
    <div class="tts-box">
      <div class="tts-rate-row">
        <label for="cfg-rate">语速</label>
        <input type="range" id="cfg-rate" min="0.5" max="1.2" step="0.05" value="${tts.rate || 0.9}">
        <b id="cfg-rate-val">${(tts.rate || 0.9).toFixed(2)}</b>
        <button class="tts-mini-btn" id="cfg-rate-test" type="button">🔊 试听语速</button>
      </div>
    </div>
    <div id="cfg-voices"></div>
    <div class="tts-box tts-note">
      已选的默认声音只保存在这台设备上（换设备需重选）。
      <button class="tts-mini-btn" id="cfg-tts-reset" type="button">恢复跟随系统</button>
    </div>
  </div>`

  el.querySelector('#cfg-back').addEventListener('click', () => {
    if (history.length > 1) history.back()
    else location.hash = '/recite'
  })

  if (isTeacher) {
    const host = el.querySelector('#cfg-wc')
    const status = el.querySelector('#cfg-wc-status')
    const paintCards = () => {
      host.innerHTML = `
        <table class="cfg-wc">
          <thead>
            <tr><th>功能</th>${WORD_CARD_FIELDS.map((f) => `<th>${f.label}</th>`).join('')}</tr>
          </thead>
          <tbody>
            ${WORD_CARD_FEATURES.map((feat) => `
              <tr>
                <td>${feat.label}<small>${feat.hint}</small></td>
                ${WORD_CARD_FIELDS.map((field) => {
                  const on = getWordCard(feat.id)[field.id]
                  return `<td><input type="checkbox" data-feat="${feat.id}" data-key="${field.id}" ${on ? 'checked' : ''}></td>`
                }).join('')}
              </tr>`).join('')}
          </tbody>
        </table>`
    }
    try {
      const s = await api.librarySettings()
      if (s && s.student && s.student.wordCards) setWordCardCache(s.student.wordCards)
      else await loadWordCards()
    } catch {
      await loadWordCards()
    }
    paintCards()
    let wcSeq = 0
    host.addEventListener('change', async (e) => {
      const input = e.target.closest('input[data-feat]')
      if (!input) return
      const next = wordCardSnapshot()
      next[input.dataset.feat][input.dataset.key] = input.checked
      setWordCardCache(next)
      const mine = ++wcSeq
      status.textContent = '保存中…'
      try {
        const saved = await api.librarySaveSettings({ student: { wordCards: next } })
        if (mine !== wcSeq) return
        const cards = saved && saved.student && saved.student.wordCards
        if (cards) setWordCardCache(cards)
        status.textContent = '已保存，学生端立即生效'
      } catch (err) {
        if (mine !== wcSeq) return
        input.checked = !input.checked
        const revert = wordCardSnapshot()
        revert[input.dataset.feat][input.dataset.key] = input.checked
        setWordCardCache(revert)
        status.textContent = (err && err.message) || '保存失败'
      }
    })
  }

  const verLabel = (version, sha) => {
    const v = version || '—'
    return sha ? `${v} · ${sha}` : v
  }
  el.querySelector('#cfg-web-ver').textContent = verLabel(APP_VERSION, APP_GIT_SHA)
  api.version().then((info) => {
    const node = el.querySelector('#cfg-srv-ver')
    if (!node || !info) return
    node.textContent = verLabel(info.version, info.git)
  }).catch(() => {
    const node = el.querySelector('#cfg-srv-ver')
    if (node) node.textContent = '读取失败'
  })

  // ---- 泛读馆宽度 ----
  el.querySelector('#cfg-libwidth').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]')
    if (!b) return
    setLibPref({ libWidth: b.dataset.v })
    el.querySelectorAll('#cfg-libwidth button').forEach((x) =>
      x.classList.toggle('active', x === b),
    )
  })

  // ---- FCE 听力播放器显隐 ----
  el.querySelector('#cfg-fce-audio').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]')
    if (!b) return
    setFceAudioPref(b.dataset.v === 'on')
    el.querySelectorAll('#cfg-fce-audio button').forEach((x) =>
      x.classList.toggle('active', x === b),
    )
  })

  // ---- 阅读练习排版 ----
  const fsInput = el.querySelector('#cfg-fs')
  const fsVal = el.querySelector('#cfg-fs-val')
  const wInput = el.querySelector('#cfg-w')
  const wVal = el.querySelector('#cfg-w-val')
  const preview = el.querySelector('#cfg-preview')
  const wBar = el.querySelector('#cfg-widthbar-i')
  function refreshRd() {
    const p = getReadPref()
    fsInput.value = p.fontSize
    fsVal.textContent = `${p.fontSize}px`
    wInput.value = p.width
    wVal.textContent = `${p.width}px`
    preview.style.fontSize = `${p.fontSize}px`
    preview.style.maxWidth = `${p.width}px`
    wBar.style.width = `${Math.round(((p.width - 480) / (980 - 480)) * 100)}%`
  }
  fsInput.addEventListener('input', () => {
    setReadPref({ fontSize: Number(fsInput.value) })
    refreshRd()
  })
  wInput.addEventListener('input', () => {
    setReadPref({ width: Number(wInput.value) })
    refreshRd()
  })
  el.querySelector('#cfg-rd-reset').addEventListener('click', () => {
    resetReadPref()
    refreshRd()
  })
  refreshRd()

  el.querySelector('#cfg-atlas-accent').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]')
    if (!b) return
    setAccent(b.dataset.v)
    el.querySelectorAll('#cfg-atlas-accent button').forEach((x) =>
      x.classList.toggle('active', x === b),
    )
  })
  api.atlasInfo().then((info) => {
    const node = el.querySelector('#cfg-atlas-count')
    if (!node || !info) return
    if (!info.count) {
      node.textContent = '词光词条还没导入。导入后，对得上的单词会显示插画；现在先用浏览器语音。'
    } else if (info.media_ready) {
      node.textContent = `已导入 ${info.count} 条词光词条，插画和录音可播放。`
    } else {
      node.textContent = `已导入 ${info.count} 条词光词条。媒体文件还没放到服务器，发音会退回浏览器语音。`
    }
  }).catch(() => {})

  // ---- TTS ----
  const rateInput = el.querySelector('#cfg-rate')
  const rateVal = el.querySelector('#cfg-rate-val')
  rateInput.addEventListener('input', () => {
    rateVal.textContent = Number(rateInput.value).toFixed(2)
    setTtsPref({ rate: Number(rateInput.value) })
  })
  el.querySelector('#cfg-rate-test').addEventListener('click', () => speak('Hello! This is how I sound when I read for you.'))
  el.querySelector('#cfg-tts-reset').addEventListener('click', () => {
    localStorage.removeItem('gkb-tts-pref-v1')
    rateInput.value = '0.9'
    rateVal.textContent = '0.90'
    renderVoices(voices, null)
    statusEl.textContent = '已恢复跟随系统（未配置时用英式英语第一个声音）。'
  })

  const voicesEl = el.querySelector('#cfg-voices')
  const statusEl = el.querySelector('#cfg-tts-status')
  const voices = await waitForVoices()

  function renderVoices(list, selectedUri) {
    if (!list.length) {
      voicesEl.innerHTML = `
      <div class="tts-box tts-empty">
        这台设备没有报告任何语音音色。若是安卓/浏览器 WebView，请在系统设置 →
        语言和输入 → 文字转语音输出 里安装「Google 文字转语音」，装好后回到本页刷新。
      </div>`
      return
    }
    const en = list.filter(isEn)
    const other = list.filter((v) => !isEn(v))
    voicesEl.innerHTML = `
      ${en.length ? `<h2 class="tts-group">英语声音（推荐用这些）</h2>
      <div class="tts-list">${en.map((v) => voiceRow(v, v.voiceURI === selectedUri)).join('')}</div>` : ''}
      <h2 class="tts-group">其他语言（${other.length}）</h2>
      <div class="tts-list">${other.map((v) => voiceRow(v, v.voiceURI === selectedUri)).join('')}</div>`
  }

  renderVoices(voices, getTtsPref().voiceURI || '')
  statusEl.textContent = voices.length
    ? `本设备共 ${voices.length} 个音色（其中英语 ${voices.filter(isEn).length} 个）。点一条即设为默认。`
    : '未取到音色列表。'
  const diag = ttsDiagnostics()
  if (!diag.ok) {
    const d = document.createElement('div')
    d.style.cssText = 'margin-top:6px;color:#b3541e;font-size:13px'
    d.textContent = diag.reason === 'no-speechSynthesis'
      ? '⚠️ 此浏览器不支持语音合成。'
      : '⚠️ 引擎暂未报告音色。若试听无声，请把浏览器控制台 [TTS] 开头的日志发给开发。'
    statusEl.appendChild(d)
  }

  voicesEl.addEventListener('click', (e) => {
    const test = e.target.closest('[data-test]')
    if (test) {
      e.stopPropagation()
      speakWithVoice(test.dataset.test, Number(rateInput.value))
      return
    }
    const row = e.target.closest('.tts-voice')
    if (!row) return
    setTtsPref({ voiceURI: row.dataset.uri })
    renderVoices(voices, row.dataset.uri)
    statusEl.textContent = '已设为默认 ✓ 查词和朗读会用这个声音。'
    speak('Hello! This is how I sound when I read for you.')
  })
}

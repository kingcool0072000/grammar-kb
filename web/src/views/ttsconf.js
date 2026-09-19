// TTS 设置（隐藏页 #/ttsconf，不进 Tab）：列出本设备可用音色，
// 点选默认音色（localStorage，本设备生效）+ 试听 + 语速微调。
// 适配安卓 WebView：音色列表异步加载（waitForVoices），空列表给引擎提示。
import { speak, getTtsPref, setTtsPref, waitForVoices } from '../tts.js'
import { escapeHtml } from '../render.js'

const SAMPLE = 'Hello! This is how I sound when I read for you.'

function langOf(v) {
  return (v.lang || '').replace('_', '-')
}

function voiceRow(v, selected) {
  const isEn = langOf(v).toLowerCase().startsWith('en')
  const tags = [
    v.default ? '系统默认' : '',
    v.localService === false ? '在线' : '本地',
    isEn ? '' : '非英语',
  ].filter(Boolean)
  return `
  <button class="tts-voice ${selected ? 'selected' : ''} ${isEn ? '' : 'dim'}"
          data-uri="${escapeHtml(v.voiceURI)}" type="button">
    <span class="tts-radio"></span>
    <span class="tts-vname">${escapeHtml(v.name)}</span>
    <span class="tts-vlang">${escapeHtml(langOf(v))}</span>
    ${tags.map((t) => `<span class="tts-vtag">${escapeHtml(t)}</span>`).join('')}
    <span class="tts-vtest" data-test="${escapeHtml(v.voiceURI)}" title="试听">🔊</span>
  </button>`
}

export async function mountTtsConf(el) {
  const pref = getTtsPref()
  el.innerHTML = `
  <div class="ttsconf-page">
    <button class="fce-back-btn" id="tts-back">← 返回</button>
    <div class="tts-hero">
      <p class="eyebrow">和爸学 · 隐藏设置</p>
      <h1>🗣️ 发音设置</h1>
      <p class="sub">查词、预习朗读用的是这台设备自带的语音引擎。这里可以看看有哪些声音，挑一个最喜欢的当默认。</p>
    </div>
    <div class="tts-box" id="tts-status">正在读取本设备的音色列表…</div>
    <div class="tts-box">
      <div class="tts-rate-row">
        <label for="tts-rate">语速</label>
        <input type="range" id="tts-rate" min="0.5" max="1.2" step="0.05" value="${pref.rate || 0.9}">
        <b id="tts-rate-val">${(pref.rate || 0.9).toFixed(2)}</b>
        <button class="tts-mini-btn" id="tts-rate-test" type="button">🔊 试听语速</button>
      </div>
    </div>
    <div id="tts-voices"></div>
    <div class="tts-box tts-note">
      已选的默认声音只保存在这台设备上（换设备需重选）。
      <button class="tts-mini-btn" id="tts-reset" type="button">恢复跟随系统</button>
    </div>
  </div>`

  el.querySelector('#tts-back').addEventListener('click', () => {
    if (history.length > 1) history.back()
    else location.hash = '/recite'
  })

  const voicesEl = el.querySelector('#tts-voices')
  const statusEl = el.querySelector('#tts-status')
  const rateInput = el.querySelector('#tts-rate')
  const rateVal = el.querySelector('#tts-rate-val')

  // 语速即时保存
  rateInput.addEventListener('input', () => {
    rateVal.textContent = Number(rateInput.value).toFixed(2)
    setTtsPref({ rate: Number(rateInput.value) })
  })
  el.querySelector('#tts-rate-test').addEventListener('click', () => speak(SAMPLE))

  el.querySelector('#tts-reset').addEventListener('click', () => {
    localStorage.removeItem('gkb-tts-pref-v1')
    rateInput.value = '0.9'
    rateVal.textContent = '0.90'
    renderVoices(voices, null)
    statusEl.textContent = '已恢复跟随系统（未配置时用英式英语第一个声音）。'
  })

  const voices = await waitForVoices()

  function renderVoices(list, selectedUri) {
    if (!list.length) {
      voicesEl.innerHTML = `
      <div class="tts-box tts-empty">
        这台设备没有报告任何语音音色（安卓 WebView 常见于未安装 TTS 引擎）。<br>
        可在系统设置 → 语言和输入 → 文字转语音输出 里安装/切换
        「Google 文字转语音」或「讯飞语音+」，装好后回到本页刷新。
      </div>`
      return
    }
    const en = list.filter((v) => langOf(v).toLowerCase().startsWith('en'))
    const other = list.filter((v) => !langOf(v).toLowerCase().startsWith('en'))
    voicesEl.innerHTML = `
      ${en.length ? `<h2 class="tts-group">英语声音（推荐用这些）</h2>
      <div class="tts-list">${en.map((v) => voiceRow(v, v.voiceURI === selectedUri)).join('')}</div>` : ''}
      <h2 class="tts-group">其他语言（${other.length}）</h2>
      <div class="tts-list">${other.map((v) => voiceRow(v, v.voiceURI === selectedUri)).join('')}</div>`
  }

  const cur = getTtsPref().voiceURI || ''
  renderVoices(voices, cur)
  statusEl.textContent = voices.length
    ? `本设备共 ${voices.length} 个音色（其中英语 ${voices.filter((v) => langOf(v).toLowerCase().startsWith('en')).length} 个）。点一条即设为默认。`
    : '未取到音色列表。'

  // 事件委托：整行＝选中默认；🔊＝只试听
  voicesEl.addEventListener('click', (e) => {
    const test = e.target.closest('[data-test]')
    if (test) {
      e.stopPropagation()
      const v = voices.find((x) => x.voiceURI === test.dataset.test)
      if (!v) return
      try {
        window.speechSynthesis.cancel()
        const u = new SpeechSynthesisUtterance(SAMPLE)
        u.voice = v
        u.lang = v.lang
        u.rate = Number(rateInput.value) || 0.9
        window.speechSynthesis.speak(u)
      } catch { /* 静默 */ }
      return
    }
    const row = e.target.closest('.tts-voice')
    if (!row) return
    setTtsPref({ voiceURI: row.dataset.uri })
    renderVoices(voices, row.dataset.uri)
    statusEl.textContent = '已设为默认 ✓ 查词和朗读会用这个声音。'
    speak(SAMPLE)
  })
}

// 阅读练习排版设置（隐藏页 #/readconf，不进 Tab）：正文字号 / 栏宽，
// localStorage 本设备生效。与 #/ttsconf 同款隐藏配置页交互。
import { getReadPref, setReadPref, resetReadPref } from '../readpref.js'

export async function mountReadConf(el) {
  el.innerHTML = `
  <div class="ttsconf-page">
    <button class="fce-back-btn" id="rdc-back">← 返回</button>
    <div class="tts-hero" style="background:linear-gradient(140deg,#54452f 0%,#6d5a3a 50%,#8a6d3b 100%)">
      <p class="eyebrow">和爸学 · 隐藏设置</p>
      <h1>📖 阅读排版</h1>
      <p class="sub">调整「阅读练习」正文的大小和每行宽度。只影响阅读练习的文章页，保存在这台设备上。</p>
    </div>
    <div class="tts-box">
      <div class="rdc-row">
        <label for="rdc-fs">字号</label>
        <input type="range" id="rdc-fs" min="15" max="28" step="1">
        <b id="rdc-fs-val"></b>
      </div>
      <div class="rdc-preview" id="rdc-preview">
        <p>The little boy opened his eyes and saw the sea for the first time. Waves rolled in, one after another, under the morning sun.</p>
      </div>
    </div>
    <div class="tts-box">
      <div class="rdc-row">
        <label for="rdc-w">每行宽度</label>
        <input type="range" id="rdc-w" min="480" max="980" step="20">
        <b id="rdc-w-val"></b>
      </div>
      <div class="rdc-widthbar"><i id="rdc-widthbar-i"></i></div>
    </div>
    <div class="tts-box tts-note">
      太宽的行读着容易串行，推荐 560~680；字号推荐 18~22。
      <button class="tts-mini-btn" id="rdc-reset" type="button">恢复默认</button>
    </div>
  </div>`

  el.querySelector('#rdc-back').addEventListener('click', () => {
    if (history.length > 1) history.back()
    else location.hash = '/recite'
  })

  const fsInput = el.querySelector('#rdc-fs')
  const fsVal = el.querySelector('#rdc-fs-val')
  const wInput = el.querySelector('#rdc-w')
  const wVal = el.querySelector('#rdc-w-val')
  const preview = el.querySelector('#rdc-preview')
  const wBar = el.querySelector('#rdc-widthbar-i')

  function refresh() {
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
    refresh()
  })
  wInput.addEventListener('input', () => {
    setReadPref({ width: Number(wInput.value) })
    refresh()
  })
  el.querySelector('#rdc-reset').addEventListener('click', () => {
    resetReadPref()
    refresh()
  })

  refresh()
}

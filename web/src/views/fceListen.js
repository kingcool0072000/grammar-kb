import { api } from '../api.js'
import { escapeHtml } from '../render.js'
import { createSpectrumPlayer } from './fceAudio.js'

// 教师版 · FCE 听力播放台（独立页，备课用）：
// 4 Test × 4 Part 全部音频一处播放——长进度条（点击/拖拽跳转）+ 实时频谱
//（平坦段 = 空白时间，方便快速定位读题/停顿位置）。
// 学生不进此页（路由 teacher 限定）；做题时的听力播放器在练习页内嵌。
const PART_CN = { 1: 'Part 1 · 八选一图片', 2: 'Part 2 · 句子填空', 3: 'Part 3 · 五选一', 4: 'Part 4 · 三选一' }

export async function mountFceListen(el) {
  el.innerHTML = '<div class="view-head"><h1>FCE 听力</h1><p>加载中…</p></div>'
  const tests = [1, 2, 3, 4]
  const statusMap = {}
  await Promise.all(tests.map(async (t) => {
    try { statusMap[t] = (await api.fceAudioStatus(t)).available || [] }
    catch { statusMap[t] = [] }
  }))

  const totalReady = tests.reduce((s, t) => s + statusMap[t].length, 0)
  el.innerHTML = `
    <div class="view-head">
      <h1>FCE 听力</h1>
      <p>4 套 × 4 部分共 ${totalReady}/16 段音频就绪。频谱平坦段 = 空白时间，点击/拖动进度条跳转。</p>
    </div>
    <div class="flm-grid">
      ${tests.map((t) => `
        <section class="flm-test">
          <div class="flm-test-head">Test ${t}
            <span class="flm-test-n">${statusMap[t].length}/4 就绪</span>
          </div>
          ${[1, 2, 3, 4].map((p) => {
            const key = `listening_p${p}`
            const ready = statusMap[t].includes(key)
            return ready
              ? `<div class="flm-part" data-test="${t}" data-part="${p}">
                   <button class="flm-part-btn" data-load="${t}/${p}">
                     ${PART_CN[p] || `Part ${p}`}
                   </button>
                 </div>`
              : `<div class="flm-part flm-part-missing">${PART_CN[p] || `Part ${p}`}<i>音频未就绪</i></div>`
          }).join('')}
        </section>`).join('')}
    </div>
    <div class="flm-stage" id="flm-stage" hidden>
      <div class="flm-stage-head">
        <b id="flm-stage-title">Test 1 · Part 1</b>
        <button class="reading-btn small" id="flm-close">收起播放器</button>
      </div>
      <div id="flm-host"></div>
    </div>
  `

  const stage = el.querySelector('#flm-stage')
  const host = el.querySelector('#flm-host')
  const titleEl = el.querySelector('#flm-stage-title')
  let player = null
  let currentKey = ''

  el.querySelectorAll('[data-load]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const [t, p] = btn.dataset.load.split('/').map(Number)
      const key = `${t}/${p}`
      stage.hidden = false
      if (player && currentKey !== key) { player.destroy(); player = null }
      if (!player) {
        titleEl.textContent = `Test ${t} · ${PART_CN[p] || 'Part ' + p}`
        player = createSpectrumPlayer(host, `/api/fce-papers/${t}/audio/listening_p${p}`)
        currentKey = key
      }
      stage.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  })
  el.querySelector('#flm-close').addEventListener('click', () => {
    if (player) { player.destroy(); player = null; currentKey = '' }
    stage.hidden = true
  })
}

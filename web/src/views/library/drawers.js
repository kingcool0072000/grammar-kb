// 泛读馆阅读器 · 三个抽屉：目录（左）/ Aa 主题（右）/ 预习（右）。
// 直译自 FCEReadingLib client/src/components/reader/{TocDrawer,ThemeDrawer,PrepDrawer}.tsx。
// 抽屉开关：transform translateX 过渡 0.25s + 遮罩淡入淡出。

import { api } from '../../api.js'
import { escapeHtml, escAttr } from '../../render.js'
import { speak } from './lookup.js'
import { ICONS } from './icons.js'

// 主题预设与字体栈由 reader.js 使用；抽屉面板自己也需展示，这里同源照抄 types/index.ts
export const THEME_PRESETS = {
  paper: { bg: '#FFFFFF', fg: '#1F2933', label: '纸白' },
  sepia: { bg: '#F5ECD9', fg: '#5B4636', label: '护眼米黄' },
  night: { bg: '#22272E', fg: '#C9CDD3', label: '夜间' },
  contrast: { bg: '#000000', fg: '#FFFFFF', label: '高对比' },
}
export const FONT_STACKS = {
  serif: "Georgia, 'Iowan Old Style', 'Times New Roman', serif",
  sans: "-apple-system, 'Helvetica Neue', Arial, 'PingFang SC', sans-serif",
  kai: "'Kaiti SC', 'STKaiti', KaiTi, Georgia, serif",
}

/** 抽屉骨架：wrapper（无样式容器）内含 mask + aside；open/close 切 class 走 CSS 过渡 */
function baseDrawer({ side, title, ariaLabel, onClose }) {
  const el = document.createElement('div')
  const mask = document.createElement('div')
  mask.className = 'lib-mask'
  const aside = document.createElement('aside')
  aside.className = `lib-drawer ${side}`
  aside.setAttribute('role', 'dialog')
  aside.setAttribute('aria-label', ariaLabel)
  aside.innerHTML = `
    <div class="lib-drawer-head">
      <h3>${escapeHtml(title)}</h3>
      <button type="button" class="lib-close" aria-label="关闭">✕</button>
    </div>
    <div class="lib-drawer-body"></div>
  `
  el.append(mask, aside)
  const body = aside.querySelector('.lib-drawer-body')

  const close = () => {
    mask.classList.remove('open')
    aside.classList.remove('open')
  }
  const open = () => {
    mask.classList.add('open')
    aside.classList.add('open')
  }
  mask.addEventListener('click', () => {
    close()
    if (onClose) onClose()
  })
  aside.querySelector('.lib-close').addEventListener('click', () => {
    close()
    if (onClose) onClose()
  })
  aside.addEventListener('click', (e) => e.stopPropagation())

  return { el, aside, body, open, close, isOpen: () => aside.classList.contains('open') }
}

// ---------------- 目录抽屉（左） ----------------

/** 当前 spine 下标所属合并章（spineIndex 不大于当前的最后一章） */
function activeChapterIdx(sorted, currentSpineIndex) {
  let active = sorted.length ? sorted[0].idx : null
  for (const c of sorted) {
    if (c.spineIndex <= currentSpineIndex) active = c.idx
    else break
  }
  return active
}

export function createTocDrawer({ onGoto, onClose }) {
  const d = baseDrawer({ side: 'left', title: '目录', ariaLabel: '目录', onClose })

  function renderList(chapters, currentSpineIndex) {
    const sorted = [...(chapters || [])].sort((a, b) => a.spineIndex - b.spineIndex)
    if (!sorted.length) {
      d.body.innerHTML = '<p class="lib-muted">目录加载中…</p>'
      return
    }
    const active = activeChapterIdx(sorted, currentSpineIndex)
    d.body.innerHTML = sorted
      .map(
        (c, i) => `
        <button type="button" class="lib-toc-item${c.idx === active ? ' active' : ''}" data-spine="${c.spineIndex}">
          <span class="t">${escapeHtml(c.title || `第 ${i + 1} 节`)}</span>
          ${c.prepped ? `<span class="prep-dot" title="已生成预习">${ICONS.headphones(14)}</span>` : ''}
        </button>`,
      )
      .join('')
    d.body.querySelectorAll('.lib-toc-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (onGoto) onGoto(Number(btn.dataset.spine))
        d.close()
      })
    })
  }

  return {
    el: d.el,
    open(chapters, currentSpineIndex) {
      renderList(chapters, currentSpineIndex)
      d.open()
    },
    close: d.close,
    isOpen: d.isOpen,
  }
}

// ---------------- Aa 主题抽屉（右） ----------------

export function createThemeDrawer({ isTeacher, getTheme, onChange, onClose }) {
  const d = baseDrawer({
    side: 'right',
    title: isTeacher ? '阅读主题' : '字号',
    ariaLabel: '阅读主题',
    onClose,
  })

  if (isTeacher) {
    d.body.innerHTML = `
      <div class="lib-theme-preview">
        <p>The old envelope arrived on a rainy Tuesday, smudged with mud and smelling faintly of vanilla.</p>
      </div>
      <div class="lib-theme-section">
        <h4>预设配色</h4>
        <div class="lib-preset-row">
          ${Object.entries(THEME_PRESETS)
            .map(
              ([key, p]) => `
            <button type="button" class="lib-preset-card" data-preset="${key}">
              <span class="swatch" style="background:${p.bg};color:${p.fg}">Aa</span>
              ${escapeHtml(p.label)}
            </button>`,
            )
            .join('')}
        </div>
      </div>
      <div class="lib-theme-section">
        <h4>自定义颜色</h4>
        <div class="lib-custom-colors">
          <label>背景色<input type="color" data-color="bg" /></label>
          <label>文字颜色<input type="color" data-color="fg" /></label>
        </div>
      </div>
      <div class="lib-theme-section">
        <h4>字体</h4>
        <div class="lib-seg" data-seg="fontFamily">
          <button type="button" data-val="serif">衬线</button>
          <button type="button" data-val="sans">无衬线</button>
          <button type="button" data-val="kai">楷体</button>
        </div>
      </div>
      <div class="lib-theme-section">
        <h4>字号</h4>
        <div class="lib-slider-row">
          <input type="range" min="14" max="28" step="1" data-slider aria-label="字号" />
          <output></output>
        </div>
      </div>
    `
    d.body.querySelectorAll('.lib-preset-card').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.preset
        const p = THEME_PRESETS[key]
        if (!p) return
        onChange({ preset: key, bg: p.bg, fg: p.fg })
        syncUI()
      })
    })
    d.body.querySelectorAll('input[type="color"]').forEach((inp) => {
      inp.addEventListener('input', () => {
        onChange({ preset: 'custom', [inp.dataset.color]: inp.value })
        syncUI()
      })
    })
    d.body.querySelectorAll('.lib-seg button').forEach((btn) => {
      btn.addEventListener('click', () => {
        onChange({ fontFamily: btn.dataset.val })
        syncUI()
      })
    })
  } else {
    // 学生：配色与字体由教师统一配置，面板只留两档字号（标准 23 / 大 28）
    d.body.innerHTML = `
      <div class="lib-theme-section">
        <div class="lib-seg" data-seg="fontSize">
          <button type="button" data-val="23">标准</button>
          <button type="button" data-val="28">大</button>
        </div>
      </div>
    `
    d.body.querySelectorAll('.lib-seg[data-seg="fontSize"] button').forEach((btn) => {
      btn.addEventListener('click', () => {
        onChange({ fontSize: Number(btn.dataset.val) })
        syncUI()
      })
    })
  }

  const slider = d.body.querySelector('[data-slider]')
  const output = d.body.querySelector('.lib-slider-row output')
  if (slider)
  slider.addEventListener('input', () => {
    const v = Number(slider.value)
    onChange({ fontSize: v })
    setSlider(v)
    // 预览段字号实时跟随（不整段 syncUI，避免打断拖动）
    if (isTeacher) {
      const preview = d.body.querySelector('.lib-theme-preview')
      preview.style.fontSize = `${v}px`
    }
  })

  function setSlider(v) {
    if (Number(slider.value) !== v) slider.value = String(v)
    output.textContent = String(v)
    // 滑杆填充：内联 --fill 配 CSS 渐变轨道（webkit/moz）
    slider.style.setProperty('--fill', `${((v - 14) / 14) * 100}%`)
  }

  /** 面板内部状态跟随最新主题（就地更新，不打断滑杆拖动） */
  function syncUI() {
    const t = getTheme() || {}
    if (!isTeacher) {
      const fs = Number(t.fontSize) >= 26 ? 28 : 23
      d.body.querySelectorAll('.lib-seg[data-seg="fontSize"] button').forEach((btn) => {
        btn.classList.toggle('active', Number(btn.dataset.val) === fs)
      })
      return
    }
    if (isTeacher) {
      const preview = d.body.querySelector('.lib-theme-preview')
      preview.style.background = t.bg
      preview.style.color = t.fg
      preview.style.fontFamily = FONT_STACKS[t.fontFamily] || FONT_STACKS.serif
      preview.style.fontSize = `${t.fontSize}px`
      preview.style.lineHeight = String(t.lineHeight || 1.9)
      d.body.querySelectorAll('.lib-preset-card').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.preset === t.preset)
      })
      const bgInput = d.body.querySelector('input[data-color="bg"]')
      const fgInput = d.body.querySelector('input[data-color="fg"]')
      if (/^#[0-9a-fA-F]{6}$/.test(t.bg || '')) bgInput.value = t.bg
      if (/^#[0-9a-fA-F]{6}$/.test(t.fg || '')) fgInput.value = t.fg
      d.body.querySelectorAll('.lib-seg button').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.val === t.fontFamily)
      })
    }
    if (isTeacher) setSlider(Math.min(28, Math.max(14, Number(t.fontSize) || 23)))
  }

  return {
    el: d.el,
    open() {
      syncUI()
      d.open()
    },
    close: d.close,
    isOpen: d.isOpen,
    syncUI,
  }
}

// ---------------- 预习抽屉（右） ----------------

export function createPrepDrawer({ onClose, onStart }) {
  const d = baseDrawer({
    side: 'right',
    title: '课前预习',
    ariaLabel: '课前预习',
    onClose,
  })

  function showLoading() {
    d.body.innerHTML = '<div class="lib-prep-loading"><span class="lib-spinner"></span></div>'
  }

  function render(bookId, chapterIndex, chapterTitle, prep) {
    const title = chapterTitle || `第 ${chapterIndex + 1} 节`
    let inner = `<p class="lib-muted lib-prep-chapter">${escapeHtml(title)}</p>`
    const words = (prep && prep.words) || []
    const idioms = (prep && prep.idioms) || []

    if (words.length) {
      inner += `<div class="lib-prep-group-title">困难单词 · ${words.length}</div>`
      inner += words
        .map((w) => {
          const ph = w.phonetic ? `<span class="ph">${escapeHtml(w.phonetic)}</span>` : ''
          const pos = w.pos ? `<span class="pos">${escapeHtml(w.pos)}</span>` : ''
          const ex = w.example
            ? `<div class="lib-prep-example">${escapeHtml(w.example)}${
                w.exampleZh ? `<span class="zh">${escapeHtml(w.exampleZh)}</span>` : ''
              }</div>`
            : ''
          return `
            <div class="lib-prep-word">
              <div class="lib-prep-word-head">
                <span class="w">${escapeHtml(w.word)}</span>${ph}${pos}
                <button type="button" class="lib-prep-sound" data-tts="${escAttr(w.word)}" title="发音" aria-label="发音 ${escAttr(w.word)}">${ICONS.sound(16)}</button>
              </div>
              <div class="lib-prep-meaning">${escapeHtml(w.meaning || '')}</div>
              ${ex}
            </div>`
        })
        .join('')
    }

    if (idioms.length) {
      inner += `<div class="lib-prep-group-title">俚语 · 习语 · ${idioms.length}</div>`
      inner += idioms
        .map((p) => {
          const usage = p.usage ? `<div class="line usage">${escapeHtml(p.usage)}</div>` : ''
          return `
            <div class="lib-prep-idiom">
              <div class="lib-prep-word-head">
                <span class="p">${escapeHtml(p.phrase)}</span>
                <button type="button" class="lib-prep-sound accent" data-tts="${escAttr(p.phrase)}" title="发音" aria-label="发音 ${escAttr(p.phrase)}">${ICONS.sound(16)}</button>
              </div>
              <div class="line">字面：<b>${escapeHtml(p.literal || '')}</b></div>
              <div class="line">实义：<b>${escapeHtml(p.actual || '')}</b></div>
              ${usage}
            </div>`
        })
        .join('')
    }

    if (!words.length && !idioms.length && prep) {
      inner += `
        <div class="lib-prep-empty">
          <div class="ico">🎊</div>
          <h3>这一章很轻松</h3>
          <p>没有需要提前学习的难词和俚语</p>
        </div>`
    }

    inner += `<button type="button" class="lib-prep-start">已预习完，继续阅读 →</button>`
    d.body.innerHTML = inner

    d.body.querySelectorAll('[data-tts]').forEach((btn) => {
      btn.addEventListener('click', () => speak(btn.dataset.tts))
    })
    d.body.querySelector('.lib-prep-start').addEventListener('click', () => {
      d.close()
      if (onStart) onStart()
    })
  }

  function renderNotFound(chapterIndex) {
    d.body.innerHTML = `
      <p class="lib-muted lib-prep-chapter">第 ${chapterIndex + 1} 节</p>
      <div class="lib-prep-empty">
        <div class="ico">${ICONS.seedling(40)}</div>
        <h3>本章还没有预习</h3>
        <p>请老师在书籍的「章节预习管理」页为这一章生成预习</p>
      </div>
      <button type="button" class="lib-prep-start">已预习完，继续阅读 →</button>`
    const btn = d.body.querySelector('.lib-prep-start')
    if (btn) {
      btn.addEventListener('click', () => {
        d.close()
        if (onStart) onStart()
      })
    }
  }

  function renderError(msg) {
    d.body.innerHTML = `<div class="lib-prep-empty"><div class="ico">${ICONS.warn(40)}</div><h3>加载预习失败</h3><p>${escapeHtml(msg)}</p></div>`
  }

  let fetchSeq = 0
  function open(bookId, chapterIndex, chapterTitle) {
    fetchSeq += 1
    const seq = fetchSeq
    showLoading()
    d.open()
    api
      .libraryPrep(bookId, chapterIndex)
      .then((prep) => {
        if (seq !== fetchSeq || !d.isOpen()) return
        if (prep) render(bookId, chapterIndex, chapterTitle, prep)
        else renderNotFound(chapterIndex)
      })
      .catch((e) => {
        if (seq !== fetchSeq || !d.isOpen()) return
        const msg = (e && e.message) || '加载预习失败'
        if (msg.includes('尚未生成')) renderNotFound(chapterIndex)
        else renderError(msg)
      })
  }

  return { el: d.el, open, close: d.close, isOpen: d.isOpen }
}

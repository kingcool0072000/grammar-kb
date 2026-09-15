// 泛读馆阅读器 · 页面装配（FCEReadingLib client/src/pages/ReaderPage.tsx 的原生 JS 直译）。
// 全屏 fixed 容器（盖住 app header/main，沉浸式）；hash 离开 libraryReader 路由时自杀清理。
// 加载序：并行 [epub blob → ePub(buf)] 与 [chapters / POST open / GET 云端进度→写 localStorage]，
// 云端进度写完后再建 rendition 恢复，避免竞态。

import './reader.css'
import { api, getAuth } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { createEpubRenderer } from './epubcore.js'
import { createLookupLayer } from './lookup.js'
import { ICONS } from './icons.js'
import { createTocDrawer, createThemeDrawer, createPrepDrawer, THEME_PRESETS, FONT_STACKS } from './drawers.js'
import { createFocusTracker } from './focus.js'

const DEFAULT_THEME = {
  preset: 'paper',
  bg: '#FFFFFF',
  fg: '#1F2933',
  fontFamily: 'serif',
  fontSize: 19,
  lineHeight: 1.9,
  autoPrep: true,
}

function themeKey() {
  const a = getAuth()
  return `gkb-lib-theme-${(a && a.user) || 'anon'}`
}

function loadTheme() {
  let saved = {}
  try {
    saved = JSON.parse(localStorage.getItem(themeKey())) || {}
  } catch {
    saved = {}
  }
  const t = { ...DEFAULT_THEME, ...saved }
  t.fontSize = Math.min(28, Math.max(14, Math.round(Number(t.fontSize) || DEFAULT_THEME.fontSize)))
  t.lineHeight = DEFAULT_THEME.lineHeight // 固定 1.9
  t.autoPrep = saved.autoPrep !== undefined ? !!saved.autoPrep : true
  if (THEME_PRESETS[t.preset]) {
    // 命中预设：颜色以预设为准（防止存量数据 bg/fg 与 preset 不一致）
    t.bg = THEME_PRESETS[t.preset].bg
    t.fg = THEME_PRESETS[t.preset].fg
  }
  if (!FONT_STACKS[t.fontFamily]) t.fontFamily = DEFAULT_THEME.fontFamily
  return t
}

function saveTheme(t) {
  try {
    localStorage.setItem(themeKey(), JSON.stringify(t))
  } catch {
    /* 配额满则静默 */
  }
}

function routeName() {
  return ((location.hash.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean))[0] || '')
}

const ICON_BACK =
  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>'
const ICON_LIST =
  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></svg>'

export function mountLibraryReader(viewEl, ctx) {
  const role = (ctx && ctx.role) || (getAuth() && getAuth().role) || 'teacher'
  const isTeacher = role === 'teacher'
  const bookId = Number(ctx && ctx.bookId)
  let cleaned = false

  const root = document.createElement('div')
  root.className = 'lib-reader'
  root.innerHTML = `
    <header class="lib-topbar">
      <button type="button" class="lib-icon-btn" data-act="back" aria-label="返回书架" title="返回书架">${ICON_BACK}</button>
      <div class="lib-title"><span class="s"></span></div>
      <button type="button" class="lib-accent-btn" data-act="prep" title="查看本章预习">${ICONS.headphones(16)} 预习</button>
      <button type="button" class="lib-icon-btn" data-act="toc" aria-label="目录" title="目录">${ICON_LIST}</button>
      <button type="button" class="lib-icon-btn" data-act="theme" aria-label="排版设置" title="排版设置">Aa</button>
    </header>
    <div class="lib-body">
      <div class="lib-loading"><span class="lib-spinner"></span><p>正在打开书籍…</p></div>
      <div id="epub-viewer"></div>
      <!-- 视线引导带：屏幕上部 30% 常驻柔和遮罩，让孩子视线落在中段 -->
      <div class="lib-gaze-band" aria-hidden="true"></div>
    </div>
  `
  viewEl.innerHTML = ''
  viewEl.appendChild(root)

  const bodyEl = root.querySelector('.lib-body')
  const viewerEl = root.querySelector('#epub-viewer')
  const loadingEl = root.querySelector('.lib-loading')
  const labelEl = root.querySelector('.lib-title .s')

  // ---- 生命周期：mount 时先注册自杀监听（route 不再是 libraryReader → cleanup）----
  function onHashChange() {
    if (routeName() !== 'libraryReader') cleanup()
  }
  window.addEventListener('hashchange', onHashChange)

  // ---- 状态 ----
  let chapters = []
  let sortedChapters = []
  let chapterIndex = 0 // spine 下标（epub.js relocated）
  let chapterLabel = ''
  let theme = loadTheme()
  let studentOverride = null // 学生：教师配置的 preset/fontFamily
  let renderer = null
  let lastAutoPrepChapter = -1
  let prepOpenChapterIdx = null
  const closedPrepChapters = new Set()

  // 「已预习完」章集合：按书持久化（localStorage），点过按钮的章跨会话不再自动弹预习
  const PREPPED_KEY = `gkb-lib-prepped-${getAuth() && getAuth().user}-${bookId}`
  function loadPreppedSet() {
    try {
      const arr = JSON.parse(localStorage.getItem(PREPPED_KEY) || '[]')
      return new Set(Array.isArray(arr) ? arr.map(Number).filter(Number.isFinite) : [])
    } catch {
      return new Set()
    }
  }
  let preppedDoneSet = loadPreppedSet()
  function markPreppedDone(chapterIdx) {
    if (chapterIdx == null || preppedDoneSet.has(chapterIdx)) return
    preppedDoneSet.add(chapterIdx)
    try {
      localStorage.setItem(PREPPED_KEY, JSON.stringify([...preppedDoneSet]))
    } catch {
      /* 配额满则跳过持久化（内存态仍生效） */
    }
  }

  // ---- 进度上报（节流）----
  const progress = { cfi: '', chapterIndex: 0, percent: 0 }
  const reported = { cfi: '', time: 0 }
  let secondsAccum = 0
  let lastTick = Date.now()

  function reportProgress(force = false) {
    if (!Number.isFinite(bookId)) return
    const now = Date.now()
    if (!progress.cfi) return
    if (!force && progress.cfi === reported.cfi && now - reported.time < 5000) return
    reported.cfi = progress.cfi
    reported.time = now
    const seconds = Math.round(secondsAccum / 1000)
    secondsAccum = 0
    api
      .libraryPutProgress(bookId, {
        cfi: progress.cfi,
        chapterIndex: progress.chapterIndex,
        percent: progress.percent,
        readingSecondsDelta: seconds,
      })
      .catch(() => {})
  }

  // 阅读时长累计（页面可见时），1s interval
  const timer = setInterval(() => {
    if (document.visibilityState === 'visible') secondsAccum += Date.now() - lastTick
    lastTick = Date.now()
  }, 1000)
  function onBeforeUnload() {
    reportProgress(true)
  }
  window.addEventListener('beforeunload', onBeforeUnload)

  // ---- 合并章：spine 下标 → 所属章（spineIndex 是每章首个 spine 项，取不大于它的最大值）----
  function mergedChapterAt(spineIdx) {
    let cur = sortedChapters[0]
    for (const c of sortedChapters) {
      if (c.spineIndex <= spineIdx) cur = c
      else break
    }
    return cur
  }

  // ---- 主题 ----
  function effectiveTheme() {
    const t = { ...theme }
    if (!isTeacher && studentOverride && studentOverride.preset && THEME_PRESETS[studentOverride.preset]) {
      t.preset = studentOverride.preset
      t.bg = THEME_PRESETS[studentOverride.preset].bg
      t.fg = THEME_PRESETS[studentOverride.preset].fg
      if (studentOverride.fontFamily && FONT_STACKS[studentOverride.fontFamily]) {
        t.fontFamily = studentOverride.fontFamily
      }
    }
    return t
  }

  function themeStyleNow() {
    const t = effectiveTheme()
    return {
      bg: t.bg,
      fg: t.fg,
      fontFamily: FONT_STACKS[t.fontFamily] || FONT_STACKS.serif,
      fontSize: t.fontSize,
      lineHeight: t.lineHeight,
    }
  }

  function applyThemeNow() {
    const t = effectiveTheme()
    bodyEl.style.background = t.bg
    // 外壳（顶栏/抽屉/遮罩）跟随主题：夜间/高对比时同步换深浅面色与文字色。
    // 变量必须挂在 root（.lib-reader）——顶栏是 bodyEl 的兄弟、抽屉挂在 root 下，
    // 挂 bodyEl 的话变量传不到它们（复走查 B2）
    root.style.setProperty('--lib-shell-bg', t.bg)
    root.style.setProperty('--lib-shell-fg', t.fg)
    root.style.setProperty(
      '--lib-shell-border',
      t.fg === '#C9CDD3' || t.fg === '#FFFFFF' ? 'rgba(255,255,255,0.16)' : 'var(--lib-border)',
    )
    // 视线引导带配色切换：夜间/高对比用纯黑墨（data-theme-dark 选择器）
    const dark = t.fg === '#C9CDD3' || t.fg === '#FFFFFF'
    if (dark) root.setAttribute('data-theme-dark', '')
    else root.removeAttribute('data-theme-dark')
    if (renderer) renderer.applyTheme(themeStyleNow())
  }

  function patchTheme(patch) {
    theme = { ...theme, ...patch }
    theme.fontSize = Math.min(28, Math.max(14, Math.round(Number(theme.fontSize) || 19)))
    saveTheme(theme)
    applyThemeNow()
    focusTracker.hooks.onThemeAdjust()
  }

  // ---- 专注力采集（教师自己读书不追踪；学生开关未到前先采，确认关闭后 disable）----
  const focusTracker = createFocusTracker({
    readerRoot: root,
    viewerEl,
    bookId,
    bookTitle: '',
    enabled: !isTeacher,
  })

  // 学生账号：配色与字体由教师统一配置（字号仍可自调）
  if (!isTeacher) {
    api
      .librarySettings()
      .then((s) => {
        if (cleaned) return
        const stu = s && s.student
        if (stu && (stu.preset || stu.fontFamily)) {
          studentOverride = stu
          applyThemeNow()
        }
        // 专注力开关：focus !== false 才采集（默认开）
        if (stu && stu.focus === false) focusTracker.disable()
      })
      .catch(() => {})
  }

  // 首帧即应用主题（阅读主体底色与 epub 主题一致；rendition 未建时只记 currentTheme）
  applyThemeNow()

  // ---- 划词/查词浮层（onLookup/onSpeak 喂给专注力采集）----
  const lookup = createLookupLayer(bodyEl, {
    onLookup: focusTracker.hooks.onLookup,
    onSpeak: focusTracker.hooks.onSpeak,
  })
  // 点击阅读主体空白处清浮层（浮层自身 stopPropagation）
  bodyEl.addEventListener('click', () => lookup.hideAll())

  // ---- 抽屉 ----
  const tocDrawer = createTocDrawer({
    onGoto: gotoChapter,
  })
  const themeDrawer = createThemeDrawer({
    isTeacher,
    getTheme: () => theme,
    onChange: patchTheme,
  })
  const prepDrawer = createPrepDrawer({
    onClose: () => {
      // 手动关闭过的章本次会话不再自动弹
      if (prepOpenChapterIdx != null) closedPrepChapters.add(prepOpenChapterIdx)
    },
    onStart: () => {
      // 点「已预习完」：记录该章（跨会话不再自动弹），下次打开直达记忆位置
      markPreppedDone(prepOpenChapterIdx)
      focusTracker.hooks.onPrepStart()
    },
  })
  root.append(tocDrawer.el, themeDrawer.el, prepDrawer.el)

  function openPrep(idx, label) {
    prepOpenChapterIdx = idx
    focusTracker.hooks.onPrepOpen()
    prepDrawer.open(bookId, idx, label)
  }

  // ---- 顶栏事件 ----
  root.querySelector('.lib-topbar').addEventListener('click', (e) => {
    const btn = e.target.closest('button')
    if (!btn) return
    const act = btn.dataset.act
    if (act === 'back') {
      location.hash = '/library'
    } else if (act === 'toc') {
      tocDrawer.open(chapters, chapterIndex)
    } else if (act === 'theme') {
      themeDrawer.open()
    } else if (act === 'prep') {
      const merged = mergedChapterAt(chapterIndex)
      openPrep(merged ? merged.idx : chapterIndex, chapterLabel)
    }
  })

  // ---- epub 事件 ----
  function handleReloc(info) {
    progress.cfi = info.cfi
    progress.chapterIndex = info.chapterIndex
    progress.percent = info.percent
    focusTracker.hooks.onReloc(info.percent)
    chapterIndex = info.chapterIndex
    const merged = mergedChapterAt(chapterIndex)
    chapterLabel = info.chapterTitle || (merged && merged.title) || ''
    labelEl.textContent = `${chapterLabel}${info.percent > 0 ? ` · ${Math.round(info.percent)}%` : ''}`
    try {
      localStorage.setItem(`gkb-lib-cfi-${bookId}`, info.cfi || '')
    } catch {
      /* ignore */
    }
    // 进入新合并章：自动弹预习（若已生成、偏好开启、未点过「已预习完」且本次会话未手动关过）
    if (merged && merged.idx !== lastAutoPrepChapter) {
      lastAutoPrepChapter = merged.idx
      if (theme.autoPrep && merged.prepped && !preppedDoneSet.has(merged.idx) && !closedPrepChapters.has(merged.idx)) {
        openPrep(merged.idx, chapterLabel)
      }
    }
    reportProgress(false)
  }

  function handleSelect(payload) {
    if (!payload || !payload.word) {
      lookup.hideAll()
      return
    }
    lookup.showSelection(payload)
  }

  /** 每章渲染时取当前章的上下章信息（iframe 内章导航链接注入用） */
  function onInjectChapterLinks() {
    const cur = mergedChapterAt(chapterIndex)
    if (!cur) return null
    const pos = sortedChapters.findIndex((c) => c.idx === cur.idx)
    if (pos < 0) return null
    const prev = pos > 0 ? { spineIndex: sortedChapters[pos - 1].spineIndex } : null
    const next = pos < sortedChapters.length - 1 ? { spineIndex: sortedChapters[pos + 1].spineIndex } : null
    return { prev, next }
  }

  /** iframe 正文内点击空白：清掉划词/查词浮层（章链接注入按钮已 stopPropagation） */
  function onRendered(contents) {
    const doc = contents && contents.document
    if (!doc) return
    try {
      doc.addEventListener('click', () => lookup.hideAll())
      // 专注模式：iframe 内右键也拦（主文档的全局拦截够不到这里；走查 M1）
      doc.addEventListener('contextmenu', (e) => e.preventDefault())
      // 选区塌缩要作用到 iframe 的 window（lookup 层默认只清主文档；走查 M2）
      lookup.setSelectionWindow(contents.window || doc.defaultView)
      // 专注力：iframe 正文内鼠标轨迹（幂等，重复渲染不重挂）
      focusTracker.hooks.attachIframeDoc(doc)
    } catch {
      /* ignore */
    }
  }

  // 章级导航：以合并章的 spineIndex 定位（后端 chapters 已按 spine 对齐）
  function gotoChapter(targetSpineIndex) {
    focusTracker.hooks.onChapterNav()
    if (!renderer) return
    const href = renderer.spineHref(targetSpineIndex)
    if (href) renderer.displayTarget(href)
  }

  // ---- 键盘：←/→ 翻章，Esc 关抽屉与浮层 ----
  function onKeyup(e) {
    if (e.key === 'ArrowRight') {
      if (e.target instanceof HTMLInputElement) return // 抽屉内滑杆/取色器的方向键不翻章
      if (renderer) renderer.goNext()
    } else if (e.key === 'ArrowLeft') {
      if (e.target instanceof HTMLInputElement) return
      if (renderer) renderer.goPrev()
    } else if (e.key === 'Escape') {
      tocDrawer.close()
      themeDrawer.close()
      prepDrawer.close()
      lookup.hideAll()
    }
  }
  window.addEventListener('keyup', onKeyup)

  // ---- resize：rendition 重测容器 ----
  let resizeTimer = null
  function onResize() {
    if (resizeTimer) clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => {
      if (cleaned || !renderer || !renderer.rendition) return
      try {
        renderer.rendition.resize()
      } catch {
        /* ignore */
      }
    }, 150)
  }
  window.addEventListener('resize', onResize)

  // ---- 错误态 ----
  function showError(err) {
    if (cleaned) return
    if (loadingEl && loadingEl.parentNode) loadingEl.remove()
    const errEl = document.createElement('div')
    errEl.className = 'lib-error'
    errEl.innerHTML = `
      <div class="ico">${ICONS.warn(40)}</div>
      <h3>无法打开这本书</h3>
      <p>${escapeHtml((err && err.message) || String(err || '加载失败'))}</p>
      <button type="button" class="lib-btn">返回书架</button>
    `
    errEl.querySelector('.lib-btn').addEventListener('click', () => {
      location.hash = '/library'
    })
    bodyEl.appendChild(errEl)
  }

  // ---- 加载序 ----
  // POST open + GET 云端进度（覆盖写 localStorage，等它 settle 后 epubcore 才建 rendition 恢复）
  const progressSync = (async () => {
    try {
      await api.libraryOpenBook(bookId)
    } catch {
      /* 打开记录失败不阻断阅读 */
    }
    try {
      const r = await api.libraryGetProgress(bookId)
      if (r && r.cfi) localStorage.setItem(`gkb-lib-cfi-${bookId}`, r.cfi)
    } catch {
      /* 云端进度失败按本地恢复 */
    }
  })()

  // chapters：晚到 300ms 后对当前帧补注入章导航链接（首帧渲染可能早于数据）
  api
    .libraryChapters(bookId)
    .then((r) => {
      if (cleaned) return
      chapters = (r && r.chapters) || []
      sortedChapters = [...chapters].sort((a, b) => a.spineIndex - b.spineIndex)
      setTimeout(() => {
        if (!cleaned && renderer) renderer.injectChapterLinksNow()
      }, 300)
    })
    .catch(() => {})

  renderer = createEpubRenderer(bookId, viewerEl, themeStyleNow(), {
    progressSync,
    onReloc: handleReloc,
    onSelect: handleSelect,
    onInjectChapterLinks,
    onChapterLinkClick: gotoChapter,
    onRendered,
  })

  renderer.ready.then(
    () => {
      if (cleaned) return
      if (loadingEl && loadingEl.parentNode) loadingEl.remove()
    },
    (e) => showError(e),
  )

  // ---- cleanup：hash 离开路由时由自杀监听触发 ----
  function cleanup() {
    if (cleaned) return
    cleaned = true
    try {
      reportProgress(true) // 强制 flush 进度与阅读时长
    } catch {
      /* ignore */
    }
    clearInterval(timer)
    if (resizeTimer) clearTimeout(resizeTimer)
    window.removeEventListener('hashchange', onHashChange)
    window.removeEventListener('keyup', onKeyup)
    window.removeEventListener('resize', onResize)
    window.removeEventListener('beforeunload', onBeforeUnload)
    try {
      if (renderer) renderer.destroy()
    } catch {
      /* ignore */
    }
    try {
      lookup.destroy()
    } catch {
      /* ignore */
    }
    try {
      focusTracker.destroy()
    } catch {
      /* ignore */
    }
    root.remove()
  }
}

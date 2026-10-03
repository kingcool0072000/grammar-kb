// 泛读馆阅读器 · epubjs 渲染核心（FCEReadingLib client/src/epub/useBook.ts 的原生 JS 直译）。
// 职责：加载 epub → 创建 rendition → 恢复进度 → relocated/selected/rendered 事件分发
// → 章导航链接注入 → themes 注入 → locations 进度精化。
// 所有 display/next/prev 经 navChain 序列化（单步 20s 超时），避免恢复进度与用户导航乱序覆盖。

import ePub from 'epubjs'
import { api } from '../../api.js'

/** 清洗 TOC 标签：去出版方编号前缀（"1: •"、"2    "）与项目符号 */
export function cleanTocLabel(label) {
  return String(label)
    .replace(/^\s*\d+\s*[:：]?\s*[•·\-–]?\s*/, '')
    .replace(/[•·]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

/**
 * 创建 epub 渲染器。
 * @param {number} bookId 书 id（用于拉文件与 localStorage 进度键）
 * @param {HTMLElement} container #epub-viewer 容器
 * @param {{bg:string,fg:string,fontFamily:string,fontSize:number,lineHeight:number}} themeStyle
 * @param callbacks {
 *   onReloc(info:{cfi,chapterIndex,percent,chapterTitle}),
 *   onSelect(payload:{text,word,context,x,y}),
 *   onInjectChapterLinks(): {prev:{spineIndex}|null, next:{spineIndex}|null}|null,
 *   onChapterLinkClick(targetSpineIndex),
 *   onRendered(contents),          // 每次 iframe 渲染完成（contents.document 为真实 iframe 文档）
 *   progressSync: Promise,         // 可选：云端进度恢复（写 localStorage）完成后再建 rendition
 * }
 * @returns {{book,rendition,toc,ready,error,goNext,goPrev,displayTarget,spineHref,injectChapterLinksNow,applyTheme,destroy}}
 */
export function createEpubRenderer(bookId, container, themeStyle, callbacks = {}) {
  let book = null
  let rendition = null
  let navChain = Promise.resolve()
  let destroyed = false
  let loadError = null
  let toc = []
  let chapterIndex = 0
  let percent = 0
  let injectHandler = null
  let currentTheme = themeStyle

  let readyResolve
  let readyReject
  /** rendition 创建成功时 resolve；加载失败时 reject（Error） */
  const ready = new Promise((res, rej) => {
    readyResolve = res
    readyReject = rej
  })

  let restoreResolve
  let restoreSettled = false // 落位稳定前插值 within 不可信（帧高未展开）
  /** 初始恢复落位完成时 resolve。两个条件：
   * ①display 完成 + 自愈滚动窗口过后（620ms）；
   * ②locations 就绪（或 3s 超时兜底）——locations 未生成时 relocated 用
   *   spine 比例兜底公式（(idx+0.5)/spine数），大书首章会算出 ~15% 的假
   *   百分比，落位前不开放采集以免假起点入档。 */
  const restored = new Promise((res) => {
    restoreResolve = () => {
      restoreSettled = true
      res()
    }
  })
  let locationsReadyResolve
  const locationsReady = new Promise((res) => {
    locationsReadyResolve = res
  })

  /** 序列化所有 display/next/prev：按调用顺序生效；单步 20s 超时防死锁 */
  function navigate(fn) {
    const p = navChain
      .then(() =>
        Promise.race([
          fn(),
          new Promise((_, rej) => setTimeout(() => rej(new Error('navigation timeout')), 20000)),
        ]),
      )
      .catch((e) => {
        console.warn('[epub] navigation failed:', e)
      })
    navChain = p
    return p
  }

  /** spine 比例兜底 / locations 精确百分比。分母必须数 spine（TOC 项数 ≠ spine 项数） */
  function computePercent(cfi, spineIndex) {
    if (book && book.locations && book.locations.length() && cfi) {
      const p = book.locations.percentageFromCfi(cfi)
      if (p && p > 0) return p * 100
    }
    const spine = book ? book.spine : null
    let total = 0
    if (spine && typeof spine.get === 'function') {
      for (let i = 0; ; i++) {
        if (!spine.get(i)) break
        total = i + 1
      }
    }
    if (!total) total = toc.length || 1
    return Math.min(100, ((spineIndex + 0.5) / total) * 100)
  }

  /** 以"渲染中的 href"在 TOC 中精确匹配章标题（spine 序号与 TOC 序号不一一对应，禁止下标映射） */
  function chapterTitleFromHref(href) {
    const nav = (book && book.navigation && book.navigation.toc) || []
    if (!href) return ''
    const base = href.split('#')[0]
    const found = nav.find((n) => {
      const h = String(n.href || '').split('#')[0]
      return h === base || h.endsWith('/' + base) || base.endsWith('/' + h)
    })
    return found ? cleanTocLabel(found.label || '') : ''
  }

  function emitReloc(start) {
    const spineIndex = start.index ?? 0
    const pct = computePercent(start.cfi || '', spineIndex)
    chapterIndex = spineIndex
    percent = pct
    if (callbacks.onReloc) {
      callbacks.onReloc({
        cfi: start.cfi || '',
        chapterIndex: spineIndex,
        percent: pct,
        chapterTitle: chapterTitleFromHref(start.href),
      })
    }
  }

  /**
   * 章导航链接注入（iframe 正文内，微信读书式）。
   * contents 为 epubjs Contents 实例（.document 即真实 iframe 文档）。
   */
  function injectChapterLinks(contents) {
    const doc = contents && contents.document
    if (!doc || !doc.body) return
    const info = callbacks.onInjectChapterLinks ? callbacks.onInjectChapterLinks() : null
    if (!info) return
    const { prev, next } = info

    if (!doc.getElementById('gkb-lib-chapter-nav-style')) {
      const style = doc.createElement('style')
      style.id = 'gkb-lib-chapter-nav-style'
      style.textContent = `
        .fce-chapter-link {
          display: block; margin: 0 auto; padding: 10px 0;
          font-family: inherit; font-size: 0.85em; font-weight: 600;
          color: #d65124;
          cursor: pointer; user-select: none; text-decoration: none;
          background: none; border: none; width: auto; max-width: 620px;
          text-align: center;
        }
        .fce-chapter-link:hover { text-decoration: underline; }
        .fce-chapter-sep {
          text-align: center; color: #999; font-size: 0.8em; letter-spacing: 2px;
          margin: 2.2em 0 1.2em;
        }
        .fce-chapter-end { text-align: center; margin: 2.5em 0 1.5em; }
        /* 划词锚点高亮：选区塌缩后保持词位反馈（色值随主题对比自动适配） */
        mark[data-lib-anchor] {
          background: rgba(255, 138, 92, 0.32);
          color: inherit;
          border-radius: 2px;
          padding: 0 1px;
        }
      `
      if (doc.head) doc.head.appendChild(style)
    }

    const mk = (label, target) => {
      const btn = doc.createElement('button')
      btn.className = 'fce-chapter-link'
      btn.textContent = label
      btn.addEventListener('click', (e) => {
        e.preventDefault()
        e.stopPropagation()
        if (target != null && callbacks.onChapterLinkClick) callbacks.onChapterLinkClick(target)
      })
      return btn
    }

    // 章首：上一章（无上一章则不放）
    if (prev && !doc.querySelector('.fce-prev-chapter')) {
      const top = mk('上一章', prev.spineIndex)
      top.className = 'fce-chapter-link fce-prev-chapter'
      doc.body.insertBefore(top, doc.body.firstChild)
    }
    // 章末：分隔线 + 下一章（末章显示「全书完」）
    const existingEnd = doc.querySelector('.fce-chapter-end')
    if (existingEnd) existingEnd.remove()
    const end = doc.createElement('div')
    end.className = 'fce-chapter-end'
    const sep = doc.createElement('div')
    sep.className = 'fce-chapter-sep'
    if (next) {
      sep.textContent = '本章完'
      const nextBtn = mk('下一章', next.spineIndex)
      nextBtn.style.cssText = 'font-size:1.05em; margin: 0 auto;'
      end.appendChild(sep)
      end.appendChild(nextBtn)
    } else {
      sep.textContent = '全书完'
      end.appendChild(sep)
    }
    doc.body.appendChild(end)
  }

  function relocatedHandler(location) {
    const loc = location
    if (loc && loc.start) emitReloc(loc.start)
    // relocated 时机做一次注入兜底（rendered 可能早于监听注册 / 数据晚到）
    setTimeout(() => {
      if (destroyed || !rendition || !injectHandler) return
      const list = rendition.getContents ? rendition.getContents() : []
      if (list && list[0]) injectHandler(list[0])
    }, 200)
    // 已读遮罩：确保滚动监听挂上并立即按当前滚动位刷新
    setupReadMaskScroll()
    updateReadMask()
  }

  // 划词：iframe 内坐标 + iframe 元素自身偏移 → 阅读容器坐标
  function onSelected(_cfi, contents) {
    const win = contents && contents.window
    if (!win) return
    const sel = win.getSelection()
    const text = (sel && sel.toString().trim()) || ''
    if (!text || text.length > 80) {
      if (callbacks.onSelect) callbacks.onSelect({ text: '', word: '', context: '', x: 0, y: 0 })
      return
    }
    const word = text.replace(/[^A-Za-z'’-]/g, '')
    const range = sel.getRangeAt(0).cloneRange()
    const context = range.toString().replace(/\s+/g, ' ').trim().slice(0, 200)

    const rect = sel.getRangeAt(0).getBoundingClientRect()
    const frameEl = win.frameElement
    const containerRect = container ? container.getBoundingClientRect() : null
    if (!frameEl || !containerRect) return
    const frameRect = frameEl.getBoundingClientRect()
    if (callbacks.onSelect) {
      callbacks.onSelect({
        text,
        word,
        context,
        // 原始选区 range（克隆）：lookup 层用它做装饰性锚点高亮（塌缩选区后仍能看见词位）
        range: range.cloneRange(),
        x: frameRect.left - containerRect.left + rect.left + rect.width / 2,
        y: frameRect.top - containerRect.top + rect.top,
      })
    }
  }

  /** rendered 事件：epubjs 签名 (section, view)，view.contents 才挂在真实 iframe 文档上 */
  function renderedHandler(_section, view) {
    const contents = view && view.contents ? view.contents : null
    if (contents && callbacks.onRendered) callbacks.onRendered(contents)
    injectChapterLinks(contents)
    // 新帧渲染后重挂滚动监听并刷新已读遮罩
    setupReadMaskScroll()
    updateReadMask()
  }

  // locations 生成（异步一次性；完成后用当前位置刷新百分比）
  let locTimeout = null
  function generateLocations() {
    if (!book) return
    if (book.locations.length()) {
      try { locationsReadyResolve() } catch { /* 已 resolve */ }
      return
    }
    locTimeout = setTimeout(() => {
      // 兜底：生成卡死 3s 也放行——假百分比风险换可用性（采集已另有
      // 恢复瞬态净化与限速层兜底）
      try { locationsReadyResolve() } catch { /* 已 resolve */ }
    }, 3000)
    book.locations
      .generate(1024)
      .then(() => {
        clearTimeout(locTimeout)
        try { locationsReadyResolve() } catch { /* 已 resolve */ }
        if (destroyed || !book || !rendition) return
        const loc = rendition.currentLocation()
        if (loc && loc.start) {
          const spineIndex = loc.start.index ?? 0
          const p = book.locations.percentageFromCfi(loc.start.cfi || '') || 0
          const pct = p > 0 ? p * 100 : computePercent(loc.start.cfi || '', spineIndex)
          percent = pct
          if (callbacks.onReloc) {
            callbacks.onReloc({
              cfi: loc.start.cfi || '',
              chapterIndex: spineIndex,
              percent: pct,
              chapterTitle: chapterTitleFromHref(loc.start.href),
            })
          }
        }
      })
      .catch(() => {})
  }

  function createRendition() {
    rendition = book.renderTo(container, {
      width: '100%',
      height: '100%',
      flow: 'scrolled',
      spread: 'none',
      allowScriptedContent: false,
    })
    injectHandler = injectChapterLinks
    rendition.on('relocated', relocatedHandler)
    rendition.on('selected', onSelected)
    rendition.on('rendered', renderedHandler)
    applyTheme(currentTheme)

    // 恢复进度（走 navChain，与用户导航严格排序）
    navigate(() => {
      const stored = localStorage.getItem(`gkb-lib-cfi-${bookId}`)
      const p = stored
        ? rendition.display(stored).catch(() => rendition.display())
        : rendition.display()
    // 恢复落位窗口：display 完成 + 自愈滚动 350ms + 重报 120ms 后视为稳定；
    // 同时等 locations 就绪（未就绪时 relocated 是 spine 兜底假百分比）
      p.then(() => {
        setTimeout(() => {
          if (!destroyed) restoreResolve()
        }, 620)
      })
      // epubjs scrolled 帧式渲染的恢复自愈：display(cfi) 时目标可能落在尚未 append 的
      // 后续帧里，moveTo 的 scrollTo 被 clamp 回 0。等帧真正显示后按目标 offset 再滚一次。
      if (stored) {
        p.then(() => {
          setTimeout(() => {
            if (destroyed || !rendition) return
            try {
              const range = rendition.getRange(stored)
              const node = range && range.startContainer
              if (!node) return
              let el = node.nodeType === 1 ? node : node.parentElement
              let top = 0
              const doc = node.ownerDocument
              while (el && el !== doc.body) {
                top += el.offsetTop || 0
                el = el.offsetParent
              }
              // 换算到滚动容器：iframe 自身相对容器偏移 + 帧内 offsetTop
              const frameEl = doc.defaultView && doc.defaultView.frameElement
              let frameTop = 0
              let fe = frameEl
              const scroller = container ? container.querySelector('.epub-container') : null
              while (fe && fe !== scroller && scroller) {
                frameTop += fe.offsetTop || 0
                fe = fe.offsetParent
              }
              const target = frameTop + top
              if (scroller && target > 2) {
                scroller.scrollTop = target
                updateReadMask()
                // 自愈滚动不触发 epubjs 的 relocated——直接设 scrollTop 后
                // progress.cfi 停留在恢复 display 时报告的旧值，退出时会用旧 cfi
                // 覆盖云端进度（表现为"进 17% 不动退出回退到 2%"）。
                // 用 currentLocation()（按真实滚动位置计算）重报一次。
                setTimeout(() => {
                  if (destroyed || !rendition) return
                  try {
                    const loc = rendition.currentLocation()
                    if (loc && loc.start) emitReloc(loc.start)
                  } catch {
                    /* ignore */
                  }
                }, 120)
              }
            } catch {
              /* 自愈失败维持 epubjs 原行为 */
            }
          }, 350)
        })
      }
      return p
    })

    generateLocations()
    readyResolve({ toc })
  }

  async function load() {
    try {
      const blob = await api.libraryFileBlob(bookId)
      const buf = await blob.arrayBuffer()
      if (destroyed) return
      book = ePub(buf)
      await book.ready
      if (destroyed) return
      // navigation 加载加超时保护：个别书的 nav promise 会悬挂，导致整书打不开
      await Promise.race([
        book.loaded.navigation,
        new Promise((_, rej) => setTimeout(() => rej(new Error('目录加载超时')), 15000)),
      ])
      if (destroyed) return
      toc = ((book.navigation && book.navigation.toc) || []).map((item) => ({
        ...item,
        label: cleanTocLabel(String(item.label || '')) || String(item.label || ''),
      }))
    } catch (e) {
      if (destroyed) return
      loadError = (e && e.message) || '打开书籍失败'
      readyReject(new Error(loadError))
      return
    }
    // 云端进度恢复：等 localStorage 写完再建 rendition，保证 display 恢复读到最新 cfi
    if (callbacks.progressSync) {
      try {
        await callbacks.progressSync
      } catch {
        /* 进度拉取失败按本地进度恢复 */
      }
    }
    if (destroyed) return
    createRendition()
  }

  /** 主题注入（iframe 全部 !important；themeStyle 变化时重调） */
  function applyTheme(t) {
    currentTheme = t
    if (!rendition) return
    rendition.themes.default({
      'html, body': {
        'background-color': `${t.bg} !important`,
        color: `${t.fg} !important`,
      },
      html: {
        // 已读遮罩（absolute）的定位基准
        'position': 'relative !important',
      },
      body: {
        background: `${t.bg} !important`,
        color: `${t.fg} !important`,
        'font-family': `${t.fontFamily} !important`,
        'font-size': `${t.fontSize}px !important`,
        'line-height': `${t.lineHeight} !important`,
        padding: '0 18px !important',
        // 专注模式：长按菜单（iOS Safari/WebView）；正文保持可选中——
        // 划词查词依赖选区，user-select:none 会连自家 selected 事件一起杀掉
        //（走查 B1）。系统文本菜单靠选区塌缩压制（安卓壳已下线）。
        '-webkit-touch-callout': 'none !important',
      },
      'p, div, span, li': {
        'background-color': 'transparent !important',
      },
      p: {
        'margin-top': '0.9em !important',
        'margin-bottom': '0.9em !important',
        // 微信读书式窄正文列（段落限宽居中，不影响 body 自身布局测量）。
        // 宽度偏好（#/config：standard=620px / wide=80%）不进阅读器显式 UI
        'max-width': `${
          t.libWidth === 'wide' ? '80%' : t.libWidth === 'semiwide' ? '60%' : '620px'
        } !important`,
        'margin-left': 'auto !important',
        'margin-right': 'auto !important',
      },
      '::selection': {
        background: 'rgba(255, 138, 92, 0.35)',
      },
    })
    rendition.themes.override('background-color', t.bg, true)
    rendition.themes.override('color', t.fg, true)
  }

  /** 按 spine 下标取 href（章级导航用；合并章的 spineIndex 与 spine 一一对应） */
  function spineHref(idx) {
    const spine = book ? book.spine : null
    if (!spine) return null
    const item = typeof spine.get === 'function' ? spine.get(idx) : undefined
    return (item && item.href) || null
  }

  /** 对当前渲染帧补注入章导航链接（chapters 数据晚到时用） */
  /**
   * 已读遮罩（微信读书式"读完感"）：已滚过（记忆位置以上）的正文盖 60% 半透明层。
   * epubjs scrolled 帧式渲染：滚动元素是 .epub-container（overflow:auto），iframe
   * 按帧高渲染——跨帧 relocated 粒度太粗，改由 scroll 驱动：
   *   已读高度 = container.scrollTop（跨多帧时累加前面帧的文档高度）。
   * 当前帧内的遮罩高度 = (已读总高 - 当前帧文档起点) / 当前帧文档高。
   */
  let maskScrollEl = null
  let maskScrollHandler = null
  function setupReadMaskScroll() {
    if (destroyed || !container) return
    const scroller = container.querySelector('.epub-container') || container
    if (scroller === maskScrollEl) return
    if (maskScrollEl && maskScrollHandler) {
      maskScrollEl.removeEventListener('scroll', maskScrollHandler)
    }
    maskScrollEl = scroller
    maskScrollHandler = () => requestAnimationFrame(updateReadMask)
    scroller.addEventListener('scroll', maskScrollHandler, { passive: true })
  }

  function updateReadMask() {
    if (destroyed || !rendition || !maskScrollEl) return
    const scrollTop = maskScrollEl.scrollTop
    const viewH = maskScrollEl.clientHeight || 1
    // 书最顶部（滚动量小于半个视口）：不加已读遮罩（需求②）
    const atTop = scrollTop < viewH * 0.5
    // 已读 = 已滚过的内容（当前视口上缘之上的部分）；顶部半屏内视为未读
    const readPx = atTop ? 0 : Math.max(0, scrollTop)
    let contentsList = []
    try {
      contentsList = rendition.getContents ? rendition.getContents() : []
    } catch {
      return
    }
    // 精确百分比（视线引导带下缘口径）——**全书口径**，分母绝不能是
    // 滚动流高度：epubjs scrolled 大书懒渲染，滚动流只含已渲染帧
    // （单章时 scrollHeight≈本章高，「章内 60%」会被当「全书 60%」——
    // Percy 单章 4472 词曾虚记上万词）。改用「帧锚点插值」：
    // 当前 gazeLine 所在帧的 iframe，其书内百分比区间 [fStart%, fEnd%]
    // 由 locations 按帧头/帧尾 cfi 算出（帧≈一章，区间稳定），帧内按
    // 像素线性插值。locations 未就绪时不上报（relocated 的粗值兜底）。
    const scrollStreamH = maskScrollEl.scrollHeight || 1
    const gazeLine = Math.min(scrollStreamH, scrollTop + viewH * 0.3)
    let precisePct = null
    try {
      let best = null
      for (const contents of contentsList) {
        const doc = contents && contents.document
        if (!doc || !doc.body) continue
        const frameEl = doc.defaultView && doc.defaultView.frameElement
        if (!frameEl) continue
        let frameTop = 0
        let el = frameEl
        while (el && el !== maskScrollEl) {
          frameTop += el.offsetTop || 0
          el = el.offsetParent
        }
        const fH = frameEl.offsetHeight || 1
        if (gazeLine >= frameTop && gazeLine <= frameTop + fH + 2) {
          best = { frameEl, frameTop, fH, doc }
          break
        }
      }
      if (best && book.locations && book.locations.length()) {
        const href = best.frameEl.getAttribute('src') || ''
        const base = href.split('/').pop()
        // 帧头/帧尾的书内百分比（locations 按 spine 项头尾取）
        const item = book.spine && book.spine.get
          ? [...Array(book.spine.length || 0).keys()].map((i) => book.spine.get(i)).find((s) => {
              const h = String((s && s.href) || '').split('/').pop()
              return h === base || base.includes(h) || h.includes(base)
            })
          : null
        if (item && item.cfiBase) {
          const headPct = book.locations.percentageFromCfi(item.cfiBase) || 0
          const tailPct = book.locations.percentageFromCfi(
            `${item.cfiBase}/99999:0`) || 0
          // 帧尾 cfi 越界时回落：用下一 spine 项头部（帧≈章边界）
          const idx2 = item.index != null ? item.index + 1 : null
          const nxt = idx2 != null && book.spine.get ? book.spine.get(idx2) : null
          const endPct = tailPct > headPct ? tailPct
            : (nxt && nxt.cfiBase && book.locations.percentageFromCfi(nxt.cfiBase)) || headPct
          const within = Math.max(0, Math.min(1,
            (gazeLine - best.frameTop) / (best.fH || 1)))
          precisePct = Math.max(0, Math.min(100,
            (headPct + (endPct - headPct) * within) * 100))
        }
      }
    } catch {
      precisePct = null
    }
    for (const contents of contentsList) {
      const doc = contents && contents.document
      if (!doc || !doc.body) continue
      const docH = doc.body.scrollHeight || 1
      // 该帧在整书滚动流中的起点：iframe 元素相对滚动容器的 offsetTop
      const frameEl = doc.defaultView && doc.defaultView.frameElement
      let frameTop = 0
      let el = frameEl
      while (el && el !== maskScrollEl) {
        frameTop += el.offsetTop || 0
        el = el.offsetParent
      }
      if (el === null) frameTop = 0 // 不在滚动流内（理论不发生）
      // 帧内已读高度：总已读 − 本帧起点（clamp 到 0..docH）
      const readInFrame = Math.min(docH, Math.max(0, readPx - frameTop))
      let mask = doc.getElementById('gkb-lib-readmask')
      if (readInFrame <= 2) {
        if (mask) mask.remove()
        continue
      }
      if (!mask) {
        mask = doc.createElement('div')
        mask.id = 'gkb-lib-readmask'
        mask.setAttribute('style', 'position:absolute;left:0;right:0;top:0;pointer-events:none;z-index:0;')
        ;(doc.body.parentElement || doc.body).appendChild(mask)
      }
      const h = Math.round((readInFrame / docH) * 10000) / 100
      mask.style.height = `${h}%`
      mask.style.background = `linear-gradient(to bottom, rgba(60,50,35,0.6) 0%, rgba(60,50,35,0.6) ${Math.max(0, h - 6)}%, rgba(60,50,35,0) ${h}%)`
    }
    // 回调精确百分比（整数变化才发，避免每像素刷顶栏）。
    // 插值失败（locations 未就绪/找不到帧）不发——relocated 的粗值兜底，
    // 绝不退回滚动流分母（那是本 bug 的根源）。
    // 恢复落位窗口内也不发：display 后 iframe 高度是渐进展开的（先≈视口高
    // 后长到章高），此刻 within 会被钳到 1，把恢复位置误报成帧尾
    // （实测：恢复在第5章27%被报成全书19.44%=章内98.5%）。
    if (precisePct != null && restoreSettled) {
      const rounded = atTop
        ? Math.max(0, Math.round(precisePct))
        : Math.round(precisePct)
      if (rounded !== lastPrecisePct) {
        lastPrecisePct = rounded
        if (callbacks.onPrecisePercent) callbacks.onPrecisePercent(rounded)
      }
    }
  }
  let lastPrecisePct = -1

  function injectChapterLinksNow() {
    if (!rendition || !rendition.getContents || !injectHandler) return
    const list = rendition.getContents() || []
    for (const c of list) injectHandler(c)
  }

  function destroy() {
    destroyed = true
    if (maskScrollEl && maskScrollHandler) {
      maskScrollEl.removeEventListener('scroll', maskScrollHandler)
      maskScrollEl = null
      maskScrollHandler = null
    }
    try {
      if (rendition) rendition.destroy()
    } catch {
      /* ignore */
    }
    try {
      if (book) book.destroy()
    } catch {
      /* ignore */
    }
    rendition = null
    book = null
    if (container) container.innerHTML = ''
  }

  load()

  return {
    get book() { return book },
    get rendition() { return rendition },
    get toc() { return toc },
    get error() { return loadError },
    get chapterIndex() { return chapterIndex },
    get percent() { return percent },
    ready,
    goNext: () => navigate(() => (rendition ? rendition.next() : Promise.resolve())),
    goPrev: () => navigate(() => (rendition ? rendition.prev() : Promise.resolve())),
    displayTarget: (target) => navigate(() => (rendition ? rendition.display(target) : Promise.resolve())),
    spineHref,
    injectChapterLinksNow,
    updateReadMask,
    applyTheme,
    restored: Promise.all([restored, locationsReady]).then(() => {}),
    destroy,
  }
}

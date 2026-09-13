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
  }

  // locations 生成（异步一次性；完成后用当前位置刷新百分比）
  function generateLocations() {
    if (!book) return
    if (book.locations.length()) return
    book.locations
      .generate(1024)
      .then(() => {
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
      return stored
        ? rendition.display(stored).catch(() => rendition.display())
        : rendition.display()
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
      body: {
        background: `${t.bg} !important`,
        color: `${t.fg} !important`,
        'font-family': `${t.fontFamily} !important`,
        'font-size': `${t.fontSize}px !important`,
        'line-height': `${t.lineHeight} !important`,
        padding: '0 18px !important',
      },
      'p, div, span, li': {
        'background-color': 'transparent !important',
      },
      p: {
        'margin-top': '0.9em !important',
        'margin-bottom': '0.9em !important',
        // 微信读书式窄正文列（段落限宽居中，不影响 body 自身布局测量）
        'max-width': '620px !important',
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
  function injectChapterLinksNow() {
    if (!rendition || !rendition.getContents || !injectHandler) return
    const list = rendition.getContents() || []
    for (const c of list) injectHandler(c)
  }

  function destroy() {
    destroyed = true
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
    applyTheme,
    destroy,
  }
}

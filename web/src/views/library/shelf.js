import { api } from '../../api.js'
import { ICONS } from './icons.js'
import { escapeHtml, escAttr } from '../../render.js'
import './shelf.css'

// 泛读馆 · 书架（教师/学生共用）
// 自 FCEReadingLib LibraryPage 直译为原生 JS：书卡网格、搜索排序、阅读统计 chips；
// 教师额外有上传 EPUB（按钮 / 网格虚线卡 / 全页拖拽）与书卡管理菜单（预习管理 / 删除）。
export async function mountLibraryShelf(viewEl, { role } = {}) {
  const isTeacher = role === 'teacher'

  viewEl.innerHTML = ''
  const root = document.createElement('div')
  root.className = 'lib-shelf'
  viewEl.append(root)

  // ---------- 自制 toast（顶部居中；error 5s，其余 3s） ----------
  function toast(msg, type = 'info') {
    let wrap = root.querySelector(':scope > .lib-toast-wrap')
    if (!wrap) {
      wrap = document.createElement('div')
      wrap.className = 'lib-toast-wrap'
      root.append(wrap)
    }
    const t = document.createElement('div')
    t.className = `lib-toast ${type}`
    t.textContent = msg
    wrap.append(t)
    setTimeout(() => {
      t.classList.add('out')
      setTimeout(() => t.remove(), 260)
    }, type === 'error' ? 5000 : 3000)
  }

  // ---------- 状态 ----------
  let data = null // {books, stats}；null = 加载中
  let loadError = null
  let search = ''
  let sort = 'recent' // 'recent' | 'added'
  let uploading = false
  let dragDepth = 0
  const coverUrls = new Set() // 待回收的封面 objectURL

  root.innerHTML = `
    <header class="lib-head">
      <div class="lib-titlebar">
        <h1 class="lib-title">${ICONS.books(24)} 泛读馆</h1>
        <span class="lib-stats" data-stats hidden></span>
        <span class="lib-flex"></span>
        ${isTeacher ? '<button class="lib-btn" data-upload-btn></button>' : ''}
      </div>
      <div class="lib-toolbar">
        <div class="lib-search">
          <input class="lib-input" type="search" placeholder="搜索书名或作者…" data-search autocomplete="off">
        </div>
        <select class="lib-input lib-sort" data-sort aria-label="排序方式">
          <option value="recent">最近阅读</option>
          <option value="added">添加时间</option>
        </select>
      </div>
    </header>
    <div class="lib-grid" data-grid></div>
    <div class="lib-empty" data-empty hidden></div>
    <input type="file" accept=".epub,application/epub+zip" hidden data-file>
    <div class="lib-drop" data-drop hidden>松开鼠标，导入 EPUB 电子书</div>
  `

  const gridEl = root.querySelector('[data-grid]')
  const emptyEl = root.querySelector('[data-empty]')
  const statsEl = root.querySelector('[data-stats]')
  const fileEl = root.querySelector('[data-file]')
  const dropEl = root.querySelector('[data-drop]')

  // ---------- 封面 blob：objectURL 统一登记，重渲染/卸载/加载失败时回收 ----------
  function revokeCovers() {
    for (const url of coverUrls) URL.revokeObjectURL(url)
    coverUrls.clear()
  }
  function loadCovers() {
    root.querySelectorAll('img[data-cover]').forEach((img) => {
      const id = Number(img.dataset.cover)
      api.libraryCoverBlob(id).then((blob) => {
        if (!img.isConnected) return
        const url = URL.createObjectURL(blob)
        coverUrls.add(url)
        // 渐显而非 hidden 切换：loading=lazy+display:none 会让 Chromium 永不加载（死锁）
        img.onload = () => { img.style.opacity = '1' }
        img.onerror = () => {
          img.remove()
          if (coverUrls.delete(url)) URL.revokeObjectURL(url)
        }
        img.src = url
      }).catch(() => { img.remove() /* 无封面：保留渐变底 + 书本图标 */ })
    })
  }

  // 路由切走时回收 objectURL。原方案是 MutationObserver 盯 viewEl 的 childList，
  // 改造后路由容器由 main.js 统一换血，改为往容器上登记清理函数（语义等价）
  ;(viewEl._cleanups ||= []).push(revokeCovers)

  // ---------- 过滤 + 排序 ----------
  function visibleBooks() {
    if (!data) return []
    const q = search.trim().toLowerCase()
    const list = data.books.filter((b) =>
      !q || (b.title || '').toLowerCase().includes(q) || (b.author || '').toLowerCase().includes(q))
    if (sort === 'recent') {
      // updatedAt 降序，null/空靠后；同位次按 id 降序
      list.sort((a, b) => {
        const ta = a.updatedAt || ''
        const tb = b.updatedAt || ''
        if (!ta && !tb) return b.id - a.id
        if (!ta) return 1
        if (!tb) return -1
        if (ta === tb) return b.id - a.id
        return tb.localeCompare(ta)
      })
    } else {
      list.sort((a, b) => b.id - a.id)
    }
    return list
  }

  // ---------- 渲染 ----------
  function renderStats() {
    if (!data) { statsEl.hidden = true; return }
    const s = data.stats || { reading: 0, finished: 0, totalSeconds: 0 }
    const hours = (s.totalSeconds / 3600).toFixed(1)
    statsEl.hidden = false
    statsEl.innerHTML = `在读 <b>${s.reading}</b> · 累计 <b>${hours} 小时</b> · 读完 <b>${s.finished}</b>`
  }

  function renderUploadState() {
    const btn = root.querySelector('[data-upload-btn]')
    if (btn) {
      btn.disabled = uploading
      btn.innerHTML = uploading
        ? '<span class="lib-spin"></span> 正在导入…'
        : `${ICONS.upload(16)} 上传书籍`
    }
    const card = root.querySelector('[data-upload-card]')
    if (card) {
      card.disabled = uploading
      card.innerHTML = uploading
        ? '<span class="lib-spin on-light lib-spin-big"></span><span>正在导入解析…</span>'
        : '<span class="plus">＋</span><span>上传 EPUB</span>'
    }
    const emptyBtn = root.querySelector('[data-upload-empty]')
    if (emptyBtn) {
      emptyBtn.disabled = uploading
      emptyBtn.innerHTML = uploading
        ? '<span class="lib-spin"></span> 正在导入…'
        : `${ICONS.upload(16)} 上传第一本书`
    }
  }

  function uploadCardHtml() {
    return `<button class="lib-upload-card" data-upload-card></button>`
  }

  function bookCardHtml(b) {
    const percent = b.percent == null ? 0 : Math.round(b.percent)
    const finished = percent >= 100
    return `
      <article class="lib-book" data-id="${b.id}" title="${escAttr(b.title)}">
        <div class="lib-cover">
          <span class="lib-cover-fb">${ICONS.book(38)}</span>
          ${b.hasCover ? `<img alt="${escAttr(b.title)}" data-cover="${b.id}" style="opacity:0;transition:opacity .25s">` : ''}
          ${finished ? '<span class="lib-done-flag">✓ 读完</span>' : ''}
          ${percent > 0 && !finished ? `<div class="lib-card-prog"><i style="width:${percent}%"></i></div>` : ''}
        </div>
        <div class="lib-book-info">
          <p class="lib-book-title">${escapeHtml(b.title)}</p>
          <div class="lib-book-author">${escapeHtml(b.author || '未知作者')}</div>
          <div class="lib-book-meta">${finished ? '已完成' : percent > 0 ? `${percent}%` : '未读'}</div>
        </div>
        ${isTeacher ? `
          <button class="lib-book-menu-btn" data-menu="${b.id}" aria-label="管理菜单">⋯</button>
          <div class="lib-book-menu" hidden data-menu-panel="${b.id}">
            <button data-go-manage="${b.id}">章节预习管理</button>
            <button class="danger" data-del="${b.id}">删除本书</button>
          </div>` : ''}
      </article>`
  }

  function showEmpty(html) {
    gridEl.innerHTML = ''
    emptyEl.hidden = false
    emptyEl.innerHTML = html
    renderUploadState()
  }

  function renderGrid() {
    revokeCovers()
    closeMenus()
    emptyEl.hidden = true
    if (loadError) {
      showEmpty(`
        <div class="art">${ICONS.warn(40)}</div>
        <h3>书架加载失败</h3>
        <p>${escapeHtml(loadError)}</p>
        <button class="lib-btn" data-retry>重试</button>`)
      return
    }
    if (!data) {
      gridEl.innerHTML = Array.from({ length: 6 }, () => '<div class="lib-skel"></div>').join('')
      return
    }
    const list = visibleBooks()
    if (list.length === 0) {
      if (isTeacher && !search.trim()) {
        showEmpty(`
          <div class="art">${ICONS.book(40)}</div>
          <h3>书馆还是空的</h3>
          <p>上传一本 EPUB 电子书开始阅读之旅<br>拖拽文件到页面任意位置也可以</p>
          <button class="lib-btn" data-upload-empty>${ICONS.upload(16)} 上传第一本书</button>`)
      } else if (search.trim()) {
        showEmpty(`
          <div class="art">${ICONS.search(40)}</div>
          <h3>没有找到书籍</h3>
          <p>没有匹配「${escapeHtml(search.trim())}」的书籍</p>`)
      } else {
        showEmpty(`
          <div class="art">${ICONS.book(40)}</div>
          <h3>还没有书</h3>
          <p>等待老师上传书籍，上传后即可开始阅读</p>`)
      }
      return
    }
    gridEl.innerHTML = (isTeacher ? uploadCardHtml() : '') + list.map(bookCardHtml).join('')
    renderUploadState()
    loadCovers()
  }

  // ---------- 书卡管理菜单（教师） ----------
  function closeMenus() {
    root.querySelectorAll('[data-menu-panel]').forEach((p) => (p.hidden = true))
    root.querySelectorAll('[data-menu]').forEach((b) => b.classList.remove('open'))
    root.querySelectorAll('[data-menu-backdrop]').forEach((b) => b.remove())
  }

  function toggleMenu(btn) {
    const panel = root.querySelector(`[data-menu-panel="${btn.dataset.menu}"]`)
    if (!panel) return
    const wasOpen = !panel.hidden
    closeMenus()
    if (!wasOpen) {
      panel.hidden = false
      btn.classList.add('open')
      const backdrop = document.createElement('div')
      backdrop.className = 'lib-menu-backdrop'
      backdrop.dataset.menuBackdrop = ''
      root.append(backdrop)
    }
  }

  function byId(id) {
    return data && data.books.find((b) => b.id === id)
  }

  // 删除确认：自制弹窗（不用 confirm()）
  function openDeleteConfirm(book) {
    closeMenus()
    const mask = document.createElement('div')
    mask.className = 'lib-modal-mask'
    mask.innerHTML = `
      <div class="lib-modal">
        <h3>删除《${escapeHtml(book.title)}》？</h3>
        <p class="lib-modal-text">将同时删除章节内容、预习数据与所有人的阅读进度，操作不可恢复。</p>
        <div class="lib-modal-actions">
          <button class="lib-btn sec" data-cancel>取消</button>
          <button class="lib-btn danger" data-ok><span class="lib-spin" data-del-spin hidden></span>确认删除</button>
        </div>
      </div>`
    root.append(mask)
    mask.addEventListener('click', async (e) => {
      if (e.target === mask || e.target.closest('[data-cancel]')) {
        mask.remove()
        return
      }
      const ok = e.target.closest('[data-ok]')
      if (!ok) return
      ok.disabled = true
      mask.querySelector('[data-del-spin]').hidden = false
      try {
        await api.libraryDeleteBook(book.id)
        toast(`已删除《${book.title}》`, 'success')
        mask.remove()
        await load()
      } catch (err) {
        toast(err.message || '删除失败', 'error')
        ok.disabled = false
        mask.querySelector('[data-del-spin]').hidden = true
      }
    })
  }

  // ---------- 上传（教师） ----------
  async function uploadFile(file) {
    if (!file.name.toLowerCase().endsWith('.epub')) {
      toast('只支持 .epub 格式的电子书', 'error')
      return
    }
    if (file.size > 100 * 1024 * 1024) {
      toast('文件超过 100MB 上限', 'error')
      return
    }
    uploading = true
    renderUploadState()
    try {
      const book = await api.libraryUpload(file)
      toast(`《${book.title}》已导入，共 ${book.chapterCount} 章`, 'success')
      location.hash = `/libraryManage/${book.id}`
    } catch (e) {
      toast(e.message || '导入失败', 'error')
    } finally {
      uploading = false
      renderUploadState()
    }
  }

  // ---------- 事件 ----------
  root.addEventListener('click', (e) => {
    if (e.target.closest('[data-menu-backdrop]')) { closeMenus(); return }
    const menuBtn = e.target.closest('[data-menu]')
    if (menuBtn) { toggleMenu(menuBtn); return }
    const goManage = e.target.closest('[data-go-manage]')
    if (goManage) {
      closeMenus()
      location.hash = `/libraryManage/${goManage.dataset.goManage}`
      return
    }
    const del = e.target.closest('[data-del]')
    if (del) {
      const book = byId(Number(del.dataset.del))
      if (book) openDeleteConfirm(book)
      return
    }
    if (e.target.closest('[data-upload-btn], [data-upload-card], [data-upload-empty]')) {
      fileEl.click()
      return
    }
    if (e.target.closest('[data-retry]')) {
      load()
      return
    }
    // 点整卡进阅读器（以上控件均已提前返回）
    const card = e.target.closest('.lib-book')
    if (card) location.hash = `/libraryReader/${card.dataset.id}`
  })

  root.querySelector('[data-search]').addEventListener('input', (e) => {
    search = e.target.value
    renderGrid()
  })
  root.querySelector('[data-sort]').addEventListener('change', (e) => {
    sort = e.target.value
    renderGrid()
  })
  fileEl.addEventListener('change', () => {
    const f = fileEl.files && fileEl.files[0]
    if (f) uploadFile(f)
    fileEl.value = ''
  })

  // 全页拖拽导入（教师；dragenter 计数器管理 overlay 状态）
  if (isTeacher) {
    root.addEventListener('dragenter', (e) => {
      e.preventDefault()
      dragDepth++
      dropEl.hidden = false
    })
    root.addEventListener('dragover', (e) => e.preventDefault())
    root.addEventListener('dragleave', (e) => {
      e.preventDefault()
      dragDepth--
      if (dragDepth <= 0) {
        dragDepth = 0
        dropEl.hidden = true
      }
    })
    root.addEventListener('drop', (e) => {
      e.preventDefault()
      dragDepth = 0
      dropEl.hidden = true
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
      if (f) uploadFile(f)
    })
  }

  // ---------- 数据加载 ----------
  async function load() {
    loadError = null
    data = null
    renderGrid()
    renderStats()
    try {
      data = await api.libraryBooks()
    } catch (e) {
      loadError = e.message || '无法连接后端'
    }
    renderGrid()
    renderStats()
  }

  await load()
}

import { api } from '../../api.js'
import { ICONS } from './icons.js'
import { escapeHtml } from '../../render.js'
import './manage.css'

// 泛读馆 · 章节预习管理（教师）
// 自 FCEReadingLib BookManagePage 直译为原生 JS：
// 章列表勾选 → 批量生成预习（POST batch + 每 2s 轮询任务进度）→ 失败章单独重试。
const GENERATING_STATUS = new Set(['queued', 'running'])

export async function mountLibraryManage(viewEl, { bookId } = {}) {
  viewEl.innerHTML = ''
  const root = document.createElement('div')
  root.className = 'lib-manage'
  viewEl.append(root)

  // ---------- 自制 toast ----------
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

  // ---------- 链接无效（bookId 为 NaN 等） ----------
  if (!Number.isInteger(bookId)) {
    root.innerHTML = `
      <div class="lm-error-card">
        <div class="art">${ICONS.warn(40)}</div>
        <h3>链接无效</h3>
        <p>地址里的书籍编号缺失或不对，请从书架重新进入。</p>
        <button class="lib-btn" data-back-shelf>返回书架</button>
      </div>`
    root.querySelector('[data-back-shelf]').addEventListener('click', () => {
      location.hash = '/library'
    })
    return
  }

  // ---------- 状态 ----------
  let chapters = null // null = 加载中
  let loadError = null
  let filter = 'todo' // todo | done | failed | skip | all
  let checked = new Set() // 勾选的章 idx
  let initialized = false // 首次数据到达后按「待生成」默认勾选
  let job = null // 最近一次批量任务（轮询更新）
  let submitting = false
  let retrying = null // 正在重试的章 idx
  let togglingSkip = null // 正在切换「无需生成」的章 idx
  let pollTimer = null

  root.innerHTML = `
    <header class="lm-head">
      <button class="lm-back" data-back-shelf>← 返回书架</button>
      <div class="lm-head-main">
        <h1>章节预习管理</h1>
        <p class="lm-sub" data-sub>加载中…</p>
      </div>
    </header>
    <div data-progress></div>
    <div class="lm-toolbar">
      <div class="lm-chips" data-chips></div>
      <div class="lm-sel-row">
        <button class="lib-btn small sec" data-select-page>选中本页</button>
        <button class="lib-btn small sec" data-select-all>全选</button>
        <button class="lib-btn small sec" data-select-none>全不选</button>
      </div>
    </div>
    <div class="lm-list" data-list></div>
    <div class="lm-actionbar">
      <div class="lm-actionbar-inner">
        <span class="lm-sel-info" data-sel-info></span>
        <span class="lm-flex"></span>
        <button class="lib-btn sec" data-gen-selected></button>
        <button class="lib-btn" data-gen-all></button>
      </div>
    </div>
  `

  const subEl = root.querySelector('[data-sub]')
  const chipsEl = root.querySelector('[data-chips]')
  const listEl = root.querySelector('[data-list]')
  const progressEl = root.querySelector('[data-progress]')
  const selInfoEl = root.querySelector('[data-sel-info]')
  const genSelBtn = root.querySelector('[data-gen-selected]')
  const genAllBtn = root.querySelector('[data-gen-all]')

  // ---------- 派生数据 ----------
  const byIdx = (idx) => (chapters || []).find((c) => c.idx === idx)
  const failedSet = () => new Set((job && job.failedIndexes) || [])
  const isGenerating = () => !!job && GENERATING_STATUS.has(job.status)
  // 待生成：未生成、未跳过、有正文、且不在最近一次任务的失败名单里
  const isTodo = (c, failed) => !c.prepped && !c.skipPrep && c.wordCount > 0 && !failed.has(c.idx)
  const todoList = () => {
    const failed = failedSet()
    return (chapters || []).filter((c) => isTodo(c, failed))
  }
  const pendingSelected = () => {
    const failed = failedSet()
    return [...checked].filter((idx) => {
      const c = byIdx(idx)
      return c && !c.prepped && !c.skipPrep && c.wordCount > 0 && !failed.has(c.idx)
    })
  }

  function visibleList() {
    const failed = failedSet()
    switch (filter) {
      case 'todo': return (chapters || []).filter((c) => isTodo(c, failed))
      case 'done': return (chapters || []).filter((c) => c.prepped)
      case 'failed': return (chapters || []).filter((c) => failed.has(c.idx))
      case 'skip': return (chapters || []).filter((c) => !!c.skipPrep)
      default: return chapters || []
    }
  }

  // ---------- 渲染 ----------
  function renderSub() {
    if (loadError) { subEl.textContent = ''; return }
    if (!chapters) { subEl.textContent = '加载中…'; return }
    const prepped = chapters.filter((c) => c.prepped).length
    const gen = isGenerating() && job ? ` · 生成中 ${job.done}/${job.total}` : ''
    subEl.textContent = `共 ${chapters.length} 章 · 已生成 ${prepped} 章${gen}`
  }

  function renderChips() {
    const failed = failedSet()
    const counts = {
      todo: 0, done: 0, skip: 0,
    }
    for (const c of chapters || []) {
      if (c.prepped) counts.done++
      if (c.skipPrep) counts.skip++
      if (isTodo(c, failed)) counts.todo++
    }
    const defs = [
      { key: 'todo', label: '待生成', count: counts.todo },
      { key: 'done', label: '已生成', count: counts.done },
      { key: 'failed', label: '失败', count: job ? failed.size : '—' },
      { key: 'skip', label: '无需生成', count: counts.skip },
      { key: 'all', label: '全部', count: chapters ? chapters.length : '—' },
    ]
    chipsEl.innerHTML = defs.map((f) => `
      <button class="lm-chip${filter === f.key ? ' active' : ''}" data-filter="${f.key}">
        ${f.label}<span class="lm-chip-count">${f.count}</span>
      </button>`).join('')
  }

  // 章标题常自带编号前缀（如「1\xa0\xa0\xa0\xa0I Accidentally…」），
  // 与行首 .idx 序号叠加会显示成「6 1 …」；展示前剥掉纯数字前缀
  function displayTitle(c) {
    const t = c.title || `第 ${c.idx + 1} 章`
    return t.replace(/^\s*\d+\s+/, '').trim() || t
  }

  function rowHtml(c) {
    const failed = failedSet()
    const gen = isGenerating()
    const eligible = !c.prepped && !c.skipPrep && c.wordCount > 0
    let badge
    if (c.prepped) badge = '<span class="lm-badge green">✓ 已生成</span>'
    else if (c.skipPrep) badge = '<span class="lm-badge gray">无需生成</span>'
    else if (gen && !failed.has(c.idx) && checked.has(c.idx)) badge = '<span class="lm-badge">⏳ 队列中</span>'
    else if (failed.has(c.idx)) badge = '<span class="lm-badge red">失败</span>'
    else if (c.wordCount === 0) badge = '<span class="lm-badge gray">无正文</span>'
    else badge = '<span class="lm-badge gray">未生成</span>'
    return `
      <div class="lm-row${eligible ? '' : ' ineligible'}" data-idx="${c.idx}">
        <input type="checkbox" data-check="${c.idx}" ${checked.has(c.idx) ? 'checked' : ''} ${eligible ? '' : 'disabled'}>
        <div class="lm-row-title">
          <div class="t"><span class="idx">${c.idx + 1}</span>${escapeHtml(displayTitle(c))}</div>
          <div class="sub">${c.wordCount > 0 ? `${c.wordCount} 词` : '无正文（扉页/目录等）'}</div>
        </div>
        <div class="lm-row-ops">
          ${badge}
          <button class="lib-btn small sec lm-skip-toggle" data-skip="${c.idx}"
            ${c.prepped ? 'disabled' : ''}
            title="${c.skipPrep ? '取消无需生成标记' : '标记为无需生成（批量任务会跳过）'}">
            ${togglingSkip === c.idx ? '<span class="lib-spin on-light"></span>' : c.skipPrep ? '取消标记' : '标记无需生成'}
          </button>
        </div>
      </div>`
  }

  function renderList() {
    if (loadError) {
      listEl.innerHTML = `
        <div class="lm-list-empty">
          <div class="art">${ICONS.warn(40)}</div>
          <h3>章节数据加载失败</h3>
          <p>${escapeHtml(loadError)}</p>
          <button class="lib-btn" data-retry-load>重试</button>
        </div>`
      return
    }
    if (!chapters) {
      listEl.innerHTML = Array.from({ length: 5 }, () =>
        '<div class="lm-row"><span class="lm-skel"></span></div>').join('')
      return
    }
    const visible = visibleList()
    if (visible.length === 0) {
      const hints = {
        todo: ['没有待生成的章节', '这本书的预习都已生成完毕'],
        done: ['还没有已生成的预习', '在「待生成」里勾选章节，点下方按钮开始'],
        skip: ['没有标记为无需生成的章节', '在「全部」里点行尾的「标记无需生成」'],
        failed: ['没有失败记录', '生成失败的章节会出现在这里，可单独重试'],
        all: ['没有章节', ''],
      }
      const [t, p] = hints[filter] || hints.all
      listEl.innerHTML = `
        <div class="lm-list-empty">
          <div class="art">✅</div>
          <h3>${t}</h3>
          ${p ? `<p>${p}</p>` : ''}
        </div>`
      return
    }
    listEl.innerHTML = visible.map(rowHtml).join('')
  }

  function renderBar() {
    const pending = pendingSelected()
    selInfoEl.textContent = `已选 ${checked.size} 章 · 待生成 ${pending.length}`
    const gen = isGenerating()
    genSelBtn.disabled = submitting || gen || pending.length === 0
    genSelBtn.innerHTML = submitting && !gen
      ? '<span class="lib-spin"></span> 提交中…'
      : `生成预习（${pending.length}）`
    genAllBtn.disabled = submitting || gen || todoList().length === 0
    genAllBtn.innerHTML = gen
      ? '<span class="lib-spin"></span> 生成中…'
      : submitting
        ? '<span class="lib-spin"></span> 提交中…'
        : `${ICONS.bolt(16)} 全书一键预习`
  }

  function renderProgress() {
    if (!job) { progressEl.innerHTML = ''; return }
    const gen = isGenerating()
    const failed = job.failedIndexes || []
    if (!gen && failed.length === 0) { progressEl.innerHTML = ''; return }
    const statusText = gen
      ? '正在逐章生成，每章调用一次 AI，请稍候…'
      : job.status === 'interrupted'
        ? '任务曾中断（服务重启），可重新生成未完成章节'
        : '部分章节生成失败'
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0
    progressEl.innerHTML = `
      <div class="lm-prog-card">
        <div class="lm-prog-info">
          <span>${statusText}</span>
          <span class="lm-muted">${job.done}/${job.total} · 全书 ${job.preppedTotal ?? '-'}/${job.bookChapters ?? '-'}</span>
        </div>
        <div class="lm-prog-track"><i style="width:${pct}%"></i></div>
        ${failed.length ? `
          <div class="lm-prog-failed">
            <span class="lm-muted">失败章节：</span>
            ${failed.map((i) => `
              <button class="lib-btn small danger" data-retry="${i}">
                ${retrying === i ? '<span class="lib-spin"></span>' : ''}重试第 ${i + 1} 章
              </button>`).join('')}
          </div>` : ''}
      </div>`
  }

  function renderAll() {
    renderSub()
    renderChips()
    renderList()
    renderBar()
    renderProgress()
  }

  // ---------- 勾选 ----------
  function toggleCheck(idx) {
    if (checked.has(idx)) checked.delete(idx)
    else checked.add(idx)
    renderList()
    renderBar()
  }

  // ---------- 无需生成 toggle ----------
  async function toggleSkipChapter(idx) {
    const c = byIdx(idx)
    if (!c || togglingSkip !== null) return
    togglingSkip = idx
    renderList()
    try {
      await api.librarySkipChapter(bookId, idx, !c.skipPrep)
      c.skipPrep = !c.skipPrep
      if (c.skipPrep) checked.delete(idx) // 跳过后不再参与批量
    } catch (e) {
      toast(e.message || '操作失败', 'error')
    } finally {
      togglingSkip = null
      renderAll()
    }
  }

  // ---------- 批量提交 + 轮询 ----------
  async function submitBatch(list) {
    if (submitting || isGenerating()) return
    const failed = failedSet()
    const eligible = (list || pendingSelected()).filter((idx) => {
      const c = byIdx(idx)
      return c && !c.prepped && !c.skipPrep && c.wordCount > 0 && !failed.has(idx)
    })
    if (eligible.length === 0) {
      toast('没有需要生成的章节', 'info')
      return
    }
    submitting = true
    renderBar()
    try {
      const r = await api.libraryPrepBatch(bookId, eligible)
      if (r.skipped || r.jobId == null) {
        toast('没有需要生成的章节', 'info')
        return
      }
      toast(`已开始生成 ${eligible.length} 章预习`, 'success')
      await trackJob(r.jobId, eligible.length)
    } catch (e) {
      toast(e.message || '提交失败', 'error')
    } finally {
      submitting = false
      renderBar()
    }
  }

  async function trackJob(jobId, planned) {
    try {
      job = await api.libraryPrepBatchStatus(jobId)
    } catch {
      // 立查失败：先用占位任务，交给轮询补齐
      job = { status: 'running', total: planned, done: 0, failedIndexes: [], preppedTotal: null, bookChapters: null }
    }
    renderProgress()
    renderSub()
    renderBar()
    if (!GENERATING_STATUS.has(job.status)) {
      await onJobFinished()
      return
    }
    startPoll(jobId)
  }

  function startPoll(jobId) {
    stopPoll()
    pollTimer = setInterval(async () => {
      if (!root.isConnected) { stopPoll(); return }
      try {
        job = await api.libraryPrepBatchStatus(jobId)
      } catch {
        return // 轮询失败静默，等下一轮
      }
      renderProgress()
      renderSub()
      if (!GENERATING_STATUS.has(job.status)) {
        stopPoll()
        await onJobFinished()
      }
    }, 2000)
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function onJobFinished() {
    await refreshChapters()
    renderAll()
    const failed = (job && job.failedIndexes) || []
    if (failed.length) toast(`${failed.length} 章生成失败，可单独重试`, 'error')
    else if (job && job.status === 'done') toast('全书预习生成完成', 'success')
  }

  async function refreshChapters() {
    try {
      const r = await api.libraryChapters(bookId)
      chapters = r.chapters || []
      // 清理已生成的勾选，避免「待生成 M」虚高
      checked = new Set([...checked].filter((idx) => {
        const c = byIdx(idx)
        return c && !c.prepped
      }))
    } catch {
      /* 刷新失败保留旧数据 */
    }
  }

  // ---------- 失败章重试 ----------
  async function retrySingle(idx) {
    if (retrying !== null) return
    retrying = idx
    renderProgress()
    try {
      await api.libraryPrepSingle(bookId, idx)
      toast(`第 ${idx + 1} 章预习已生成`, 'success')
      if (job) job.failedIndexes = (job.failedIndexes || []).filter((i) => i !== idx)
      checked.delete(idx)
      await refreshChapters()
    } catch (e) {
      toast(e.message || '生成失败', 'error')
    } finally {
      retrying = null
      renderAll()
    }
  }

  // ---------- 事件 ----------
  root.querySelector('[data-back-shelf]').addEventListener('click', () => {
    location.hash = '/library'
  })

  chipsEl.addEventListener('click', (e) => {
    const chip = e.target.closest('[data-filter]')
    if (!chip) return
    filter = chip.dataset.filter
    renderChips()
    renderList()
  })

  root.querySelector('[data-select-page]').addEventListener('click', () => {
    if (!chapters) return
    const targets = visibleList().filter((c) => !c.prepped && !c.skipPrep && c.wordCount > 0).map((c) => c.idx)
    if (!targets.length) return
    const allIn = targets.every((i) => checked.has(i))
    if (allIn) targets.forEach((i) => checked.delete(i))
    else targets.forEach((i) => checked.add(i))
    renderList()
    renderBar()
  })
  root.querySelector('[data-select-all]').addEventListener('click', () => {
    if (!chapters) return
    checked = new Set(todoList().map((c) => c.idx))
    renderList()
    renderBar()
  })
  root.querySelector('[data-select-none]').addEventListener('click', () => {
    checked = new Set()
    renderList()
    renderBar()
  })

  listEl.addEventListener('click', (e) => {
    if (e.target.closest('[data-retry-load]')) { load(); return }
    const skipBtn = e.target.closest('[data-skip]')
    if (skipBtn) { toggleSkipChapter(Number(skipBtn.dataset.skip)); return }
    if (e.target.closest('input, .lm-row-ops')) return // 复选框与行内按钮自理
    const row = e.target.closest('.lm-row')
    if (!row) return
    const idx = Number(row.dataset.idx)
    const c = byIdx(idx)
    if (c && !c.prepped && !c.skipPrep && c.wordCount > 0) toggleCheck(idx)
  })
  // 进度卡里的「重试第 N 章」按钮
  progressEl.addEventListener('click', (e) => {
    const retry = e.target.closest('[data-retry]')
    if (retry) retrySingle(Number(retry.dataset.retry))
  })
  listEl.addEventListener('change', (e) => {
    const cb = e.target.closest('input[type="checkbox"][data-check]')
    if (cb) toggleCheck(Number(cb.dataset.check))
  })

  genSelBtn.addEventListener('click', () => submitBatch())
  genAllBtn.addEventListener('click', () => submitBatch(todoList().map((c) => c.idx)))

  // 卸载清理：停轮询（root 从 viewEl 移除即视为离开本页）
  const mo = new MutationObserver(() => {
    if (!root.isConnected) {
      stopPoll()
      mo.disconnect()
    }
  })
  mo.observe(viewEl, { childList: true })

  // ---------- 数据加载 ----------
  async function load() {
    loadError = null
    chapters = null
    renderAll()
    try {
      const r = await api.libraryChapters(bookId)
      chapters = r.chapters || []
    } catch (e) {
      loadError = e.message || '无法连接后端'
    }
    // 首次数据到达：默认勾选「待生成」章（未生成、未跳过、有正文）
    if (chapters && !initialized && chapters.length > 0) {
      initialized = true
      checked = new Set(todoList().map((c) => c.idx))
    }
    renderAll()
  }

  await load()
}

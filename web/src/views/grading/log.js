import { api } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { fmtDur } from '../../fmt.js'
import { focusRow, fmtCnTime } from './shared.js'
import { openFocusDetail } from './focus.js'

// 批改中心 · 学习日志子 Tab：近 7 天汇总 + 全量会话列表（含按日聚合）。

  // ---- 学习日志（独立子 Tab）：近 7 天汇总 + 全量会话列表 ----
export async function mountLog(el, { state }) {
    const student = state.student
    el.innerHTML = '<p class="muted">加载中…</p>'
    const timed = (p, ms = 8000) => Promise.race([
      p, new Promise((resolve) => setTimeout(() => resolve([]), ms)),
    ]).catch(() => [])
    let focus = [], recite = [], rm = []
    ;[focus, recite, rm] = await Promise.all([
      timed(api.focusSessions({ limit: 100, offset: 0, user: student })),
      timed(api.reciteSessions({ limit: 100, user: student })),
      timed(api.rmSessions({ user: student })),
    ])
    focus = Array.isArray(focus) ? focus : []
    // 泛读会话统一拉 rw（focus/words 同计划表公式）：僵尸会话（0%→0%
    // 挂机，后端已标「无有效阅读」且 0 词 0 重合）在此一次性剔除——
    // 近7天汇总/按日聚合/列表/专注均分全视图同口径，不再漏到聚合里
    {
      const libIds = focus.filter((s) => s.module === 'library').map((s) => s.id)
      if (libIds.length) {
        try {
          const wm = await api.focusWords(libIds)
          focus = focus.filter((s) => {
            if (s.module !== 'library') return true
            if (wm[s.id]) s.rw = wm[s.id]
            const rw = s.rw
            return !(rw && (rw.flags || []).includes('无有效阅读')
              && !rw.words && !rw.overlap)
          })
        } catch { /* rw 接口失败时不过滤（保守展示） */ }
      }
    }
    // 杂志会话归一成学习日志行形状：module=rm；时长主数字=专注学习
    // engaged_sec（对齐 active_sec 语义）；total_ms_r 保留原始总时长
    // （rmLogRow 算专注占比用，不能被 total_sec 污染）；rw.words 复用
    // 聚合行的「新学 N 词」
    rm = (Array.isArray(rm) ? rm : []).map((x) => ({
      ...x, module: 'rm', active_sec: x.engaged_sec || 0,
      total_sec: x.engaged_sec || 0, total_ms_r: x.total_sec || 0,
      rw: (x.vocab_add || 0) > 0 ? { words: x.vocab_add } : null,
    }))

    const weekMs = 7 * 24 * 3600 * 1000
    const nowMs = Date.now()
    const inWeek = (t) => new Date(t || 0).getTime() >= nowMs - weekMs
    const focusWeek = focus.filter((s) => inWeek(s.started_at || s.created_at))
    const libW = focus.filter((s) => s.module === 'library' && inWeek(s.started_at || s.created_at))
    const rdW = focus.filter((s) => s.module === 'reading' && inWeek(s.started_at || s.created_at))
    const rmW = rm.filter((s) => inWeek(s.started_at))
    const vocabW = recite.filter((s) => inWeek(s.created_at))
    const scores = focusWeek.filter((s) => typeof s.score === 'number')
    const parts = []
    if (libW.length) parts.push(`📖 泛读 ${Math.round(libW.reduce((a, s) => a + (s.active_sec || 0), 0) / 60)} 分钟`)
    if (rdW.length) parts.push(`📄 精读 ${Math.round(rdW.reduce((a, s) => a + (s.active_sec || 0), 0) / 60)} 分钟`)
    if (rmW.length) parts.push(`📰 杂志 ${Math.round(rmW.reduce((a, s) => a + (s.engaged_sec || 0), 0) / 60)} 分钟`)
    if (vocabW.length) parts.push(`🔤 背词 ${vocabW.reduce((a, s) => a + (s.total || 0), 0)} 词 · ${Math.round(vocabW.reduce((a, s) => a + (s.duration_sec || 0), 0) / 60)} 分钟`)
    if (scores.length) parts.push(`👁 专注 ${Math.round(scores.reduce((a, s) => a + s.score, 0) / scores.length)} 分`)

    el.innerHTML = `
      <section class="gcard" data-card="focus" style="margin-bottom:14px">
        <header class="gcard-head">
          <h2>📚 学习日志</h2>
          <b class="gcard-n">${focus.length + recite.length + rm.length}</b>
          <button class="reading-btn small" id="gd-log-agg" title="同一天的同类型日志合并为一条汇总">📅 按日聚合</button>
        </header>
        <p class="reading-hint">${parts.length ? `近 7 天：${parts.join(' · ')}` : '近 7 天暂无学习记录'}</p>
        <div id="gd-focus-list"><p class="reading-hint">加载中…</p></div>
      </section>
    `
    const host = el.querySelector('#gd-focus-list')
    if (!host) return

    // 按日聚合：同一天同类型（泛读/精读/专题/背词）合并为一条汇总行。
    // 纯前端聚合（focus+recite 已全量在手）；点击聚合行展开当天的原始明细。
    let aggMode = false
    const openDays = new Set()
    const dayKey = (t) => (t || '').slice(0, 10)
    function buildDayGroups() {
      const groups = new Map() // date -> { modules: Map(mod -> rows) }
      const push = (t, mod, row) => {
        const k = dayKey(t)
        if (!groups.has(k)) groups.set(k, new Map())
        const mods = groups.get(k)
        if (!mods.has(mod)) mods.set(mod, [])
        mods.get(mod).push(row)
      }
      for (const s of focus) {
        const mod = s.module === 'reading' ? '精读' : s.module === 'topics' ? '专题' : '泛读'
        push(s.started_at || s.created_at, mod, s)
      }
      for (const r of rm) push(r.started_at, '杂志', r)
      for (const r of recite) push(r.created_at, '背词', r)
      return [...groups.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1))
    }

    function aggRowHtml(date, mods, expanded) {
      const rows = []
      const totalMin = { v: 0 }
      for (const [mod, list] of mods) {
        if (mod === '背词') {
          const words = list.reduce((a, x) => a + (x.total || 0), 0)
          const wrong = list.reduce((a, x) => a + (x.wrong || 0), 0)
          const min = Math.round(list.reduce((a, x) => a + (x.duration_sec || 0), 0) / 60)
          const acc = words ? Math.round((words - wrong) / words * 100) : 0
          totalMin.v += min
          rows.push(`<div class="gd-agg-line"><i class="gd-focus-mod vocab">${mod}</i>
            <b class="gd-agg-min">${min}<u>分钟</u></b>
            <span>${list.length} 组 · ${words} 词 · 正确率 <b class="fce-his-score ${acc >= 80 ? 'ok' : acc >= 60 ? '' : 'bad'}">${acc}%</b></span></div>`)
        } else {
          const modCls = mod === '精读' ? 'gd-focus-mod reading'
            : mod === '杂志' ? 'gd-focus-mod rm' : 'gd-focus-mod'
          const sec = list.reduce((a, x) => a + (x.active_sec || 0), 0)
          const min = Math.round(sec / 60)
          const words = list.reduce((a, x) => a + ((x.rw && x.rw.words) || 0), 0)
          const lookups = list.reduce((a, x) => a + (x.lookups || 0), 0)
          const scored = list.filter((x) => typeof x.score === 'number')
          const avg = scored.length ? Math.round(scored.reduce((a, x) => a + x.score, 0) / scored.length) : null
          totalMin.v += min
          rows.push(`<div class="gd-agg-line"><i class="${modCls}">${mod}</i>
            <b class="gd-agg-min">${min}<u>分钟</u></b>
            <span>${list.length} 段${words ? ` · 新学 ${words} 词` : ''}${lookups ? ` · 查词 ${lookups}` : ''}${avg !== null ? ` · 专注 <b class="fce-his-score ${avg >= 80 ? 'ok' : avg >= 60 ? '' : 'bad'}">${avg}</b>` : ''}</span></div>`)
        }
      }
      // 展开态：该日全部原始明细行（聚合行仍置顶）。recite 行无 module
      // 字段，靠 kind 区分；rm 行有 module='rm'，先于 focusRow 判掉
      const detailHtml = expanded
        ? `<div class="gd-agg-detail">${[...mods.values()].flat()
            .map((s) => (s.module === 'rm' ? rmLogRow(s)
              : s.module ? focusRow(s) : reciteLogRow(s)))
            .join('')}</div>`
        : ''
      const open = expanded ? '▾' : '▸'
      return `
        <div class="gd-agg-day ${expanded ? 'open' : ''}" data-agg-day="${date}">
          <div class="gd-agg-head">
            <b>${open} ${date}</b>
            <span class="gd-agg-sum">${mods.size} 类 · 共 ${totalMin.v} 分钟</span>
          </div>
          ${rows.join('')}
          ${detailHtml}
        </div>`
    }

    function renderAgg() {
      const groups = buildDayGroups()
      if (!groups.length) {
        host.innerHTML = '<p class="reading-hint">暂无学习记录——学生在泛读馆读书/背单词后自动生成</p>'
        return
      }
      // 简单重渲染整组（不 diff）——outerHTML 替换后新节点没有监听，
      // 收起→再点展开会失灵；数据量（≤7 天 ×几行）重渲染无感。
      // 首次进入默认展开最近一天。
      if (!openDays.size && groups.length) openDays.add(groups[0][0])
      const paint = () => {
        host.innerHTML = groups
          .map(([date, mods]) => aggRowHtml(date, mods, openDays.has(date)))
          .join('')
        host.querySelectorAll('[data-agg-day]').forEach((dayEl) => {
          dayEl.querySelector('.gd-agg-head').addEventListener('click', () => {
            const d = dayEl.dataset.aggDay
            openDays.has(d) ? openDays.delete(d) : openDays.add(d)
            paint()
          })
        })
      }
      paint()
    }

    const aggBtn = el.querySelector('#gd-log-agg')
    if (aggBtn) {
      aggBtn.addEventListener('click', async () => {
        aggMode = !aggMode
        aggBtn.classList.toggle('primary', aggMode)
        aggBtn.textContent = aggMode ? '📅 按日聚合（开）' : '📅 按日聚合'
        if (aggMode) {
          // rw 已在 mountLog 加载时统一拉好（含僵尸剔除），直接渲染
          renderAgg()
        } else {
          renderFocusPage(0)
        }
      })
    }

    // 会话列表：focus（泛读/精读/专题）+ recite（背词）混排分页
    const FOCUS_PAGE = 12
    let focusPage = 0
    async function renderFocusPage(page) {
      host.innerHTML = '<p class="reading-hint">加载中…</p>'
      let items = focus.map((s) => ({
        kind: 'focus', id: s.id, user: s.user, module: s.module,
        t: s.started_at || s.created_at, raw: s,
      }))
      // 背单词会话并入学习日志（正确率即质量指标，无专注分）
      for (const r of recite) {
        items.push({ kind: 'recite', id: `r${r.id}`, user: r.user, module: 'vocab', t: r.created_at, raw: r })
      }
      // 杂志阅读会话（阅读更多）并入
      for (const r of rm) {
        items.push({ kind: 'rm', id: `m${r.id}`, user: r.user, module: 'rm', t: r.started_at, raw: r })
      }
      items.sort((a, b) => (b.t || '') < (a.t || '') ? -1 : 1)
      focusPage = page
      const pageItems = items.slice(page * FOCUS_PAGE, (page + 1) * FOCUS_PAGE)
      const hasNext = items.length > (page + 1) * FOCUS_PAGE
      // 泛读僵尸会话已在 mountLog 数据加载时统一剔除（rw 亦已带上）
      if (!pageItems.length) {
        host.innerHTML = page === 0
          ? '<p class="reading-hint">暂无学习记录——学生在泛读馆读书/背单词后自动生成</p>'
          : '<p class="reading-hint">没有更多记录了</p>'
      } else {
        host.innerHTML = pageItems.map((s) => s.kind === 'recite' ? reciteLogRow(s.raw)
          : s.kind === 'rm' ? rmLogRow(s.raw) : focusRow(s.raw)).join('')
        host.querySelectorAll('[data-focus-id]').forEach((row) => {
          row.addEventListener('click', () => openFocusDetail(Number(row.dataset.focusId)))
        })
      }
      if (items.length > FOCUS_PAGE || page > 0) {
        const pager = document.createElement('div')
        pager.className = 'gd-focus-pager'
        pager.innerHTML = `
          <button class="reading-btn small" data-fp="prev" ${page === 0 ? 'disabled' : ''}>‹ 上一页</button>
          <span class="gd-focus-page-no">第 ${page + 1} 页 / 共 ${Math.max(1, Math.ceil(items.length / FOCUS_PAGE))} 页</span>
          <button class="reading-btn small" data-fp="next" ${hasNext ? '' : 'disabled'}>下一页 ›</button>`
        pager.querySelectorAll('[data-fp]').forEach((b) =>
          b.addEventListener('click', () => {
            const dir = b.dataset.fp === 'next' ? 1 : -1
            const next = focusPage + dir
            if (next < 0) return
            renderFocusPage(next)
          }),
        )
        host.appendChild(pager)
      }
    }
    renderFocusPage(0)
}

// 学习日志-背单词行（无专注分，正确率即质量）
function reciteLogRow(x) {
  return `
    <div class="fce-his-row gd-focus-row">
      <span class="fce-his-what">${escapeHtml(x.user || '')} · 背单词 ${scopeCn(x.scope)}${x.mode === 'flip' ? '（自评）' : ''}</span>
      <b class="fce-his-score ${x.acc >= 80 ? 'ok' : x.acc >= 60 ? '' : 'bad'}">${x.acc}%</b>
      <span class="fce-his-date">${x.total} 词 · 错 ${x.wrong} · ${fmtDur(x.duration_sec || 0)} · ${fmtCnTime(x.created_at)}</span>
      <span class="gd-focus-chips"><i class="gd-focus-mod vocab">背词</i></span>
    </div>`
}

// 学习日志-杂志阅读行（阅读更多）：质量数字=专注占比（engaged/total），
// 无评分模型；生词本 add 数即本次加生词量
function rmLogRow(x) {
  // 占比分母=会话原始总时长（页面可见），total_sec 已被归一化为专注秒数
  const denom = x.total_ms_r || x.total_sec || 0
  const pct = denom ? Math.round((x.engaged_sec || 0) / denom * 100) : 0
  const book = (x.book || '').trim() || '未记书名'
  return `
    <div class="fce-his-row gd-focus-row">
      <span class="fce-his-what">${escapeHtml(x.user || '')} · 📰 ${escapeHtml(book)}</span>
      <b class="fce-his-score ${pct >= 80 ? 'ok' : pct >= 60 ? '' : 'bad'}">${pct}%</b>
      <span class="fce-his-date">${fmtDur(x.engaged_sec || 0)} 专注 · ${fmtCnTime(x.started_at)}</span>
      <span class="gd-focus-chips">
        <i class="gd-focus-mod rm">杂志</i>
        ${x.turns ? `<i class="gd-focus-chip">翻页 ${x.turns}</i>` : ''}
        ${x.lookups ? `<i class="gd-focus-chip">查词 ${x.lookups}</i>` : ''}
        ${x.vocab_add ? `<i class="gd-focus-chip">生词本 +${x.vocab_add}</i>` : ''}
      </span>
    </div>`
}

function scopeCn(scope) {
  return { all: '全部词表', verb: '只动词', special: '特殊拼写' }[scope] || scope || '全部词表'
}

function topWrongWords(sessions) {
  const freq = new Map()
  for (const s of sessions.slice(0, 20)) {
    for (const w of s.wrong_words || []) freq.set(w, (freq.get(w) || 0) + 1)
  }
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([w, n]) => `${escapeHtml(w)}×${n}`)
    .join('、') || '（暂无）'
}

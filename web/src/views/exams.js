import { escapeHtml } from '../render.js'
import { api } from '../api.js'

// 作业成绩记录：每讲一份作业卷（35 题，1~5 每题 2 分、6~35 每题 3 分，满分 100）。
// 孩子每卷会做多次：全部记录存 localStorage，错题按「讲次+题号」汇总方便回查。
const STORE_KEY = 'gkb-exam-records-v1'   // 旧 localStorage 记录，首启迁移到后端
const MIGRATED_KEY = 'gkb-exam-migrated-v1'
const TOTAL = 35

// 题号 → 分值（1~5 题 2 分，6~35 题 3 分）
const scoreOf = (q) => (q <= 5 ? 2 : 3)
const fullScore = Array.from({ length: TOTAL }, (_, i) => scoreOf(i + 1)).reduce((a, b) => a + b, 0)

// 记录持久化于后端 exam.db；首次启动把 localStorage 旧记录迁移上去
async function migrateOnce() {
  if (localStorage.getItem(MIGRATED_KEY)) return
  localStorage.setItem(MIGRATED_KEY, '1')
  try {
    const remote = await api.examsList()
    if (remote.length) return
    let old = []
    try {
      old = JSON.parse(localStorage.getItem(STORE_KEY) || '[]')
    } catch { /* 忽略坏数据 */ }
    for (const r of old) {
      if (r && r.lecture && r.date != null) {
        await api.examsAdd({ lecture: r.lecture, date: r.date, score: r.score || 0, wrong: r.wrong || [] })
      }
    }
    if (old.length) console.info(`已迁移 ${old.length} 条本地成绩到后端`)
  } catch { /* 后端不可用时下次再迁移 */ }
}

export function mountExams(el, { lectures }) {
  const lecTitle = (n) => {
    const l = lectures.find((x) => x.number === n)
    return l ? l.title : ''
  }

  el.innerHTML = `
    <div class="view-head">
      <h1>作业成绩</h1>
      <p>每讲一份作业卷（35 题 · 满分 ${fullScore}：1~5 题每题 ${scoreOf(1)} 分，6~35 题每题 ${scoreOf(6)} 分）。点题号记对错，分数自动算；多次作答全部保留。</p>
    </div>

    <section class="exam-sec">
      <h3 id="ex-form-title">📝 记一次</h3>
      <div class="exam-form">
        <div class="exam-form-row">
          <label>讲次
            <select id="ex-lecture">
              ${lectures
                .slice()
                .sort((a, b) => a.number - b.number)
                .map((l) => `<option value="${l.number}">第${l.number}讲 ${escapeHtml(l.title)}</option>`)
                .join('')}
            </select>
          </label>
          <label>日期 <input type="date" id="ex-date" /></label>
          <label>得分 <input type="number" id="ex-score" min="0" max="100" step="1" /></label>
          <div class="ex-live" id="ex-live"></div>
        </div>
        <div class="ex-tip">点题号自动算分（<span class="q-btn right">对</span> / <span class="q-btn wrong">错</span>）；没有错题详情时，直接改「得分」即可只记分数</div>
        <div class="q-grid" id="ex-grid"></div>
        <div class="exam-actions">
          <button class="btn-primary" id="ex-save">保存这次成绩</button>
          <button class="btn-ghost" id="ex-clear">清空重选</button>
          <button class="btn-ghost" id="ex-cancel" hidden>取消修改</button>
        </div>
      </div>
    </section>

    <section class="exam-sec">
      <h3 class="sec-collapsible" id="ex-wrong-toggle">❌ 错题本 <span class="sec-sub" id="ex-wrong-sub"></span> <span class="tax-arrow">▸</span></h3>
      <div id="ex-wrong" style="display:none"></div>
    </section>

    <section class="exam-sec">
      <h3>📚 全部成绩 <span class="sec-sub">按讲次查看每次作答</span></h3>
      <div class="exam-toolbar">
        <button class="btn-ghost" id="ex-export">导出记录（JSON）</button>
      </div>
      <div id="ex-history"></div>
    </section>
  `

  // ---------- 记一次 ----------
  const $lec = el.querySelector('#ex-lecture')
  const $date = el.querySelector('#ex-date')
  const $grid = el.querySelector('#ex-grid')
  const $live = el.querySelector('#ex-live')
  const $score = el.querySelector('#ex-score')
  $date.value = new Date().toISOString().slice(0, 10)

  // 35 题按钮
  $grid.innerHTML = Array.from({ length: TOTAL }, (_, i) => i + 1)
    .map((q) => `<button class="q-btn" data-q="${q}">${q}</button>`)
    .join('')
  let wrong = new Set()
  let manualScore = null  // 非 null 时以手填分数为准（无错题详情，只记分数）
  let editingId = null   // 非 null 时表单处于「修改已有记录」模式
  const $title = el.querySelector('#ex-form-title')
  const $save = el.querySelector('#ex-save')
  const $cancel = el.querySelector('#ex-cancel')
  const paintEditState = () => {
    $title.textContent = editingId == null ? '📝 记一次' : `✏️ 修改记录 #${editingId}`
    $save.textContent = editingId == null ? '保存这次成绩' : '保存修改'
    $cancel.hidden = editingId == null
  }
  $cancel.addEventListener('click', () => {
    editingId = null
    wrong.clear()
    manualScore = null
    $lec.value = $lec.querySelector('option')?.value
    $date.value = new Date().toISOString().slice(0, 10)
    paintQ()
    paintLive()
    paintEditState()
  })
  const paintQ = () =>
    $grid.querySelectorAll('.q-btn').forEach((b) => {
      b.classList.toggle('wrong', wrong.has(Number(b.dataset.q)))
    })
  const paintLive = () => {
    const lost = [...wrong].reduce((s, q) => s + scoreOf(q), 0)
    const auto = fullScore - lost
    const score = manualScore ?? auto
    $score.value = String(score)
    let detail = ''
    if (manualScore != null && manualScore !== auto) {
      detail = `（按题号推算 ${auto} 分，以手填分数为准）`
    } else if (manualScore != null) {
      detail = `（与题号推算一致：扣 ${lost} 分）`
    } else if (wrong.size) {
      const part1 = [...wrong].filter((q) => q <= 5).length
      const part2 = wrong.size - part1
      detail = `（错 ${wrong.size} 题：1~5 题 ${part1} 个×2 分 + 6~35 题 ${part2} 个×3 分 = 扣 ${lost} 分）`
    }
    $live.innerHTML = `满分 ${fullScore}` + (detail ? ` <span class="ex-detail">${detail}</span>` : '')
  }
  $score.addEventListener('input', () => {
    const v = $score.value.trim()
    manualScore = v === '' ? null : Math.max(0, Math.min(fullScore, Math.round(Number(v) || 0)))
    paintLive()
  })
  $grid.addEventListener('click', (e) => {
    const b = e.target.closest('.q-btn')
    if (!b) return
    const q = Number(b.dataset.q)
    wrong.has(q) ? wrong.delete(q) : wrong.add(q)
    manualScore = null  // 重新点题号则回到自动算分
    paintQ()
    paintLive()
  })
  el.querySelector('#ex-clear').addEventListener('click', () => {
    wrong.clear()
    manualScore = null
    paintQ()
    paintLive()
  })
  el.querySelector('#ex-save').addEventListener('click', async () => {
    const lost = [...wrong].reduce((s, q) => s + scoreOf(q), 0)
    const rec = {
      lecture: Number($lec.value),
      date: $date.value || new Date().toISOString().slice(0, 10),
      score: manualScore ?? (fullScore - lost),
      wrong: [...wrong].sort((a, b) => a - b),
    }
    const btn = el.querySelector('#ex-save')
    btn.disabled = true
    try {
      if (editingId == null) {
        await api.examsAdd(rec)
      } else {
        await api.examsUpdate(editingId, rec)
      }
      editingId = null
      wrong.clear()
      manualScore = null
      paintQ()
      paintLive()
      paintEditState()
      await refresh()
    } catch (e) {
      alert(`保存失败：${e.message}（请确认后端服务在运行）`)
    } finally {
      btn.disabled = false
    }
  })
  paintLive()
  paintEditState()

  // ---------- 错题本（默认折叠；按讲次分组，展示原题与解析）----------
  el.querySelector('#ex-wrong-toggle').addEventListener('click', () => {
    const $w = el.querySelector('#ex-wrong')
    const arrow = el.querySelector('#ex-wrong-toggle .tax-arrow')
    const open = $w.style.display !== 'none'
    $w.style.display = open ? 'none' : ''
    if (arrow) arrow.textContent = open ? '▸' : '▾'
  })

  let wrongBank = null
  async function renderWrong() {
    const list = await fetchRecords(el)
    // (讲次, 题号) → 错误次数
    const agg = new Map()
    for (const r of list) {
      for (const q of r.wrong) {
        const k = `${r.lecture}-${q}`
        agg.set(k, (agg.get(k) || 0) + 1)
      }
    }
    const rows = [...agg.entries()]
      .map(([k, n]) => {
        const [lec, q] = k.split('-').map(Number)
        return { lec, q, n }
      })
    const $w = el.querySelector('#ex-wrong')
    const $sub = el.querySelector('#ex-wrong-sub')
    if (!rows.length) {
      if ($sub) $sub.textContent = '还没有错题记录'
      $w.innerHTML = '<div class="empty">还没有错题记录，保持下去！</div>'
      return
    }
    // 题目详情：答案+解析来自静态题库（测验平台错题导出），题干+选项来自后端作业卷库
    if (!wrongBank) {
      try {
        wrongBank = (await import('../data/wrongbank.js')).WRONG_BANK
      } catch {
        wrongBank = {}
      }
    }
    const lecs = [...new Set(rows.map((r) => r.lec))]
    let hwMap = {}
    let hwOk = false
    try {
      const batch = await api.homeworkBatch(lecs)
      hwMap = batch || {}
      hwOk = true
    } catch { /* 后端无作业卷端点（旧服务）时仅显示答案与解析 */ }

    const hot = rows.filter((r) => r.n >= 2).length
    if ($sub) $sub.textContent = `共 ${rows.length} 道 · 错 2 次以上 ${hot} 道 · 点击展开`

    const byLec = new Map()
    for (const r of rows) {
      ;(byLec.get(r.lec) || byLec.set(r.lec, []).get(r.lec)).push(r)
    }
    $w.innerHTML = [...byLec.entries()]
      .map(([lec, items]) => {
        items.sort((a, b) => b.n - a.n || a.q - b.q)
        return `
        <div class="wrong-group">
          <div class="wrong-group-head">
            <b>第${lec}讲</b> ${escapeHtml(lecTitle(lec))}
            <span class="his-meta">${items.length} 题 · 最多错 ${items[0].n} 次</span>
          </div>
          <div class="wrong-list">
          ${items.map((r) => wrongItemHtml(r, hwMap[r.lec], wrongBank[r.lec], hwOk)).join('')}
          </div>
        </div>`
      })
      .join('')
  }

  // 单道错题：题干/选项（作业卷 API）+ 答案/解析（错题导出题库）
  function wrongItemHtml(r, hwItems, bankItems, hwOk) {
    const hw = hwItems && hwItems.find ? hwItems.find((x) => x.qnum === r.q) : null
    const d = bankItems && bankItems[r.q]
    const answer = d && d.q ? String(d.q).trim() : ''
    // 选择题答案为字母时映射到选项文本
    let answerText = answer
    if (hw && hw.options && hw.options.length && /^[A-D]$/.test(answer)) {
      const opt = hw.options['ABCD'.indexOf(answer)]
      if (opt) answerText = `${answer}. ${opt}`
    }
    const stem = hw
      ? (hw.isCell ? '（表格填空题，原题见作业卷表格）' : hw.stem || '（题干缺失）')
      : hwOk
        ? '（该讲作业卷文件缺失，仅有答案与解析）'
        : '（后端未加载作业卷端点，请重启服务）'
    const opts = hw && hw.options && hw.options.length
      ? `<div class="wrong-options">${hw.options
          .map(
            (o, i) =>
              `<span class="wrong-opt${['A', 'B', 'C', 'D'][i] === answer ? ' correct' : ''}">${['A', 'B', 'C', 'D'][i]}. ${escapeHtml(o)}</span>`,
          )
          .join('')}</div>`
      : ''
    return `
    <div class="wrong-detail ${r.n >= 2 ? 'hot' : ''}">
      <div class="wrong-detail-head">
        <span class="wrong-n">×${r.n}</span>
        <span class="wrong-lq">第${r.q}题</span>
        <span class="wrong-stem">${escapeHtml(stem)}</span>
        ${answerText ? `<span class="wrong-ans">答案：${escapeHtml(answerText)}</span>` : ''}
      </div>
      ${opts}
      ${d && d.exp ? `<div class="wrong-detail-exp">${escapeHtml(d.exp)}</div>` : ''}
    </div>`
  }

  // ---------- 全部成绩 ----------
  async function renderHistory() {
    const list = (await fetchRecords(el)).slice().sort((a, b) => a.lecture - b.lecture || (a.date < b.date ? 1 : -1))
    const $h = el.querySelector('#ex-history')
    if (!list.length) {
      $h.innerHTML = '<div class="empty">还没有作答记录，先在上方记一次。</div>'
      return
    }
    const byLec = new Map()
    for (const r of list) {
      ;(byLec.get(r.lecture) || byLec.set(r.lecture, []).get(r.lecture)).push(r)
    }
    $h.innerHTML = [...byLec.entries()]
      .map(([lec, recs]) => {
        const best = Math.max(...recs.map((r) => r.score))
        const last = recs[0]
        return `
        <div class="his-group">
          <div class="his-head">
            <b>第${lec}讲</b> ${escapeHtml(lecTitle(lec))}
            <span class="his-meta">做了 ${recs.length} 次 · 最高 ${best} 分 · 最近 ${last.score} 分</span>
          </div>
          ${recs
            .map(
              (r) => `
            <div class="his-row" data-id="${r.id}">
              <span class="his-date">${escapeHtml(r.date)}</span>
              <span class="his-score ${r.score >= 90 ? 'good' : r.score >= 60 ? 'mid' : 'bad'}">${r.score} 分</span>
              <span class="his-wrong">${
                r.wrong.length
                  ? r.wrong.map((q) => `<span class="wrong-chip">第${q}题</span>`).join('')
                  : r.score >= fullScore
                    ? '<span class="all-right">全对 🎉</span>'
                    : '<span class="no-detail">未记错题</span>'
              }</span>
              <button class="his-edit" title="修改这条记录">改</button>
              <button class="his-del" title="删除这条记录">×</button>
            </div>`,
            )
            .join('')}
        </div>`
      })
      .join('')
  }

  // 删除单条 / 修改单条（填回上方表单）
  el.querySelector('#ex-history').addEventListener('click', async (e) => {
    const del = e.target.closest('.his-del')
    const edit = e.target.closest('.his-edit')
    if (!del && !edit) return
    const row = e.target.closest('.his-row')
    if (!row) return
    const id = row.dataset.id
    if (del) {
      if (!confirm('确定删除这条记录吗？')) return
      try {
        await api.examsDelete(id)
        if (editingId === Number(id)) editingId = null
        paintEditState()
        await refresh()
      } catch (err) {
        alert(`删除失败：${err.message}`)
      }
    } else if (edit) {
      const rec = (await fetchRecords(el)).find((r) => r.id === Number(id))
      if (!rec) return
      editingId = rec.id
      $lec.value = rec.lecture
      $date.value = rec.date
      wrong = new Set(rec.wrong)
      // 无错题详情的记录（只有分数）：以手填分数模式填回
      manualScore = rec.wrong.length ? null : rec.score
      paintQ()
      paintLive()
      paintEditState()
      el.querySelector('.exam-sec').scrollIntoView({ behavior: 'smooth' })
    }
  })

  // 导出
  el.querySelector('#ex-export').addEventListener('click', async () => {
    const blob = new Blob([JSON.stringify(await fetchRecords(el), null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `作业成绩-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  })

  // 统一刷新：迁移旧数据 → 拉后端记录 → 重渲染
  async function refresh() {
    await migrateOnce()
    await Promise.all([renderWrong(), renderHistory()])
  }
  refresh()
}

// 拉记录（带错误提示）；挂载时 el 已在 DOM
async function fetchRecords(el) {
  try {
    return await api.examsList()
  } catch (e) {
    const tip = el.querySelector('.view-head p')
    if (tip && !tip.dataset.err) {
      tip.dataset.err = '1'
      tip.innerHTML += `<br><span style="color:#b91c1c">⚠ 成绩服务连接失败（${escapeHtml(e.message)}），检查后端是否在运行。</span>`
    }
    return []
  }
}

import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'
import {
  PLAN_ADD_TYPES, appendPlanItem, removePlanItem, applyVocabTarget,
  vocabRowTitle, hwMismatchWarning, completionLine, copyTasks,
} from './planAdd.js'

const DAY = ['一', '二', '三', '四', '五', '六', '日']

function hasPlanItems(tasks) {
  const t = tasks || {}
  return Boolean(
    (t.lectures || []).length || (t.hw_papers || []).length || (t.focus_kps || []).length
    || (t.articles || []).length || (t.fce || []).length || (t.vocab_papers || []).length
    || (t.videos || []).length || t.vocab_goal != null || t.speak
    || Object.keys(t.reading || {}).length)
}

function includesNum(arr, n) {
  return (arr || []).map(Number).includes(Number(n))
}

/** 教师周编辑器：一份清单 + 添加，右边是学生课表预览。 */
export async function openWeekEditor({
  ws, student, week, ed, recentWeeks, weekLabel, onSaved,
}) {
  let topicOn = true
  try {
    const rules = (await api.planRules({ user: student })).rules || []
    const row = rules.find((r) => r.id === 'topic_counts_lecture')
    if (row) topicOn = Boolean(row.value)
  } catch { /* 读不到规则时按默认：专题学完算这讲完成 */ }

  let tasks = copyTasks(week?.tasks || {})
  let openKey = null
  let addOpen = !hasPlanItems(tasks)
  let addType = ''
  let showAll = false
  let videoPick = []
  let readBook = ''
  let readChapters = []
  let previewTimer = 0
  let previewSeq = 0

  const books = ed.books || []
  const lectures = ed.lectures || []
  const articles = ed.articles || []
  const videos = ed.videos || []
  const fceParts = ed.fce_parts || []
  const papers = ed.goal_assets?.vocab_papers || []
  const hwPapers = (ed.goal_assets?.lectures || []).filter((l) => l.has_paper)
  const vLevels = ed.goal_assets?.vocab_levels || []
  const vocabWeek = ed.vocab_week || {}
  const goals = ed.goals || {}
  const done = ed.done || {}

  const dlg = document.createElement('div')
  dlg.className = 'plan-editor'
  document.body.appendChild(dlg)
  const close = () => dlg.remove()

  function lecTitle(n) {
    const l = lectures.find((x) => x.number === Number(n))
      || hwPapers.find((x) => x.number === Number(n))
    return l?.title ? `第${n}讲 ${l.title}` : `第${n}讲`
  }

  function levelStat(level) {
    const row = (vocabWeek.levels || []).find((x) => x.level === Number(level))
    const asset = vLevels.find((x) => x.level === Number(level))
    return {
      words: asset?.words || row?.words || 0,
      base: Number(row?.base) || 0,
      gained: Number(row?.gained) || 0,
    }
  }

  function defaultLevel() {
    if (tasks.vocab_lv != null) return Number(tasks.vocab_lv)
    if (vocabWeek.practicing_level != null) return Number(vocabWeek.practicing_level)
    const withWords = vLevels.find((v) => v.words)
    return withWords ? withWords.level : (vLevels[0]?.level ?? 0)
  }

  function vocabCountFor(level) {
    if (tasks.vocab_goal == null) return 40
    const base = tasks.vocab_lv == null && Number(level) === defaultLevel()
      ? (Number(vocabWeek.base_all) || 0)
      : levelStat(level).base
    const n = Number(tasks.vocab_goal) - base
    return n > 0 ? n : 40
  }

  function vocabHint(level, count) {
    const s = levelStat(level)
    const shown = Math.max(0, Math.round(Number(count) || 0))
    const bits = [
      `L${level} 一共 ${s.words} 个词。周初已经会 ${s.base} 个，本周又新掌握 ${s.gained} 个。`,
      `进度条看的就是「本周新掌握」：大约 ${Math.min(s.gained, shown)}/${shown || 0}。周初会的不算进这周。`,
    ]
    const practicing = vocabWeek.practicing_level
    if (practicing != null && Number(practicing) !== Number(level)) {
      bits.push(`孩子最近在背 L${practicing}。这里选的是 L${level}，只数这一级里新会的词。`)
    } else if (practicing != null) {
      bits.push(`孩子最近就在背 L${practicing}。`)
    }
    const left = Math.max(0, s.words - s.base)
    if (shown > 0 && s.words > 0 && shown > left) {
      bits.push(`这一级还剩 ${left} 个没掌握，本周目标比剩下的多。`)
    }
    return bits.join('')
  }

  function levelOptions(selected) {
    const levels = vLevels.length
      ? vLevels
      : [0, 1, 2, 3, 4, 5].map((level) => ({ level, words: 0 }))
    return levels.map((v) =>
      `<option value="${v.level}" ${Number(selected) === Number(v.level) ? 'selected' : ''}>L${v.level}（${v.words || 0} 词）</option>`).join('')
  }

  function readingTitle(bookId, value) {
    const b = books.find((x) => String(x.id) === String(bookId))
    const name = String(b?.title || bookId).slice(0, 18)
    if (!Array.isArray(value)) return `泛读《${name}》· 旧目标 ${Number(value) || 0}%`
    const optMap = new Map((b?.chapter_opts || []).map((c) => [c.idx, c]))
    const nos = value.map((i) => optMap.get(i)?.no).filter((n) => n != null)
    const rng = !nos.length ? `${value.length} 章`
      : nos.length > 2 ? `第${Math.min(...nos)}–${Math.max(...nos)}章`
      : nos.map((n) => `第${n}章`).join('、')
    return `泛读《${name}》${rng}`
  }

  function rows() {
    const out = []
    for (const n of [...(tasks.lectures || [])].sort((a, b) => a - b)) {
      out.push({ key: `lecture:${n}`, type: 'lecture', lecture: n, title: `语法课 · ${lecTitle(n)}` })
    }
    for (const n of [...(tasks.hw_papers || [])].sort((a, b) => a - b)) {
      out.push({ key: `hw:${n}`, type: 'hw_paper', lecture: n, title: `作业卷 · ${lecTitle(n)}` })
    }
    for (const n of [...(tasks.focus_kps || [])].sort((a, b) => a - b)) {
      out.push({ key: `focus:${n}`, type: 'focus_kp', lecture: n, title: `微练习 · ${lecTitle(n)}` })
    }
    if (tasks.vocab_goal != null) {
      out.push({ key: 'vocab', type: 'vocab', title: `🔤 ${vocabRowTitle(tasks, vocabWeek)}` })
    }
    for (const vid of tasks.videos || []) {
      const v = videos.find((x) => x.vid === vid)
      out.push({
        key: `video:${vid}`, type: 'video', vid,
        title: `视频 · ${String(v?.title || vid).slice(0, 28)}`,
      })
    }
    for (const [id, val] of Object.entries(tasks.reading || {})) {
      out.push({
        key: `reading:${id}`, type: 'reading', book_id: id, chapters: val,
        title: `📖 ${readingTitle(id, val)}`,
      })
    }
    for (const label of tasks.fce || []) {
      out.push({ key: `fce:${label}`, type: 'fce', label, title: `FCE · ${label}` })
    }
    for (const id of tasks.articles || []) {
      const a = articles.find((x) => x.id === id)
      out.push({
        key: `article:${id}`, type: 'article', article_id: id,
        title: `精读 · ${a?.title || ('#' + id)}`,
      })
    }
    for (const id of tasks.vocab_papers || []) {
      const p = papers.find((x) => x.paper_id === id)
      const head = p && Number(p.level) < 0 ? `已学混合卷 ${id}` : `单词试卷 ${id}`
      out.push({ key: `paper:${id}`, type: 'paper', paper_id: id, title: head })
    }
    if (tasks.speak) out.push({ key: 'speak', type: 'speak', title: `朗读 ${tasks.speak} 篇` })
    return out
  }

  function goalish(type, id) {
    if (type === 'lecture' || type === 'focus_kp') {
      return includesNum(goals.lectures, id) && !includesNum(done.lectures, id)
    }
    if (type === 'hw_paper') return !includesNum(done.hw_papers, id)
    if (type === 'fce') {
      return (goals.fce_parts || []).includes(id) && !(done.fce_parts || []).includes(id)
    }
    if (type === 'article') return !includesNum(done.articles, id)
    if (type === 'paper') return (goals.vocab_papers || []).includes(id)
    if (type === 'reading') {
      return includesNum(goals.reading, id) && !includesNum(done.books, id)
    }
    return true
  }

  function takenOf(type, value) {
    if (type === 'lecture') return includesNum(tasks.lectures, value)
    if (type === 'hw_paper') return includesNum(tasks.hw_papers, value)
    if (type === 'focus_kp') return includesNum(tasks.focus_kps, value)
    if (type === 'article') return includesNum(tasks.articles, value)
    if (type === 'fce') return (tasks.fce || []).includes(value)
    if (type === 'paper') return (tasks.vocab_papers || []).includes(value)
    if (type === 'video') return (tasks.videos || []).includes(value)
    if (type === 'reading') return Object.prototype.hasOwnProperty.call(tasks.reading || {}, String(value))
    return false
  }

  function pickItems(type) {
    if (type === 'lecture' || type === 'focus_kp') {
      return lectures.map((l) => ({
        value: String(l.number),
        label: lecTitle(l.number),
        taken: takenOf(type, l.number),
        goalish: goalish(type, l.number),
        mark: includesNum(done.lectures, l.number) ? '已学过' : '',
      }))
    }
    if (type === 'hw_paper') {
      const pool = hwPapers.length ? hwPapers : lectures
      return pool.map((l) => ({
        value: String(l.number),
        label: lecTitle(l.number),
        taken: takenOf(type, l.number),
        goalish: goalish(type, l.number),
        mark: includesNum(done.hw_papers, l.number) ? '卷已考过' : '',
      }))
    }
    if (type === 'article') {
      return articles.map((a) => ({
        value: String(a.id),
        label: a.title || ('#' + a.id),
        taken: takenOf(type, a.id),
        goalish: goalish(type, a.id),
        mark: includesNum(done.articles, a.id) ? '已朗读' : '',
      }))
    }
    if (type === 'fce') {
      return fceParts.map((p) => ({
        value: p.label,
        label: p.label,
        taken: takenOf(type, p.label),
        goalish: goalish(type, p.label),
        mark: (done.fce_parts || []).includes(p.label) ? '已做过' : '',
      }))
    }
    if (type === 'paper') {
      return papers.map((p) => ({
        value: p.paper_id,
        label: Number(p.level) < 0 ? `已学混合卷 ${p.paper_id}` : `${p.paper_id}`,
        taken: takenOf(type, p.paper_id),
        goalish: goalish(type, p.paper_id),
        mark: '',
      }))
    }
    return []
  }

  function paint() {
    const prev = dlg.querySelector('.plan-editor-body')
    const sc = prev?.scrollTop || 0
    const list = rows()
    const warn = hwMismatchWarning(tasks, recentWeeks, ws)
    dlg.innerHTML = `
      <div class="plan-editor-mask"></div>
      <div class="plan-editor-body card pe-body pe-week">
        <h3>编辑本周 <i>· ${escapeHtml(weekLabel || ws)} · ${escapeHtml(student || '')}</i></h3>
        <p class="pe-lead">左边是这周要做的事，点一项就能改或去掉。右边是学生周一到周日课表上会看到的样子。</p>
        <div class="pe-layout">
          <div class="pe-main">
            <div class="pe-list">
              ${list.length
                ? list.map(rowHtml).join('')
                : '<p class="muted pe-empty">这周还没有安排。点下面的「添加」，把语法课、作业卷、本周要新掌握的词放进来。</p>'}
            </div>
            ${addOpen
              ? addPanelHtml()
              : '<button class="chip" id="pe-add-open" type="button">＋ 添加</button>'}
            <div class="pe-warn" id="pe-warn" ${warn ? '' : 'hidden'}>${escapeHtml(warn)}</div>
            <div class="chip-row">
              <button class="btn-primary" id="pe-save" type="button">保存</button>
              <button class="chip" id="pe-cancel" type="button">取消</button>
            </div>
          </div>
          <aside class="pe-preview-card">
            <b>学生课表预览</b>
            <p class="pe-lead">按正式课表的办法摊到每一天：词汇个数分到工作日，视频工作日每天 1 个。</p>
            <div id="pe-preview-body"><p class="muted">正在按课表摊开…</p></div>
          </aside>
        </div>
      </div>`
    const next = dlg.querySelector('.plan-editor-body')
    if (next) next.scrollTop = sc
    bind()
    schedulePreview()
  }

  function rowHtml(r) {
    const open = openKey === r.key
    return `<div class="pe-row ${open ? 'open' : ''}" data-row="${escAttr(r.key)}">
      <div class="pe-row-main">
        <div class="pe-row-title">${escapeHtml(r.title)}</div>
        <span class="pe-done-line">${escapeHtml(completionLine(r.type, { topicCounts: topicOn }))}</span>
        ${open ? `<div class="pe-edit" data-stop>${editHtml(r)}</div>` : ''}
      </div>
      <button class="chip pe-row-del" type="button" data-del="${escAttr(r.key)}">去掉</button>
    </div>`
  }

  function editHtml(r) {
    if (r.type === 'vocab') return vocabFormHtml('pe-v', true)
    if (r.type === 'speak') {
      return `<label class="pe-speak">本周朗读
        <input id="pe-speak-n" type="number" min="1" max="14" value="${Number(tasks.speak) || 1}"> 篇</label>`
    }
    if (r.type === 'lecture' || r.type === 'hw_paper' || r.type === 'focus_kp') {
      return `<label class="pe-speak">改成 <select id="pe-swap-lec">${lecOptions(r)}</select></label>`
    }
    if (r.type === 'reading') return readingEditHtml(r)
    if (r.type === 'article') return `<label class="pe-speak">改成 <select id="pe-swap-art">${articleOptions(r.article_id)}</select></label>`
    if (r.type === 'fce') return `<label class="pe-speak">改成 <select id="pe-swap-fce">${fceOptions(r.label)}</select></label>`
    if (r.type === 'paper') return `<label class="pe-speak">改成 <select id="pe-swap-paper">${paperOptions(r.paper_id)}</select></label>`
    if (r.type === 'video') return `<label class="pe-speak">改成 <select id="pe-swap-vid">${videoOptions(r.vid)}</select></label>`
    return ''
  }

  function lecOptions(r) {
    const pool = r.type === 'hw_paper' && hwPapers.length ? hwPapers : lectures
    const cur = Number(r.lecture)
    const opts = []
    if (!pool.some((l) => Number(l.number) === cur)) {
      opts.push(`<option value="${cur}" selected>${escapeHtml(lecTitle(cur))}</option>`)
    }
    for (const l of pool) {
      const n = Number(l.number)
      const taken = takenOf(r.type, n) && n !== cur
      opts.push(`<option value="${n}" ${n === cur ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(lecTitle(n))}${taken ? '（已在清单）' : ''}</option>`)
    }
    return opts.join('')
  }

  function articleOptions(cur) {
    return articles.map((a) => {
      const taken = takenOf('article', a.id) && Number(a.id) !== Number(cur)
      return `<option value="${a.id}" ${Number(a.id) === Number(cur) ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(a.title || ('#' + a.id))}</option>`
    }).join('')
  }

  function fceOptions(cur) {
    return fceParts.map((p) => {
      const taken = takenOf('fce', p.label) && p.label !== cur
      return `<option value="${escAttr(p.label)}" ${p.label === cur ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(p.label)}</option>`
    }).join('')
  }

  function paperOptions(cur) {
    return papers.map((p) => {
      const taken = takenOf('paper', p.paper_id) && p.paper_id !== cur
      const label = Number(p.level) < 0 ? `已学混合卷 ${p.paper_id}` : p.paper_id
      return `<option value="${escAttr(p.paper_id)}" ${p.paper_id === cur ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(label)}</option>`
    }).join('')
  }

  function videoOptions(cur) {
    return videos.map((v) => {
      const taken = takenOf('video', v.vid) && v.vid !== cur
      return `<option value="${escAttr(v.vid)}" ${v.vid === cur ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(String(v.title || v.vid).slice(0, 40))}</option>`
    }).join('')
  }

  function readingEditHtml(r) {
    const b = books.find((x) => String(x.id) === String(r.book_id))
    const opts = b?.chapter_opts || []
    const cur = Array.isArray(r.chapters) ? r.chapters : []
    if (!opts.length) return '<p class="muted">这本书没有可选章节。</p>'
    return `<div class="pe-chchips">${opts.map((c) =>
      `<button type="button" class="chip pe-chchip ${cur.includes(c.idx) ? 'primary' : ''}" data-edit-ch="${c.idx}">第${c.no}章</button>`).join('')}</div>
      ${Array.isArray(r.chapters) ? '' : '<p class="pe-wiz-hint">这是以前按百分比排的。点一章，就改成按章选。</p>'}`
  }

  function vocabFormHtml(prefix, inList) {
    const level = inList && tasks.vocab_lv != null ? Number(tasks.vocab_lv) : defaultLevel()
    const count = inList ? vocabCountFor(level) : 40
    const legacy = inList && tasks.vocab_lv == null && tasks.vocab_goal != null
    const reached = inList && !legacy && tasks.vocab_goal != null
      && levelStat(level).base >= Number(tasks.vocab_goal)
    const s = levelStat(level)
    let hint = vocabHint(level, count)
    if (legacy) {
      const base = Number(vocabWeek.base_all) || 0
      const goal = Number(tasks.vocab_goal)
      const n = Math.max(0, goal - base)
      hint = base >= goal
        ? '这周还没指定级别，按全部单词算，周初已经达到目标，进度是满格。选好级别再改个数，之后只数那一级。'
        : `这周还没指定级别，进度目前按全部单词算：本周新掌握大约 ${n} 个（目标 ${goal} 减去周初已会 ${base}）。选好级别再改个数，之后只数那一级。`
    } else if (reached) {
      hint = `周初已经达到原来的目标，进度现在是满格。改上面的个数后，进度条和右边课表按新的「本周新掌握」重算。L${level} 一共 ${s.words} 个词，周初已会 ${s.base} 个，本周已新掌握 ${s.gained} 个。`
    }
    return `<div class="pe-vocab-row">
        <span>本周新掌握</span>
        <select id="${prefix}-lv">${levelOptions(level)}</select>
        <span>词</span>
        <input id="${prefix}-n" type="number" min="1" max="5000" value="${count}">
        <span>个</span>
        ${inList ? '' : '<button class="btn-primary" id="pe-vocab-add" type="button">放入清单</button>'}
      </div>
      <p class="pe-wiz-hint" id="${prefix}-hint">${escapeHtml(hint)}</p>`
  }

  function addPanelHtml() {
    return `<div class="pe-add">
      <div class="pe-add-head"><b>添加</b>
        <button class="chip" id="pe-add-close" type="button">收起</button>
      </div>
      <div class="pe-type-grid">${PLAN_ADD_TYPES.map((t) =>
        `<button type="button" class="pe-type ${addType === t.id ? 'on' : ''}" data-type="${t.id}"><b>${escapeHtml(t.label)}</b><i>${escapeHtml(t.hint)}</i></button>`).join('')}</div>
      <div id="pe-add-pick">${addType ? addPickHtml() : '<p class="pe-wiz-hint">先选一种。点具体内容就会进左边的清单，这时还没保存。</p>'}</div>
    </div>`
  }

  function addPickHtml() {
    if (addType === 'vocab') {
      if (tasks.vocab_goal != null) {
        return '<p class="pe-wiz-hint">这周已经有词汇这一行。点它就能改「本周新掌握」的个数。</p>'
      }
      return vocabFormHtml('pe-add', false)
    }
    if (addType === 'speak') {
      if (tasks.speak) return '<p class="pe-wiz-hint">朗读篇数已经在清单里。点那一行可以改篇数。</p>'
      return `<label class="pe-speak">每周 <input id="pe-add-speak" type="number" min="1" max="14" value="2"> 篇</label>
        <div><button class="btn-primary" id="pe-speak-add" type="button">放入清单</button></div>`
    }
    if (addType === 'reading') return readingAddHtml()
    if (addType === 'video') return videoAddHtml()
    const items = pickItems(addType)
    const filtered = showAll ? items : items.filter((it) => it.goalish)
    const use = filtered.length || showAll ? filtered : items
    const note = !showAll && !filtered.length && items.length
      ? '<p class="pe-wiz-hint">总目标里没有还没完成的，下面先列出全部。</p>' : ''
    if (!items.length) return '<p class="muted">没有可选项。先到备课里准备内容。</p>'
    return `${note}
      <label class="pe-speak"><input type="checkbox" id="pe-show-all" ${showAll ? 'checked' : ''}/> 显示全部（含已完成、总目标以外）</label>
      <div class="pe-wiz-picks">${use.map((it) =>
        `<button type="button" class="pe-pick ${it.taken ? 'taken' : ''}" data-pick="${escAttr(it.value)}" ${it.taken ? 'disabled' : ''}>${escapeHtml(it.label)}${it.taken ? ' · 已在本周' : ''}${it.mark ? ` · ${escapeHtml(it.mark)}` : ''}</button>`).join('')}</div>`
  }

  function readingAddHtml() {
    const avail = books.filter((b) => !takenOf('reading', b.id))
    const b = books.find((x) => String(x.id) === String(readBook))
    const opts = b?.chapter_opts || []
    return `<select id="pe-add-book">
        <option value="">选择书目…</option>
        ${avail.map((book) => `<option value="${book.id}" ${String(readBook) === String(book.id) ? 'selected' : ''}>${escapeHtml(String(book.title || '').slice(0, 36))}</option>`).join('')}
      </select>
      ${b ? `<div class="pe-chchips">${opts.map((c) =>
        `<button type="button" class="chip pe-chchip ${readChapters.includes(c.idx) ? 'primary' : ''}" data-add-ch="${c.idx}">第${c.no}章</button>`).join('') || '<p class="muted">这本书没有可选章节。</p>'}</div>
        <button class="btn-primary" id="pe-read-add" type="button">放入清单</button>` : ''}`
  }

  function videoAddHtml() {
    const list = showAll ? videos : videos.filter((v) => !takenOf('video', v.vid))
    if (!videos.length) return '<p class="muted">还没有上架视频。先到备课里添加。</p>'
    return `<label class="pe-speak"><input type="checkbox" id="pe-show-all" ${showAll ? 'checked' : ''}/> 连本周已有的视频一起显示</label>
      <div class="pe-wiz-picks">${(list.length ? list : videos).map((v) => {
        const on = videoPick.includes(v.vid)
        const taken = takenOf('video', v.vid)
        return `<button type="button" class="pe-pick ${on ? 'on' : ''} ${taken ? 'taken' : ''}" data-vid-pick="${escAttr(v.vid)}" ${taken ? 'disabled' : ''}>${escapeHtml(String(v.title || v.vid).slice(0, 42))}${taken ? ' · 已在本周' : ''}</button>`
      }).join('')}</div>
      <button class="btn-primary" id="pe-vid-add" type="button" ${videoPick.length ? '' : 'disabled'}>放入清单（${videoPick.length}）</button>`
  }

  function placeVocab(level, count) {
    const s = levelStat(level)
    tasks = applyVocabTarget(tasks, { level, count, base: s.base, words: s.words })
    openKey = tasks.vocab_goal != null ? 'vocab' : null
    addOpen = false
    paint()
  }

  function bind() {
    dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
    dlg.querySelector('#pe-cancel').addEventListener('click', close)
    dlg.querySelector('#pe-save').addEventListener('click', save)
    const addBtn = dlg.querySelector('#pe-add-open')
    if (addBtn) addBtn.addEventListener('click', () => {
      addOpen = true
      openKey = null
      paint()
    })
    const addClose = dlg.querySelector('#pe-add-close')
    if (addClose) addClose.addEventListener('click', () => { addOpen = false; paint() })

    dlg.querySelectorAll('[data-row]').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-del]') || e.target.closest('[data-stop]')) return
        const key = el.dataset.row
        openKey = openKey === key ? null : key
        addOpen = false
        paint()
      })
    })
    dlg.querySelectorAll('[data-del]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        removeKey(btn.dataset.del)
      })
    })
    dlg.querySelectorAll('[data-type]').forEach((btn) => {
      btn.addEventListener('click', () => {
        addType = btn.dataset.type
        showAll = false
        videoPick = []
        readBook = ''
        readChapters = []
        paint()
      })
    })
    const show = dlg.querySelector('#pe-show-all')
    if (show) show.addEventListener('change', () => { showAll = show.checked; paint() })
    dlg.querySelectorAll('[data-pick]').forEach((btn) => {
      btn.addEventListener('click', () => { if (!btn.disabled) applyPick(btn.dataset.pick) })
    })
    bindVocab('pe-v', true)
    bindVocab('pe-add', false)
    const vocabAdd = dlg.querySelector('#pe-vocab-add')
    if (vocabAdd) vocabAdd.addEventListener('click', () => {
      const form = readVocab('pe-add')
      if (form) placeVocab(form.level, form.count)
    })
    const speakAdd = dlg.querySelector('#pe-speak-add')
    if (speakAdd) speakAdd.addEventListener('click', () => {
      const n = Math.min(14, Math.max(1, Number(dlg.querySelector('#pe-add-speak').value) || 1))
      tasks = appendPlanItem(tasks, { type: 'speak', n })
      openKey = 'speak'
      addOpen = false
      paint()
    })
    const speakN = dlg.querySelector('#pe-speak-n')
    if (speakN) speakN.addEventListener('input', () => {
      const n = Math.min(14, Math.max(1, Number(speakN.value) || 1))
      tasks.speak = n
      const title = dlg.querySelector('[data-row="speak"] .pe-row-title')
      if (title) title.textContent = `朗读 ${n} 篇`
      schedulePreview()
    })
    const swapLec = dlg.querySelector('#pe-swap-lec')
    if (swapLec) swapLec.addEventListener('change', () => swapScalar('lec', Number(swapLec.value)))
    const swapArt = dlg.querySelector('#pe-swap-art')
    if (swapArt) swapArt.addEventListener('change', () => swapScalar('art', Number(swapArt.value)))
    const swapFce = dlg.querySelector('#pe-swap-fce')
    if (swapFce) swapFce.addEventListener('change', () => swapScalar('fce', swapFce.value))
    const swapPaper = dlg.querySelector('#pe-swap-paper')
    if (swapPaper) swapPaper.addEventListener('change', () => swapScalar('paper', swapPaper.value))
    const swapVid = dlg.querySelector('#pe-swap-vid')
    if (swapVid) swapVid.addEventListener('change', () => swapScalar('vid', swapVid.value))
    dlg.querySelectorAll('[data-edit-ch]').forEach((chip) => {
      chip.addEventListener('click', (e) => { e.stopPropagation(); toggleChapter(Number(chip.dataset.editCh)) })
    })
    const bookSel = dlg.querySelector('#pe-add-book')
    if (bookSel) bookSel.addEventListener('change', () => {
      readBook = bookSel.value
      const b = books.find((x) => String(x.id) === String(readBook))
      readChapters = (b?.chapter_opts || []).map((c) => c.idx)
      paint()
    })
    dlg.querySelectorAll('[data-add-ch]').forEach((chip) => {
      chip.addEventListener('click', () => {
        const idx = Number(chip.dataset.addCh)
        const i = readChapters.indexOf(idx)
        if (i >= 0) readChapters.splice(i, 1)
        else readChapters.push(idx)
        readChapters.sort((a, b) => a - b)
        paint()
      })
    })
    const readAdd = dlg.querySelector('#pe-read-add')
    if (readAdd) readAdd.addEventListener('click', () => {
      if (!readBook || !readChapters.length) return
      tasks = appendPlanItem(tasks, { type: 'reading', book_id: readBook, chapters: [...readChapters] })
      openKey = `reading:${readBook}`
      addOpen = false
      paint()
    })
    dlg.querySelectorAll('[data-vid-pick]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const vid = btn.dataset.vidPick
        const i = videoPick.indexOf(vid)
        if (i >= 0) videoPick.splice(i, 1)
        else videoPick.push(vid)
        paint()
      })
    })
    const vidAdd = dlg.querySelector('#pe-vid-add')
    if (vidAdd) vidAdd.addEventListener('click', () => {
      for (const vid of videoPick) tasks = appendPlanItem(tasks, { type: 'video', vid })
      videoPick = []
      addOpen = false
      paint()
    })
  }

  function bindVocab(prefix, live) {
    const lv = dlg.querySelector(`#${prefix}-lv`)
    const n = dlg.querySelector(`#${prefix}-n`)
    const hint = dlg.querySelector(`#${prefix}-hint`)
    if (!lv || !n) return
    const refreshHint = () => {
      if (hint) hint.textContent = vocabHint(Number(lv.value), Number(n.value))
    }
    lv.addEventListener('change', () => {
      refreshHint()
      if (live) commitLiveVocab()
    })
    n.addEventListener('input', () => {
      refreshHint()
      if (live) commitLiveVocab()
    })
  }

  function readVocab(prefix) {
    const lv = dlg.querySelector(`#${prefix}-lv`)
    const n = dlg.querySelector(`#${prefix}-n`)
    if (!lv || !n) return null
    return {
      level: Number(lv.value),
      count: Math.min(5000, Math.max(1, Math.round(Number(n.value) || 1))),
    }
  }

  function commitLiveVocab() {
    const form = readVocab('pe-v')
    if (!form) return
    const s = levelStat(form.level)
    tasks = applyVocabTarget(tasks, {
      level: form.level, count: form.count, base: s.base, words: s.words,
    })
    const title = dlg.querySelector('[data-row="vocab"] .pe-row-title')
    if (title) title.textContent = `🔤 ${vocabRowTitle(tasks, vocabWeek)}`
    const warn = hwMismatchWarning(tasks, recentWeeks, ws)
    const box = dlg.querySelector('#pe-warn')
    if (box) {
      box.textContent = warn
      box.hidden = !warn
    }
    schedulePreview()
  }

  function applyPick(value) {
    if (addType === 'lecture' || addType === 'hw_paper' || addType === 'focus_kp') {
      tasks = appendPlanItem(tasks, { type: addType, lecture: Number(value) })
    } else if (addType === 'article') {
      tasks = appendPlanItem(tasks, { type: 'article', article_id: Number(value) })
    } else if (addType === 'fce') {
      tasks = appendPlanItem(tasks, { type: 'fce', label: value })
    } else if (addType === 'paper') {
      tasks = appendPlanItem(tasks, { type: 'paper', paper_id: value })
    }
    paint()
  }

  function removeKey(key) {
    const row = rows().find((r) => r.key === key)
    if (!row) return
    const item = { type: row.type }
    if (row.lecture != null) item.lecture = row.lecture
    if (row.article_id != null) item.article_id = row.article_id
    if (row.label) item.label = row.label
    if (row.paper_id) item.paper_id = row.paper_id
    if (row.vid) item.vid = row.vid
    if (row.book_id != null) item.book_id = row.book_id
    tasks = removePlanItem(tasks, item)
    if (openKey === key) openKey = null
    paint()
  }

  function swapScalar(kind, value) {
    const row = rows().find((r) => r.key === openKey)
    if (!row) return
    if (kind === 'lec') {
      tasks = removePlanItem(tasks, { type: row.type, lecture: row.lecture })
      tasks = appendPlanItem(tasks, { type: row.type, lecture: value })
      const prefix = row.type === 'hw_paper' ? 'hw' : row.type === 'focus_kp' ? 'focus' : 'lecture'
      openKey = `${prefix}:${value}`
    } else if (kind === 'art') {
      tasks = removePlanItem(tasks, { type: 'article', article_id: row.article_id })
      tasks = appendPlanItem(tasks, { type: 'article', article_id: value })
      openKey = `article:${value}`
    } else if (kind === 'fce') {
      tasks = removePlanItem(tasks, { type: 'fce', label: row.label })
      tasks = appendPlanItem(tasks, { type: 'fce', label: value })
      openKey = `fce:${value}`
    } else if (kind === 'paper') {
      tasks = removePlanItem(tasks, { type: 'paper', paper_id: row.paper_id })
      tasks = appendPlanItem(tasks, { type: 'paper', paper_id: value })
      openKey = `paper:${value}`
    } else if (kind === 'vid') {
      tasks = removePlanItem(tasks, { type: 'video', vid: row.vid })
      tasks = appendPlanItem(tasks, { type: 'video', vid: value })
      openKey = `video:${value}`
    }
    paint()
  }

  function toggleChapter(idx) {
    const row = rows().find((r) => r.key === openKey)
    if (!row || row.type !== 'reading') return
    const b = books.find((x) => String(x.id) === String(row.book_id))
    const cur = tasks.reading?.[row.book_id]
    let arr = Array.isArray(cur) ? [...cur] : (b?.chapter_opts || []).map((c) => c.idx)
    const i = arr.indexOf(idx)
    if (i >= 0) arr.splice(i, 1)
    else arr.push(idx)
    arr.sort((a, c) => a - c)
    if (!arr.length) {
      tasks = removePlanItem(tasks, { type: 'reading', book_id: row.book_id })
      openKey = null
    } else {
      tasks = appendPlanItem(tasks, { type: 'reading', book_id: row.book_id, chapters: arr })
    }
    paint()
  }

  function schedulePreview() {
    clearTimeout(previewTimer)
    previewTimer = setTimeout(loadPreview, 220)
  }

  async function loadPreview() {
    const seq = ++previewSeq
    const host = dlg.querySelector('#pe-preview-body')
    if (!host) return
    try {
      const data = await api.planWeekPreview({ week_start: ws, user: student, tasks })
      if (seq !== previewSeq || !dlg.isConnected) return
      const days = data.days || []
      host.innerHTML = `<div class="pe-pv-grid">${days.map((d, i) => {
        const lines = (d.tasks || []).map((t) => `<span>${escapeHtml(t.text || '')}</span>`).join('')
        return `<div class="pe-pv-day ${d.future ? 'future' : ''}"><b>周${DAY[i] || ''}</b>${lines || '<i>这天没有摊到任务</i>'}</div>`
      }).join('')}</div>`
    } catch (e) {
      if (seq !== previewSeq || !dlg.isConnected) return
      host.innerHTML = `<p class="muted">课表预览没加载出来：${escapeHtml(e.message || '')}</p>`
    }
  }

  async function save() {
    try {
      await api.planWeekPut(ws, { tasks, notes: week?.notes || '', user: student })
      close()
      if (onSaved) await onSaved()
    } catch (e) {
      alert('保存失败：' + (e.message || ''))
    }
  }

  paint()
}

import { api } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'
import { vocabPaperShort } from '../fmt.js'
import {
  PLAN_ADD_TYPES, appendPlanItem, removePlanItem, applyVocabTarget,
  vocabRowTitle, hwMismatchWarning, completionLine, copyTasks,
  defaultVocabLevel, vocabLevelWarning,
  migrateLegacyUnits, tasksForSave, appendVideoUnit,
  appendNce2Unit, removeVideoUnit, removeNce2Unit,
  toggleMicroGroup,
  videoUnitLine, nce2UnitLine,
  videoUnitVids, nce2UnitLessons,
  boostDetailsToggle, boostReady,
} from './planAdd.js'

const DAY = ['一', '二', '三', '四', '五', '六', '日']

function hasPlanItems(tasks) {
  const t = tasks || {}
  return Boolean(
    (t.lectures || []).length || (t.hw_papers || []).length || (t.focus_kps || []).length
    || (t.video_units || []).length
    || (t.nce2_units || []).length || t.micro != null
    || (t.fce || []).length || (t.vocab_papers || []).length
    || t.vocab_goal != null
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

  // 装载转换：旧周的扁平 articles/videos 先转成一组 *_units（历史精读仍留在 JSON，
  // 带 legacy 小标）。编辑器不再列出、也不再新增精读。旧的 mode 原样留在 JSON 里。
  let tasks = copyTasks(migrateLegacyUnits(week?.tasks || {}))
  delete tasks.speak
  let openKey = null
  let addOpen = !hasPlanItems(tasks)
  let addType = ''
  let showAll = false
  let videoPick = []
  let nce2Pick = []
  let nce2Task = 'shadow'
  let pickNote = ''
  let readBook = ''
  let readChapters = []
  let previewTimer = 0
  let previewSeq = 0
  // ⭐ 提升档：boost_items（口诀卡/句型复述）。旧的 boost_days 保存时原样留下，界面不再让人选周几。
  // 选择态与懒加载的数据都挂本闭包，paint() 重画不丢。
  let boostOpen = false
  let boostType = 'memory'
  let boostTopic = ''
  let boostNode = ''
  let boostPattern = ''
  let boostTopics = null
  let boostPatterns = null
  let boostLoadSeq = 0
  const boostNodeMap = new Map()  // topic_id → 带 memory 字段的节点列表

  const books = ed.books || []
  const lectures = ed.lectures || []
  const videos = ed.videos || []
  const nce2Lessons = ed.nce2 || []
  const videoTitles = new Map(videos.map((v) => [String(v.vid), String(v.title || v.vid)]))
  const nce2Titles = new Map(nce2Lessons.map((l) => [String(l.id), l.title || ('#' + l.id)]))
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
    return defaultVocabLevel(tasks, vocabWeek, vLevels)
  }

  // 清单下方的提示：作业讲次对不上（黄条，不拦保存）+ 词汇选了孩子已背完的
  // 级别（红条，保存前再确认一次）
  function warnText() {
    return [hwMismatchWarning(tasks, recentWeeks, ws), vocabLevelWarning(tasks, vocabWeek)]
      .filter(Boolean).join(' ')
  }

  function currentVocabLevel() {
    const practicing = vocabWeek.practicing_level
    if (practicing != null && practicing !== '') return Number(practicing)
    const stats = vocabWeek.levels?.length ? vocabWeek.levels : vLevels
    const open = stats.find((x) => {
      const w = Number(x.words) || 0
      const done = (Number(x.base) || 0) + (Number(x.gained) || 0)
      return w > 0 && done < w
    })
    if (open) return Number(open.level)
    const withWords = [...stats].reverse().find((x) => Number(x.words) > 0)
    return withWords ? Number(withWords.level) : defaultLevel()
  }

  function vocabCountFor() {
    if (Number(tasks.vocab_new) > 0) return Number(tasks.vocab_new)
    if (tasks.vocab_goal == null) return 40
    const base = tasks.vocab_lv == null
      ? (Number(vocabWeek.base_all) || 0)
      : levelStat(Number(tasks.vocab_lv)).base
    const n = Number(tasks.vocab_goal) - base
    return n > 0 ? n : 40
  }

  function vocabHint(count) {
    const shown = Math.max(0, Math.round(Number(count) || 0))
    const baseAll = Number(vocabWeek.base_all) || 0
    const gainedAll = Number(vocabWeek.gained_all) || 0
    const totalLearned = baseAll + gainedAll
    const curLv = currentVocabLevel()
    const s = levelStat(curLv)
    const lvDone = s.base + s.gained
    const lvPart = s.words > 0
      ? `当前已到 L${curLv}（${lvDone}/${s.words} 词）`
      : `当前已到 L${curLv}`
    const bits = [
      `已学 ${totalLearned} 词（本周已新掌握 ${gainedAll} 词），${lvPart}。`,
      `本周进度按不限级别新掌握计：${Math.min(gainedAll, shown)}/${shown}。`,
    ]
    if (tasks.vocab_lv != null) {
      bits.push(`（原先按 L${tasks.vocab_lv} 单独计数；修改词数后将自动改为不限级别。）`)
    }
    return bits.join('')
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
    if (tasks.micro != null) {
      out.push({ key: 'micro', type: 'micro', title: '✏️ 微练习（本周每日 3 题）' })
    }
    if (tasks.vocab_goal != null) {
      out.push({ key: 'vocab', type: 'vocab', title: `🔤 ${vocabRowTitle(tasks, vocabWeek)}` })
    }
    const videoUnits = tasks.video_units || []
    videoUnits.forEach((u, idx) => {
      const line = videoUnitLine(u, videoTitles)
      out.push({
        key: `video-unit:${idx}`, type: 'video', unitIdx: idx, mode: u.mode,
        title: line.head, sub: line.sub,
      })
    })
    const nce2Units = tasks.nce2_units || []
    nce2Units.forEach((u, idx) => {
      const line = nce2UnitLine(u, nce2Titles)
      out.push({
        key: `nce2-unit:${idx}`, type: 'nce2', unitIdx: idx, mode: u.mode,
        taskMode: u.task_mode, title: line.head, sub: line.sub,
        count: (u.lessons || []).length,
      })
    })
    for (const [id, val] of Object.entries(tasks.reading || {})) {
      out.push({
        key: `reading:${id}`, type: 'reading', book_id: id, chapters: val,
        title: `📖 ${readingTitle(id, val)}`,
      })
    }
    for (const label of tasks.fce || []) {
      out.push({ key: `fce:${label}`, type: 'fce', label, title: `FCE · ${label}` })
    }
    for (const id of tasks.vocab_papers || []) {
      const p = papers.find((x) => x.paper_id === id)
      const lab = vocabPaperShort(id, p && p.level)
      out.push({
        key: `paper:${id}`, type: 'paper', paper_id: id,
        title: `单词试卷 · ${lab.short}`, full: lab.full,
      })
    }
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
    if (type === 'fce') return (tasks.fce || []).includes(value)
    if (type === 'paper') return (tasks.vocab_papers || []).includes(value)
    if (type === 'video') return videoUnitVids(tasks).includes(String(value))
    if (type === 'nce2') return nce2UnitLessons(tasks).includes(Number(value))
    if (type === 'micro') return tasks.micro != null
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
      return papers.map((p) => {
        const lab = vocabPaperShort(p.paper_id, p.level)
        return {
          value: p.paper_id,
          label: lab.short,
          title: lab.full,
          taken: takenOf(type, p.paper_id),
          goalish: goalish(type, p.paper_id),
          mark: '',
        }
      })
    }
    return []
  }

  // ---- ⭐ 提升档编辑：口诀卡/句型复述列表增删 ----
  // 保存走 tasks JSON 的 boost_items。旧的 boost_days 若已在 JSON 里，整份覆盖时仍带着，后端不再读。
  function ensureBoost() {
    if (!Array.isArray(tasks.boost_items)) tasks.boost_items = []
  }

  function boostItemLabel(it) {
    if (!it || it.type === 'retell') {
      const p = (boostPatterns || []).find((x) => Number(x.id) === Number(it?.pattern_id))
      return p ? `句型复述 · 第${p.lecture}讲 ${String(p.sentence || '').slice(0, 20)}`
        : `句型复述 #${it?.pattern_id ?? '?'}`
    }
    const t = (boostTopics || []).find((x) => x.topic_id === it.topic_id)
    const nodes = boostNodeMap.get(it.topic_id) || []
    const n = nodes.find((x) => x.key === it.node_key)
    if (t && n) return `背诵口诀卡 · ${t.emoji || '📘'} ${String(t.title).slice(0, 18)} / ${String(n.title || n.key).slice(0, 18)}`
    return `背诵口诀卡 · ${it.topic_id || '?'}/${it.node_key || '?'}`
  }

  function boostReadyToAdd() {
    return boostReady({
      type: boostType, topic: boostTopic, node: boostNode, pattern: boostPattern,
    })
  }

  function boostBodyHtml() {
    const items = tasks.boost_items || []
    const itemLines = items.map((it, idx) => `
      <div class="pe-boost-item">
        <span>⭐ ${escapeHtml(boostItemLabel(it))}</span>
        <button class="chip pe-row-del" type="button" data-boost-del="${idx}">去掉</button>
      </div>`).join('')
    const picker = boostType === 'memory'
      ? `
        <select id="pe-boost-topic">
          <option value="">选择专题…</option>
          ${(boostTopics || []).filter((t) => t.published !== false).map((t) =>
            `<option value="${escAttr(t.topic_id)}" ${t.topic_id === boostTopic ? 'selected' : ''}>${escapeHtml(`${t.emoji || '📘'} ${t.title || t.topic_id}`.slice(0, 34))}</option>`).join('')}
        </select>
        ${boostTopic ? `
        <select id="pe-boost-node">
          <option value="">选择背诵卡节点…</option>
          ${(boostNodeMap.get(boostTopic) || []).map((n) =>
            `<option value="${escAttr(n.key)}" ${n.key === boostNode ? 'selected' : ''}>${escapeHtml(String(n.title || n.key).slice(0, 34))}</option>`).join('')
            || '<option value="" disabled>这个专题还没有带背诵卡的节点</option>'}
        </select>` : ''}`
      : `
        <select id="pe-boost-pattern">
          <option value="">选择句型…</option>
          ${(boostPatterns || []).map((p) =>
            `<option value="${p.id}" ${Number(boostPattern) === Number(p.id) ? 'selected' : ''}>${escapeHtml(`第${p.lecture ?? '?'}讲 ${String(p.sentence || '').slice(0, 20)}`)}</option>`).join('')}
        </select>`
    const loading = boostType === 'memory'
      ? (boostTopics == null ? '<p class="muted">专题列表加载中…</p>' : '')
      : (boostPatterns == null ? '<p class="muted">句型列表加载中…</p>' : '')
    return `
      <p class="pe-wiz-hint">本周的提升任务：背一张口诀卡，或复述一个句型。这一周任意一天做完都算。过了周日夜里再做，记在下一周。</p>
      <div class="pe-boost-items">${itemLines || '<p class="muted pe-empty">还没配提升任务。</p>'}</div>
      <div class="pe-boost-add">
        <label class="pe-speak"><input type="radio" name="pe-boost-type" value="memory" ${boostType === 'memory' ? 'checked' : ''}/> 口诀卡</label>
        <label class="pe-speak"><input type="radio" name="pe-boost-type" value="retell" ${boostType === 'retell' ? 'checked' : ''}/> 句型复述</label>
        ${loading}
        ${picker}
        <button class="btn-primary" id="pe-boost-add" type="button" ${boostReadyToAdd() ? '' : 'disabled'}>添加提升任务</button>
      </div>`
  }

  async function ensureBoostNodes(topicId) {
    if (!topicId || boostNodeMap.has(topicId)) return
    try {
      const t = await api.topicsAdminGet(topicId)
      const memNodes = []
      const walk = (ns) => (ns || []).forEach((n) => {
        if (n.memory && (n.memory.front || n.memory.back)) memNodes.push(n)
        walk(n.children)
      })
      walk(t.nodes || [])
      boostNodeMap.set(topicId, memNodes)
    } catch { boostNodeMap.set(topicId, []) }
  }

  async function loadBoostData() {
    const seq = ++boostLoadSeq
    const jobs = []
    if (boostTopics == null) {
      jobs.push(api.topicsAdminList().then((rows) => { boostTopics = rows || [] }).catch(() => { boostTopics = [] }))
    }
    if (boostPatterns == null) {
      jobs.push(api.patternsList().then((rows) => { boostPatterns = Array.isArray(rows) ? rows : [] }).catch(() => { boostPatterns = [] }))
    }
    await Promise.all(jobs)
    await Promise.all([...new Set([
      boostTopic,
      ...((tasks.boost_items || []).filter((it) => it && it.type === 'memory').map((it) => it.topic_id)),
    ])].map((id) => ensureBoostNodes(id)))
    // 关上或又打开过一次：这次的重画会把老师刚点的下拉拆掉。
    if (seq !== boostLoadSeq || !dlg.isConnected || !boostOpen) return
    paint()
  }

  function paint() {
    const prev = dlg.querySelector('.plan-editor-body')
    const sc = prev?.scrollTop || 0
    const list = rows()
    const warn = warnText()
    const danger = !!vocabLevelWarning(tasks, vocabWeek)
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
            <div class="pe-warn${danger ? ' pe-warn-danger' : ''}" id="pe-warn" ${warn ? '' : 'hidden'}>${escapeHtml(warn)}</div>
            <details class="pe-boost" id="pe-boost" ${boostOpen ? 'open' : ''}>
              <summary>⭐ 提升档 <i>排进本周即可，学生哪天做完都算</i></summary>
              <div class="pe-boost-body">${boostBodyHtml()}</div>
            </details>
            <div class="chip-row">
              <button class="btn-primary" id="pe-save" type="button">保存</button>
              <button class="chip" id="pe-cancel" type="button">取消</button>
            </div>
          </div>
          <aside class="pe-preview-card">
            <b>学生课表预览</b>
            <p class="pe-lead">右边是建议节奏，周一到周日用同一套摊法。这一周哪一天做完都算；过了周日夜里，这一周的进度就定了。</p>
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
        <div class="pe-row-title" title="${escAttr(r.full || r.title)}">${escapeHtml(r.title)}</div>
        ${r.sub ? `<span class="pe-done-line">${escapeHtml(r.sub)}</span>` : ''}
        <span class="pe-done-line">${escapeHtml(completionLine(r.type, { topicCounts: topicOn, mode: r.mode, count: r.count, taskMode: r.taskMode }))}</span>
        ${open ? `<div class="pe-edit" data-stop>${editHtml(r)}</div>` : ''}
      </div>
      <button class="chip pe-row-del" type="button" data-del="${escAttr(r.key)}">去掉</button>
    </div>`
  }

  function editHtml(r) {
    if (r.type === 'vocab') return vocabFormHtml('pe-v', true)
    if (r.type === 'lecture' || r.type === 'hw_paper' || r.type === 'focus_kp') {
      return `<label class="pe-speak">改成 <select id="pe-swap-lec">${lecOptions(r)}</select></label>`
    }
    if (r.type === 'reading') return readingEditHtml(r)
    if (r.type === 'video') {
      return '<p class="pe-wiz-hint">现在是 2 个一组：要换内容就点「去掉」删整组，再重新加一组。</p>'
    }
    if (r.type === 'nce2') {
      return '<p class="pe-wiz-hint">NCE2 听力一组 1–5 课：要换课或换完成方式就点「去掉」删整组，再重新加一组。</p>'
    }
    if (r.type === 'micro') {
      return '<p class="pe-wiz-hint">本周已启用微练习：周一到周日各打一次卡（每天 3 题）。过了当天不能补，漏打留在进度里。不要了点「去掉」。</p>'
    }
    if (r.type === 'fce') return `<label class="pe-speak">改成 <select id="pe-swap-fce">${fceOptions(r.label)}</select></label>`
    if (r.type === 'paper') return `<label class="pe-speak">改成 <select id="pe-swap-paper">${paperOptions(r.paper_id)}</select></label>`
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

  function fceOptions(cur) {
    return fceParts.map((p) => {
      const taken = takenOf('fce', p.label) && p.label !== cur
      return `<option value="${escAttr(p.label)}" ${p.label === cur ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(p.label)}</option>`
    }).join('')
  }

  function paperOptions(cur) {
    return papers.map((p) => {
      const taken = takenOf('paper', p.paper_id) && p.paper_id !== cur
      const lab = vocabPaperShort(p.paper_id, p.level)
      return `<option value="${escAttr(p.paper_id)}" title="${escAttr(lab.full)}" ${p.paper_id === cur ? 'selected' : ''} ${taken ? 'disabled' : ''}>${escapeHtml(lab.short)}</option>`
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
    const count = inList ? vocabCountFor() : 40
    const hint = vocabHint(count)
    return `<div class="pe-vocab-row">
        <span>本周新掌握</span>
        <input id="${prefix}-n" type="number" min="1" max="5000" value="${count}">
        <span>个词</span>
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
    if (addType === 'reading') return readingAddHtml()
    if (addType === 'video') return videoAddHtml()
    if (addType === 'nce2') return nce2AddHtml()
    if (addType === 'micro') return microAddHtml()
    const items = pickItems(addType)
    const filtered = showAll ? items : items.filter((it) => it.goalish)
    const use = filtered.length || showAll ? filtered : items
    const note = !showAll && !filtered.length && items.length
      ? '<p class="pe-wiz-hint">总目标里没有还没完成的，下面先列出全部。</p>' : ''
    if (!items.length) return '<p class="muted">没有可选项。先到备课里准备内容。</p>'
    return `${note}
      <label class="pe-speak"><input type="checkbox" id="pe-show-all" ${showAll ? 'checked' : ''}/> 显示全部（含已完成、总目标以外）</label>
      <div class="pe-wiz-picks">${use.map((it) =>
        `<button type="button" class="pe-pick ${it.taken ? 'taken' : ''}" data-pick="${escAttr(it.value)}" title="${escAttr(it.title || it.label)}" ${it.taken ? 'disabled' : ''}>${escapeHtml(it.label)}${it.taken ? ' · 已在本周' : ''}${it.mark ? ` · ${escapeHtml(it.mark)}` : ''}</button>`).join('')}</div>`
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
    if (!videos.length) return '<p class="muted">还没有上架视频。先到备课里添加。</p>'
    const list = showAll ? videos : videos.filter((v) => !takenOf('video', v.vid))
    return `<label class="pe-speak"><input type="checkbox" id="pe-show-all" ${showAll ? 'checked' : ''}/> 连本周已有的视频一起显示</label>
      <p class="pe-wiz-hint">勾 2 个为一组再放。这一周哪天看完都算，过了周日夜里再交记在下一周。</p>
      ${pickNote ? `<p class="pe-wiz-hint">${escapeHtml(pickNote)}</p>` : ''}
      <div class="pe-wiz-picks">${(list.length ? list : videos).map((v) => {
        const on = videoPick.includes(v.vid)
        const taken = takenOf('video', v.vid)
        return `<button type="button" class="pe-pick ${on ? 'on' : ''} ${taken ? 'taken' : ''}" data-vid-pick="${escAttr(v.vid)}" ${taken ? 'disabled' : ''}>${escapeHtml(String(v.title || v.vid).slice(0, 42))}${taken ? ' · 已在本周' : ''}</button>`
      }).join('')}</div>
      <div><button class="btn-primary" id="pe-vid-add" type="button" ${videoPick.length === 2 ? '' : 'disabled'}>放入清单（一组，已勾 ${videoPick.length}/2 个）</button></div>`
  }

  /** NCE2 听力：勾 1–5 课 + 摊法 + 完成方式（跟读默认），就能放入。 */
  function nce2AddHtml() {
    if (!nce2Lessons.length) return '<p class="muted">还没有可布置的 NCE2 听力课（有音频的课才会出现在这里）。</p>'
    const list = showAll ? nce2Lessons : nce2Lessons.filter((l) => !takenOf('nce2', l.id))
    return `<label class="pe-speak"><input type="checkbox" id="pe-show-all" ${showAll ? 'checked' : ''}/> 连本周已有的课一起显示</label>
      <p class="pe-wiz-hint">勾 1–5 课为一组再放。这一周哪天做完都算。跟读 = 听一句跟一句读，精听 = 只听不跟读。</p>
      ${pickNote ? `<p class="pe-wiz-hint">${escapeHtml(pickNote)}</p>` : ''}
      <div class="pe-wiz-picks">${(list.length ? list : nce2Lessons).map((l) => {
        const on = nce2Pick.includes(Number(l.id))
        const taken = takenOf('nce2', l.id)
        const words = Number(l.n_words) > 0 ? ` · ${Number(l.n_words)} 词` : ''
        return `<button type="button" class="pe-pick ${on ? 'on' : ''} ${taken ? 'taken' : ''}" data-nce2-pick="${Number(l.id)}" ${taken ? 'disabled' : ''}>第${l.id}课 ${escapeHtml(String(l.title || '').slice(0, 30))}${words}${taken ? ' · 已在本周' : ''}</button>`
      }).join('')}</div>
      <label class="pe-speak"><input type="radio" name="pe-nce2-task" value="shadow" ${nce2Task === 'shadow' ? 'checked' : ''}/> 听力跟读（默认）</label>
      <label class="pe-speak"><input type="radio" name="pe-nce2-task" value="listen" ${nce2Task === 'listen' ? 'checked' : ''}/> 仅精听</label>
      <div><button class="btn-primary" id="pe-nce2-add" type="button" ${nce2Pick.length >= 1 && nce2Pick.length <= 5 ? '' : 'disabled'}>放入清单（一组，已勾 ${nce2Pick.length}/5 课）</button></div>`
  }

  function microAddHtml() {
    const on = tasks.micro != null
    return `<p class="pe-wiz-hint">一组微练习 = 本周周一到周日各打一次卡（每天 3 题）。过了当天不能补。</p>
      <button type="button" class="chip pe-chchip ${on ? 'primary' : ''}" id="pe-micro-toggle" ${on ? 'disabled' : ''}>${on ? '已在本周' : '放入清单（一组）'}</button>`
  }

  function placeVocab(count) {
    tasks = applyVocabTarget(tasks, {
      level: null, count, base: Number(vocabWeek.base_all) || 0, words: 0,
    })
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
        nce2Pick = []
        nce2Task = 'shadow'
        pickNote = ''
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
      if (form) placeVocab(form.count)
    })
    const swapLec = dlg.querySelector('#pe-swap-lec')
    if (swapLec) swapLec.addEventListener('change', () => swapScalar('lec', Number(swapLec.value)))
    const swapFce = dlg.querySelector('#pe-swap-fce')
    if (swapFce) swapFce.addEventListener('change', () => swapScalar('fce', swapFce.value))
    const swapPaper = dlg.querySelector('#pe-swap-paper')
    if (swapPaper) swapPaper.addEventListener('change', () => swapScalar('paper', swapPaper.value))
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
        if (i >= 0) {
          videoPick.splice(i, 1)
          pickNote = ''
        } else if (videoPick.length >= 2) {
          // 最小单元 = 2：勾第 3 个时拒绝并提示
          pickNote = '一组最多勾 2 个：先取消一个勾选再换。'
        } else {
          videoPick.push(vid)
          pickNote = ''
        }
        paint()
      })
    })
    const vidAdd = dlg.querySelector('#pe-vid-add')
    if (vidAdd) vidAdd.addEventListener('click', () => {
      if (videoPick.length !== 2) return
      tasks = appendVideoUnit(tasks, videoPick)
      openKey = `video-unit:${(tasks.video_units || []).length - 1}`
      videoPick = []
      pickNote = ''
      addOpen = false
      paint()
    })
    dlg.querySelectorAll('[data-nce2-pick]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const no = Number(btn.dataset.nce2Pick)
        const i = nce2Pick.indexOf(no)
        if (i >= 0) {
          nce2Pick.splice(i, 1)
          pickNote = ''
        } else if (nce2Pick.length >= 5) {
          // 最小单元 = 1–5 课：勾第 6 课时拒绝并提示
          pickNote = '一组最多勾 5 课：先取消一个勾选再换。'
        } else {
          nce2Pick.push(no)
          pickNote = ''
        }
        paint()
      })
    })
    const nce2Add = dlg.querySelector('#pe-nce2-add')
    if (nce2Add) nce2Add.addEventListener('click', () => {
      if (nce2Pick.length < 1 || nce2Pick.length > 5) return
      tasks = appendNce2Unit(tasks, nce2Pick, undefined, nce2Task)
      openKey = `nce2-unit:${(tasks.nce2_units || []).length - 1}`
      nce2Pick = []
      pickNote = ''
      addOpen = false
      paint()
    })
    const microBtn = dlg.querySelector('#pe-micro-toggle')
    if (microBtn && !microBtn.disabled) {
      microBtn.addEventListener('click', () => {
        tasks = toggleMicroGroup(tasks)
        openKey = 'micro'
        addOpen = false
        paint()
      })
    }
    dlg.querySelectorAll('input[name="pe-nce2-task"]').forEach((r) => {
      r.addEventListener('change', () => { nce2Task = r.value })
    })
    // ---- ⭐ 提升档编辑区 ----
    const boostBox = dlg.querySelector('#pe-boost')
    if (boostBox) {
      boostBox.addEventListener('toggle', () => {
        // paint() 会把已经打开的 details 再写一遍，浏览器补发 toggle。
        // 状态没变就当回声丢掉，否则会一直拆掉下拉框，点不中。
        const action = boostDetailsToggle(boostOpen, boostBox.open)
        if (action === 'ignore') return
        boostOpen = boostBox.open
        if (action === 'load') loadBoostData()
      })
    }
    dlg.querySelectorAll('[data-boost-del]').forEach((btn) => {
      btn.addEventListener('click', () => {
        ensureBoost()
        tasks.boost_items.splice(Number(btn.dataset.boostDel), 1)
        if (!tasks.boost_items.length) delete tasks.boost_items
        paint()
      })
    })
    dlg.querySelectorAll('input[name="pe-boost-type"]').forEach((r) => {
      r.addEventListener('change', () => {
        boostType = r.value
        boostNode = ''
        boostPattern = ''
        paint()
      })
    })
    const topicSel = dlg.querySelector('#pe-boost-topic')
    if (topicSel) topicSel.addEventListener('change', () => {
      boostTopic = topicSel.value
      boostNode = ''
      if (boostTopic) ensureBoostNodes(boostTopic).then(() => { if (dlg.isConnected) paint() })
      else paint()
    })
    const nodeSel = dlg.querySelector('#pe-boost-node')
    if (nodeSel) nodeSel.addEventListener('change', () => {
      boostNode = nodeSel.value
      // 选齐才放开「添加提升任务」。不重画的话按钮一直 disabled，点不了。
      paint()
    })
    const patSel = dlg.querySelector('#pe-boost-pattern')
    if (patSel) patSel.addEventListener('change', () => {
      boostPattern = patSel.value
      paint()
    })
    const boostAdd = dlg.querySelector('#pe-boost-add')
    if (boostAdd) boostAdd.addEventListener('click', () => {
      if (!boostReadyToAdd()) return
      ensureBoost()
      const item = boostType === 'memory'
        ? { type: 'memory', topic_id: boostTopic, node_key: boostNode }
        : { type: 'retell', pattern_id: Number(boostPattern) }
      const dup = (tasks.boost_items || []).some((it) => it.type === item.type
        && (item.type === 'memory'
          ? it.topic_id === item.topic_id && it.node_key === item.node_key
          : Number(it.pattern_id) === item.pattern_id))
      if (!dup) tasks.boost_items.push(item)
      boostNode = ''
      boostPattern = ''
      paint()
    })
  }

  function bindVocab(prefix, live) {
    const n = dlg.querySelector(`#${prefix}-n`)
    const hint = dlg.querySelector(`#${prefix}-hint`)
    if (!n) return
    const refreshHint = () => {
      if (hint) hint.textContent = vocabHint(Number(n.value))
    }
    n.addEventListener('input', () => {
      refreshHint()
      if (live) commitLiveVocab()
    })
  }

  function readVocab(prefix) {
    const n = dlg.querySelector(`#${prefix}-n`)
    if (!n) return null
    return {
      count: Math.min(5000, Math.max(1, Math.round(Number(n.value) || 1))),
    }
  }

  function commitLiveVocab() {
    const form = readVocab('pe-v')
    if (!form) return
    tasks = applyVocabTarget(tasks, {
      level: null, count: form.count, base: Number(vocabWeek.base_all) || 0, words: 0,
    })
    const title = dlg.querySelector('[data-row="vocab"] .pe-row-title')
    if (title) title.textContent = `🔤 ${vocabRowTitle(tasks, vocabWeek)}`
    const warn = warnText()
    const box = dlg.querySelector('#pe-warn')
    if (box) {
      box.textContent = warn
      box.hidden = !warn
      box.classList.toggle('pe-warn-danger', !!vocabLevelWarning(tasks, vocabWeek))
    }
    schedulePreview()
  }

  function applyPick(value) {
    if (addType === 'lecture' || addType === 'hw_paper' || addType === 'focus_kp') {
      tasks = appendPlanItem(tasks, { type: addType, lecture: Number(value) })
    } else if (addType === 'fce') {
      tasks = appendPlanItem(tasks, { type: 'fce', label: value })
    } else if (addType === 'paper') {
      tasks = appendPlanItem(tasks, { type: 'paper', paper_id: value })
    }
    paint()
  }

  function removeKey(key) {
    // 组行与微练习：整组删 / toggle 掉
    if (key === 'micro') {
      tasks = toggleMicroGroup(tasks)
      if (openKey === key) openKey = null
      paint()
      return
    }
    const um = /^(video-unit|nce2-unit):(\d+)$/.exec(key)
    if (um) {
      tasks = um[1] === 'video-unit'
        ? removeVideoUnit(tasks, Number(um[2]))
        : removeNce2Unit(tasks, Number(um[2]))
      if (openKey === key) openKey = null
      paint()
      return
    }
    const row = rows().find((r) => r.key === key)
    if (!row) return
    const item = { type: row.type }
    if (row.lecture != null) item.lecture = row.lecture
    if (row.label) item.label = row.label
    if (row.paper_id) item.paper_id = row.paper_id
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
    } else if (kind === 'fce') {
      tasks = removePlanItem(tasks, { type: 'fce', label: row.label })
      tasks = appendPlanItem(tasks, { type: 'fce', label: value })
      openKey = `fce:${value}`
    } else if (kind === 'paper') {
      tasks = removePlanItem(tasks, { type: 'paper', paper_id: row.paper_id })
      tasks = appendPlanItem(tasks, { type: 'paper', paper_id: value })
      openKey = `paper:${value}`
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
      const data = await api.planWeekPreview({ week_start: ws, user: student, tasks: tasksForSave(tasks) })
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
    const danger = vocabLevelWarning(tasks, vocabWeek)
    if (danger && !confirm(`${danger}\n\n仍要这样保存吗？`)) return
    try {
      await api.planWeekPut(ws, { tasks: tasksForSave(tasks), notes: week?.notes || '', user: student })
      close()
      if (onSaved) await onSaved()
    } catch (e) {
      alert('保存失败：' + (e.message || ''))
    }
  }

  paint()
}

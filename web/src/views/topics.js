// 专题学习：自学手册页。内容静态打包（由讲义 HTML 精简迁移），
// 「我学完了」按钮把该主题 key 并入 done_keys 写后端
// （PUT /topics/{id}/progress——谁登录记谁的行，跨设备同步）。
// 专题对学生的可见性在前端清单里声明 assignedTo：'malin' 表示仅 malin 可见。
import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { createFocusTracker } from './library/focus.js'
import { TOPICS } from '../data/topicsV1.js'

// 专题数据本体在 ../data/topicsV1.js；此处再导出，保持「topics.js 导出 TOPICS」的旧引用兼容
export { TOPICS }

// ---- 渲染 ----------------------------------------------------------------- //

function visibleTopics(user) {
  return TOPICS.filter(
    (t) => !t.assignedTo || t.assignedTo === user,
  )
}

function qList(items, cls) {
  return items
    .map(
      ([tag, text, key, why]) => `
<li>
  <span class="topic-qtag">${escapeHtml(tag)}</span>
  <p class="topic-qtext">${text}</p>
  <button class="topic-reveal-btn">看答案</button>
  <div class="topic-answer"><span class="key">${escapeHtml(key)}</span><div class="why">${escapeHtml(why)}</div></div>
</li>`,
    )
    .join('')
}

export async function mountTopics(el, { role } = {}) {
  const auth = getAuth()
  const me = auth ? auth.user : ''
  // v2 优先：结构化专题（topic_content 表）——列表从接口拿（含开放过滤）；
  // 接口不可用降级静态清单（迁移兼容期兜底）
  let v2list = []
  try { v2list = await api.topicsV2() } catch { v2list = [] }
  const staticList = visibleTopics(me)
  const isV2 = (id) => v2list.some((x) => x.topic_id === id)

  const list = [
    ...v2list.map((x) => ({
      id: x.topic_id, title: x.title, subtitle: x.subtitle,
      emoji: x.emoji, assignedTo: x.assigned_to, v2: true, nodeCount: x.node_count,
    })),
    ...staticList.filter((x) => !isV2(x.id)).map((x) => ({ ...x, v2: false })),
  ]

  el.innerHTML = `
    <div class="topic-page">
      <div class="topic-hero">
        <p class="eyebrow">和爸学 · 专题学习</p>
        <h1>📚 我的专题</h1>
        <p class="sub">爸爸给你准备的专属手册。一个主题一个主题学，学完一个点一下「我学完了」。</p>
      </div>
      <div class="topic-list" id="topic-list"></div>
    </div>`

  if (!list.length) {
    el.querySelector('#topic-list').innerHTML =
      '<div class="topic-empty">暂无可用的专题</div>'
    return
  }

  const listEl = el.querySelector('#topic-list')

  // ---- 专题卡片入口（v2 用 node_count；静态用 themes 数）----
  async function renderList(progressMap) {
    listEl.innerHTML = list
      .map((t) => {
        const p = progressMap[t.id] || { done_keys: [] }
        const total = t.v2 ? t.nodeCount : t.themes.length
        const done = (p.done_keys || []).length
        const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0
        return `
      <article class="topic-card" data-topic="${t.id}" style="--tc:#3a7ca5">
        <div class="topic-card-head">
          <span class="topic-emoji">${t.emoji || '📘'}</span>
          <div>
            <h2>${escapeHtml(t.title)}</h2>
            <p>${escapeHtml(t.subtitle || '')}</p>
          </div>
          <span class="topic-badge ${done === total && total ? 'all-done' : ''}">${done}/${total}</span>
        </div>
        <div class="topic-mini-bar"><i style="width:${pct}%"></i></div>
      </article>`
      })
      .join('')
    listEl.querySelectorAll('.topic-card').forEach((card) => {
      card.addEventListener('click', () => {
        const id = card.dataset.topic
        location.hash = `/topics/${id}`
      })
    })
  }

  // ---- 进度加载 ----
  let progressMap = {}
  try {
    const rows = await api.topicsProgress()
    progressMap = Object.fromEntries(rows.map((r) => [r.topic_id, r]))
  } catch { /* 后端不可达时降级为空进度，按钮本地乐观更新 */ }
  renderList(progressMap)

  // ---- v2 结构化详情：节点{讲解+错题回顾+泛化题}，先想再看，节点级进度 ----
  async function renderDetailV2(topicId) {
    let t
    try { t = await api.topicsV2Get(topicId) }
    catch (e) {
      el.innerHTML = `<div class="topic-page"><p class="topic-empty">专题加载失败：${escapeHtml(e.message)}</p></div>`
      return
    }
    let p
    try { p = await api.topicProgress(topicId) } catch { p = { done_keys: [] } }
    const doneSet = new Set(p.done_keys || [])
    const flatKeys = []
    const walkKeys = (nodes) => nodes.forEach((n) => {
      flatKeys.push(n.key); walkKeys(n.children || [])
    })
    walkKeys(t.nodes || [])

    const qBlock = (q, i, kind) => `
      <li>
        <div class="topic-q-head">
          <b>${kind === 'wrong' ? '🔙' : '🎯'} Q${i + 1}</b>
          ${q.lecture != null ? `<span class="topic-q-src">第${q.lecture}讲 · 第${q.qnum}题</span>` : ''}
          ${q.note ? `<span class="topic-q-note">${escapeHtml(q.note)}</span>` : ''}
          <button class="topic-reveal-btn">先想再看答案</button>
        </div>
        <p class="topic-q-stem">${escapeHtml(q.stem || '（题目缺失）')}</p>
        ${(q.options || []).length ? `
          <ol class="topic-q-options">${q.options.map((o, j) =>
            `<li><b>${String.fromCharCode(65 + j)}.</b> ${escapeHtml(o)}</li>`).join('')}</ol>` : ''}
        <div class="topic-q-answer">
          <b>答案：${escapeHtml(q.answer || '—')}</b>
          ${q.explain ? `<p>${escapeHtml(q.explain)}</p>` : ''}
        </div>
        <button class="topic-reveal-btn reopen">再想一遍，收起答案</button>
      </li>`

    const nodeHtml = (n, depth) => `
      <section class="topic-theme" id="th-${n.key}" style="--tc:#3a7ca5;--tcs:#e9f1f7" data-key="${n.key}">
        <div class="topic-theme-head">
          <h2>${depth > 0 ? '↳ ' : ''}${escapeHtml(n.title)}</h2>
        </div>
        ${n.explain ? `<div class="topic-cardbox">${n.explain}</div>` : ''}
        ${(n.wrong_qs || []).length ? `
          <h3 class="topic-sub">错题讲解回顾（先想再点开）</h3>
          <ul class="topic-replay">${n.wrong_qs.map((q, i) => qBlock(q, i, 'wrong')).join('')}</ul>` : ''}
        ${(n.general_qs || []).length ? `
          <h3 class="topic-sub">${escapeHtml(n.qs_label || '泛化题学习（换马甲的同款题）')}</h3>
          <ul class="topic-drill">${n.general_qs.map((q, i) => qBlock(q, i, 'general')).join('')}</ul>` : ''}
        <div class="topic-done-row">
          <button class="topic-done-btn ${doneSet.has(n.key) ? 'done' : ''}" data-key="${n.key}">
            ${doneSet.has(n.key) ? '✅ 这个知识点我学完了' : '🌱 这个知识点我学完了'}
          </button>
          <span class="topic-done-hint">${doneSet.has(n.key) ? '已记录 · 点一下可取消' : '学完点这里，爸爸能看到'}</span>
        </div>
        ${(n.children || []).map((c) => nodeHtml(c, depth + 1)).join('')}
      </section>`

    el.innerHTML = `
    <div class="topic-page">
      <button class="fce-back-btn" id="topic-back">← 返回专题列表</button>
      <div class="topic-hero detail" style="--tc:#3a7ca5">
        <p class="eyebrow">和爸学 · 专题学习${t.assigned_to ? ` · ${escapeHtml(t.assigned_to)} 的专属专题` : ''}</p>
        <h1>${t.emoji || '📘'} ${escapeHtml(t.title)}</h1>
        <p class="sub">${escapeHtml(t.subtitle || '')}</p>
        <div class="topic-progress-row">
          <div class="topic-bar"><i id="topic-bar-i"></i></div>
          <span id="topic-progress-label"></span>
        </div>
      </div>
      ${(t.nodes || []).map((n) => nodeHtml(n, 0)).join('')}
    </div>`

    el.querySelector('#topic-back').addEventListener('click', () => {
      location.hash = '/topics'
    })
    el.querySelectorAll('.topic-reveal-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.classList.contains('reopen')) btn.closest('li').classList.remove('open')  // 收起重想
        else btn.closest('li').classList.add('open')
      })
    })
    const barI = el.querySelector('#topic-bar-i')
    const label = el.querySelector('#topic-progress-label')
    function refreshProgress() {
      const done = flatKeys.filter((k) => doneSet.has(k)).length
      barI.style.width = `${flatKeys.length ? Math.round((done / flatKeys.length) * 100) : 0}%`
      label.textContent = `${done}/${flatKeys.length} 个知识点`
    }
    refreshProgress()
    el.querySelectorAll('.topic-done-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const key = btn.dataset.key
        const nowDone = !doneSet.has(key)
        nowDone ? doneSet.add(key) : doneSet.delete(key)
        btn.classList.toggle('done', nowDone)
        btn.textContent = nowDone ? '✅ 这个知识点我学完了' : '🌱 这个知识点我学完了'
        btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
          nowDone ? '已记录 · 点一下可取消' : '学完点这里，爸爸能看到'
        refreshProgress()
        try {
          await api.topicPutProgress(t.topic_id, [...doneSet], t.assigned_to || '')
        } catch {
          btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
            '⚠️ 保存失败，稍后会重试（进度暂存本页）'
        }
      })
    })
    setupTopicFocus(el, t)
  }

  // ---- 专题学习专注力采集（module=topics：所有学习内容统一进批改中心轨迹）----
  // 与精读同款接法：容器即滚动源，0→100 全范围；路由离开时由 main 的
  // _cleanups 机制触发 destroy（flush 末段会话）。
  function setupTopicFocus(el, t) {
    const auth = getAuth()
    if (!auth || auth.role === 'teacher') return  // 学生才采集
    const root = el.querySelector('.topic-page')
    if (!root) return
    const label = `${t.emoji || ''}${t.title || ''}`.slice(0, 120)
    const tracker = createFocusTracker({
      readerRoot: root, viewerEl: root, bookId: null, bookTitle: label,
      enabled: true, module: 'topics', initialRangeLabel: label,
    })
    tracker.hooks.onReloc(0, label)
    tracker.hooks.onReloc(100)
    // 教师开关：student.focus=false 时静默停采（与泛读馆同一开关）
    api.librarySettings().then((cfg) => {
      if (cfg && cfg.student && cfg.student.focus === false) tracker.disable()
    }).catch(() => {})
    const cleanups = el._cleanups || (el._cleanups = [])
    cleanups.push(() => tracker.destroy())
  }

  // ---- 手册详情页（hash: #/topics/{id}）----
  async function renderDetail(topicId) {
    if (isV2(topicId)) {
      await renderDetailV2(topicId)
      return
    }
    const t = list.find((x) => x.id === topicId)
    if (!t) {
      location.hash = '/topics'
      return
    }
    let p
    try {
      p = await api.topicProgress(topicId)
    } catch {
      p = { done_keys: [] }
    }
    const doneSet = new Set(p.done_keys || [])

    el.innerHTML = `
    <div class="topic-page">
      <button class="fce-back-btn" id="topic-back">← 返回专题列表</button>
      <div class="topic-hero detail" style="--tc:${t.themes[0].color}">
        <p class="eyebrow">和爸学 · 专题学习${t.assignedTo ? ` · ${escapeHtml(t.assignedTo)} 的专属专题` : ''}</p>
        <h1>${t.emoji} ${escapeHtml(t.title)}</h1>
        <p class="sub">${escapeHtml(t.subtitle)}</p>
        <div class="topic-progress-row">
          <div class="topic-bar"><i id="topic-bar-i"></i></div>
          <span id="topic-progress-label"></span>
        </div>
      </div>
      <nav class="topic-toc" id="topic-toc">
        ${t.themes
          .map(
            (x) =>
              `<a href="#th-${x.key}" data-key="${x.key}" style="--tc:${x.color}"><i class="dot"></i>${escapeHtml(x.no)} ${escapeHtml(x.title.split('——')[0])}</a>`,
          )
          .join('')}
      </nav>
      ${t.themes
        .map((th) => {
          const done = doneSet.has(th.key)
          return `
      <section class="topic-theme" id="th-${th.key}" style="--tc:${th.color};--tcs:${th.soft}" data-key="${th.key}">
        <div class="topic-theme-head">
          <span class="topic-no">${escapeHtml(th.no)}</span>
          <h2>${escapeHtml(th.title)}</h2>
          <span class="topic-score">${escapeHtml(th.score)}</span>
        </div>
        <p class="topic-lead">${th.lead}</p>
        ${th.cards
          .map(
            (c) => `
        <h3 class="topic-sub">${escapeHtml(c.h)}</h3>
        <div class="topic-cardbox">${c.body}</div>`,
          )
          .join('')}
        <h3 class="topic-sub">错题回放（先想再点开）</h3>
        <ul class="topic-replay">${qList(th.replay)}</ul>
        <h3 class="topic-sub">变式练习（换马甲的同款题）</h3>
        <ul class="topic-drill">${qList(th.drills)}</ul>
        <div class="topic-done-row">
          <button class="topic-done-btn ${done ? 'done' : ''}" data-key="${th.key}">
            ${done ? '✅ 这个主题我学完了' : '🌱 这个主题我学完了'}
          </button>
          <span class="topic-done-hint">${done ? '已记录 · 点一下可取消' : '学完点这里，爸爸能看到'}</span>
        </div>
      </section>`
        })
        .join('')}
    </div>`

    el.querySelector('#topic-back').addEventListener('click', () => {
      location.hash = '/topics'
    })

    // 揭示答案
    el.querySelectorAll('.topic-reveal-btn').forEach((btn) => {
      btn.addEventListener('click', () => btn.closest('li').classList.add('open'))
    })

    // 进度条
    const barI = el.querySelector('#topic-bar-i')
    const label = el.querySelector('#topic-progress-label')
    function refreshProgress() {
      const total = t.themes.length
      const done = t.themes.filter((x) => doneSet.has(x.key)).length
      barI.style.width = `${total ? Math.round((done / total) * 100) : 0}%`
      label.textContent = `${done}/${total} 个主题`
      // 导航点同步打勾态
      el.querySelectorAll('#topic-toc a').forEach((a) => {
        a.classList.toggle('done', doneSet.has(a.dataset.key))
      })
    }
    refreshProgress()

    // 我学完了：乐观更新 + PUT 后端
    el.querySelectorAll('.topic-done-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const key = btn.dataset.key
        const nowDone = !doneSet.has(key)
        nowDone ? doneSet.add(key) : doneSet.delete(key)
        btn.classList.toggle('done', nowDone)
        btn.textContent = nowDone ? '✅ 这个主题我学完了' : '🌱 这个主题我学完了'
        btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
          nowDone ? '已记录 · 点一下可取消' : '学完点这里，爸爸能看到'
        refreshProgress()
        try {
          await api.topicPutProgress(t.id, [...doneSet], t.assignedTo || '')
        } catch {
          btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
            '⚠️ 保存失败，稍后会重试（进度暂存本页）'
        }
      })
    })
  }

  // 路由：#/topics 列表；#/topics/{id} 详情
  const segs = (location.hash.replace(/^#\/?/, '') || '').split('?')[0].split('/')
  if (segs[0] === 'topics' && segs[1]) {
    await renderDetail(segs[1])
  }
}

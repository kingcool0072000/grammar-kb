// 专题学习：v2 结构化专题（topic_content 表）。列表/内容全走接口，
// 学生只看被开放（assigned_to 为空=全员，或含自己）的专题；
// 「我学完了」按节点 key 写后端（PUT /topics/{id}/progress——谁登录记谁的行）。
// 开放名单（「malin,mxy 的专属专题」）不进学生标题，也不写入进度归属。
// v1 静态手册（topicsV1.js）已退役：接口失败明确报错，不再静态兜底。
import { api, getAuth } from '../api.js'
import { escapeHtml, escAttr } from '../render.js'
import { createFocusTracker } from './library/focus.js'
import { focusMemoryCard, topicRouteFromHash } from './boostNav.js'
import { studentTopicText, topicDoneCount } from './topicText.js'

export async function mountTopics(el, { role } = {}) {
  // 结构化专题（topic_content 表）：列表从接口拿（含开放过滤）。
  // 接口失败直接报错（v1 静态兜底已退役）。
  let v2list
  try { v2list = await api.topicsV2() } catch (e) {
    el.innerHTML = `<div class="topic-page"><p class="topic-empty">专题内容加载失败：${escapeHtml(e.message)}</p></div>`
    return
  }

  const list = v2list.map((x) => ({
    id: x.topic_id,
    title: studentTopicText(x.title),
    subtitle: studentTopicText(x.subtitle),
    emoji: x.emoji,
    nodeCount: x.node_count,
    nodeKeys: Array.isArray(x.node_keys) ? x.node_keys : null,
  }))

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

  // ---- 专题卡片入口 ----
  async function renderList(progressMap) {
    listEl.innerHTML = list
      .map((t) => {
        const p = progressMap[t.id] || { done_keys: [] }
        // 与详情同一口径：done_keys ∩ 节点 key。没有 key 清单时退回条数。
        const frac = topicDoneCount(p.done_keys, t.nodeKeys)
        const total = frac.total == null ? (t.nodeCount || 0) : frac.total
        const done = frac.done
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
  async function renderDetailV2(topicId, nodeKey = '') {
    let t
    try { t = await api.topicsV2Get(topicId) }
    catch (e) {
      el.innerHTML = `<div class="topic-page"><p class="topic-empty">专题加载失败：${escapeHtml(e.message)}</p></div>`
      return
    }
    let p
    try { p = await api.topicProgress(topicId) } catch { p = { done_keys: [] } }
    const doneSet = new Set(p.done_keys || [])
    const titleText = studentTopicText(t.title)
    const subText = studentTopicText(t.subtitle)
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

    // ⭐ 背诵卡（提升档素材）：节点带 memory 字段才有。先看提示面，
    // 翻开对答案，自评「记住了」= 向后端打卡（提升档过关依据）。
    const memoryBlockHtml = (n) => `
      <div class="topic-memory" data-mem="${escAttr(n.key)}">
        <div class="topic-memory-head"><b>⭐ 背诵卡</b><span>先看提示想一想，能背出来再翻开</span></div>
        <div class="topic-memory-front">
          <p>${escapeHtml(n.memory.front || '（提示缺失）')}</p>
          <button class="topic-memory-flip">翻开</button>
        </div>
        <div class="topic-memory-back" hidden>
          <div class="topic-cardbox topic-memory-backbox">${escapeHtml(n.memory.back || '（口诀缺失）')}</div>
          <div class="topic-memory-btns">
            <button class="topic-memory-btn pass">记住了 ✓</button>
            <button class="topic-memory-btn again">还没记住 ↻</button>
          </div>
        </div>
        <span class="topic-memory-msg"></span>
      </div>`

    const nodeHtml = (n, depth) => `
      <section class="topic-theme" id="th-${escAttr(n.key)}" style="--tc:#3a7ca5;--tcs:#e9f1f7" data-key="${escAttr(n.key)}">
        <div class="topic-theme-head">
          <h2>${depth > 0 ? '↳ ' : ''}${escapeHtml(n.title)}</h2>
        </div>
        ${n.explain ? `<div class="topic-cardbox">${n.explain}</div>` : ''}
        ${n.memory && (n.memory.front || n.memory.back) ? memoryBlockHtml(n) : ''}
        ${(n.wrong_qs || []).length ? `
          <h3 class="topic-sub">错题讲解回顾（先想再点开）</h3>
          <ul class="topic-replay">${n.wrong_qs.map((q, i) => qBlock(q, i, 'wrong')).join('')}</ul>` : ''}
        ${(n.general_qs || []).length ? `
          <h3 class="topic-sub">${escapeHtml(n.qs_label || '泛化题学习（换马甲的同款题）')}</h3>
          <ul class="topic-drill">${n.general_qs.map((q, i) => qBlock(q, i, 'general')).join('')}</ul>` : ''}
        <div class="topic-done-row">
          <button class="topic-done-btn ${doneSet.has(n.key) ? 'done' : ''}" data-key="${escAttr(n.key)}">
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
        <p class="eyebrow">和爸学 · 专题学习</p>
        <h1>${t.emoji || '📘'} ${escapeHtml(titleText)}</h1>
        <p class="sub">${escapeHtml(subText)}</p>
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
    // ⭐ 背诵卡：翻开 → 自评。记住了 = memory-pass 打卡（提升档过关）；
    // 还没记住 = 折回提示面可再翻；接口失败保留现场可重试。
    el.querySelectorAll('.topic-memory').forEach((block) => {
      const front = block.querySelector('.topic-memory-front')
      const back = block.querySelector('.topic-memory-back')
      const msg = block.querySelector('.topic-memory-msg')
      const flip = block.querySelector('.topic-memory-flip')
      if (flip) flip.addEventListener('click', () => {
        front.hidden = true
        back.hidden = false
      })
      const again = block.querySelector('.topic-memory-btn.again')
      if (again) again.addEventListener('click', () => {
        back.hidden = true
        front.hidden = false
        if (msg) msg.textContent = ''
      })
      const pass = block.querySelector('.topic-memory-btn.pass')
      if (pass) pass.addEventListener('click', async () => {
        pass.disabled = true
        try {
          await api.topicMemoryPass(topicId, block.dataset.mem || '')
          block.classList.add('passed')
          block.innerHTML =
            '<div class="topic-memory-head"><b>⭐ 背诵卡</b></div>' +
            '<p class="topic-memory-done">⭐ 已打卡 · 这个口诀记住了</p>'
        } catch (e) {
          pass.disabled = false
          if (msg) msg.textContent = `⚠️ 打卡失败：${e.message}`
        }
      })
    })
    const barI = el.querySelector('#topic-bar-i')
    const label = el.querySelector('#topic-progress-label')
    function refreshProgress() {
      const frac = topicDoneCount([...doneSet], flatKeys)
      barI.style.width = `${frac.total ? Math.round((frac.done / frac.total) * 100) : 0}%`
      label.textContent = `${frac.done}/${frac.total} 个知识点`
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
          // 不把 assigned_to（「malin,mxy」开放名单）写进进度归属。
          // 名单整串存进去后，列表接口按用户名精确匹配会丢掉这行，卡片停在 0/N。
          await api.topicPutProgress(t.topic_id, [...doneSet])
        } catch {
          btn.closest('.topic-done-row').querySelector('.topic-done-hint').textContent =
            '⚠️ 保存失败，稍后会重试（进度暂存本页）'
        }
      })
    })
    // 课程表口诀卡深链：#/topics/{id}?node={key} 刷新后仍定位这张背诵卡
    if (nodeKey) {
      const hit = focusMemoryCard(el, nodeKey)
      if (hit && hit.message) placeBoostNote(el, hit)
    }
    setupTopicFocus(el, t)
  }

  function placeBoostNote(root, hit) {
    const note = document.createElement('p')
    note.className = 'topic-boost-note'
    note.setAttribute('role', 'status')
    note.textContent = hit.message
    const anchor = hit.anchor && typeof hit.anchor.insertAdjacentElement === 'function'
      ? hit.anchor
      : (root.querySelector('.topic-hero') || root.querySelector('.topic-page'))
    if (!anchor) return
    if (anchor === hit.anchor) anchor.insertAdjacentElement('afterbegin', note)
    else anchor.appendChild(note)
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

  // 路由：#/topics 列表；#/topics/{id} 详情；#/topics/{id}?node={key} 定位背诵卡
  const route = topicRouteFromHash(location.hash)
  if (route.topicId) await renderDetailV2(route.topicId, route.nodeKey)
}

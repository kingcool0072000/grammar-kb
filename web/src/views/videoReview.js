import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { fmtDur } from '../fmt.js'

// 视频复述批改 & 成绩记录页（共享模块，支持独立页 #/videoReview 及备课中心队列内嵌）：
//   - 按学生筛选、按类型筛选（全部 / 视频复述 / 句型复述）
//   - 顶部统计摘要（待批改数 / 已批改数 / 均分）
//   - 待批改区 + 默认展开的「已批改成绩记录」（含头部分数徽标、录音回放、改分、多行评语、删除）

// 提升档句型复述（ref_kind='pattern'）与视频复述共用本视图：
// 行头标注「句型复述」，其余批改流程（评分/评语/删除）零改动。
function retellSourceHtml(r) {
  if (r.ref_kind === 'pattern') {
    const what = r.pattern_sentence || r.sentence || `句型 #${r.vid}`
    return `<span class="tag vr-kind-pattern">🗣 句型复述</span> ${escapeHtml(String(what).slice(0, 60))}`
  }
  return `🎬 ${escapeHtml(r.video_title || r.vid)}`
}

export async function mountVideoReview(el) {
  el.innerHTML = `
    <button class="fce-back-btn gd-tool-back" id="vr-back">← 返回备课中心</button>
    <div class="view-head"><h1>复述成绩与批改</h1><p>加载中…</p></div>
  `
  el.querySelector('#vr-back').addEventListener('click', () => {
    location.hash = '/prep'
  })
  let retells = []
  let students = []
  try {
    const [rList, uRes] = await Promise.all([
      api.videoRetells({ limit: 200 }),
      api.users().catch(() => ({ users: [] })),
    ])
    retells = Array.isArray(rList) ? rList : []
    students = (uRes.users || []).filter((u) => u.role === 'student').map((u) => u.user)
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  const urlUser = new URLSearchParams(location.hash.split('?')[1] || '').get('user') || ''
  await renderVideoReview(el, retells, {
    students,
    initialUser: urlUser,
    onBack: () => {
      location.hash = '/prep'
      return true
    },
  })
}

export async function renderVideoReview(el, retells, opts) {
  const onBack = (opts || {}).onBack
  const items = Array.isArray(retells) ? retells : []
  const allStudents = [...new Set([
    ...((opts && opts.students) || []),
    ...items.map((r) => r.user).filter(Boolean),
  ])]
  let filterUser = (opts && opts.initialUser && allStudents.includes(opts.initialUser))
    ? opts.initialUser
    : ''
  let filterKind = 'all' // 'all' | 'video' | 'pattern'

  el.innerHTML = `
    <button class="fce-back-btn gd-tool-back" id="vr-back">← 返回备课中心</button>
    <div class="view-head">
      <h1>复述成绩与批改</h1>
      <p>听学生的复述录音（视频复述 / 句型复述），10 分制打分（1 位小数）+ 评语，已批记录支持随时回听改分。</p>
    </div>
    <div class="pw-stubar">
      ${allStudents.length > 1 ? `
      <label class="pw-student">学生
        <select id="vr-user">
          <option value="">全部学生</option>
          ${allStudents.map((u) => `<option value="${escapeHtml(u)}" ${filterUser === u ? 'selected' : ''}>${escapeHtml(u)}</option>`).join('')}
        </select>
      </label>` : ''}
      <div class="vl-lv-filters" id="vr-kinds" style="margin-bottom:0">
        <button type="button" class="chip vl-lv-filter on" data-kind="all">全部</button>
        <button type="button" class="chip vl-lv-filter" data-kind="video">🎬 视频复述</button>
        <button type="button" class="chip vl-lv-filter" data-kind="pattern">🗣 句型复述</button>
      </div>
      <span class="muted" id="vr-summary" style="font-size:13px"></span>
    </div>
    <section class="fce-group" id="vr-pend-sec">
      <div class="fce-group-title" id="vr-pend-title">🎙 待批改</div>
      <div id="vr-list"></div>
    </section>
    <details class="rd-done-fold" id="vr-done-fold" open>
      <summary id="vr-done-sum">📈 已批改成绩记录</summary>
      <div id="vr-done"></div>
    </details>
  `

  const box = el.querySelector('#vr-list')
  const doneBox = el.querySelector('#vr-done')
  const summaryEl = el.querySelector('#vr-summary')
  const pendTitle = el.querySelector('#vr-pend-title')
  const doneSum = el.querySelector('#vr-done-sum')
  const token = getAuth()?.token || ''

  const scoreBadgeHtml = (r) => (
    r.status === 'graded'
      ? `<b class="fce-his-score ${Number(r.teacher_score) >= 7 ? 'ok' : ''}" data-badge>${r.teacher_score}/10</b>`
      : '<b class="fce-his-score pend" data-badge>待批改</b>'
  )

  const buildCard = (r) => {
    const isGraded = r.status === 'graded'
    const row = document.createElement('div')
    row.className = 'reading-review-card'
    row.innerHTML = `
      <div class="reading-review-head">
        <b>${escapeHtml(r.user)}</b> · ${retellSourceHtml(r)}
        ${scoreBadgeHtml(r)}
        <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
      </div>
      <audio controls preload="metadata" style="width:100%;margin:8px 0"></audio>
      <div class="reading-review-grade">
        <label>分数 <input type="number" min="0" max="10" step="0.1" value="${r.teacher_score ?? 8}" style="width:68px" data-score></label>
        <textarea rows="3" placeholder="评语（内容完整度/表达/用词建议）" data-comment>${escapeHtml(r.teacher_comment || '')}</textarea>
        <button class="reading-btn primary" data-grade>${isGraded ? '更新批改' : '提交批改'}</button>
        <button class="reading-btn danger" data-del title="删除这条复述录音">删除</button>
      </div>
      <div class="reading-review-msg"></div>
    `
    const audio = row.querySelector('audio')
    if (token) audio.src = `/api/video-study/retells/${r.id}/audio?token=${encodeURIComponent(token)}`
    const scoreInput = row.querySelector('[data-score]')
    const commentInput = row.querySelector('[data-comment]')
    const gradeBtn = row.querySelector('[data-grade]')
    const msgEl = row.querySelector('.reading-review-msg')
    const reenable = () => { gradeBtn.disabled = false }
    scoreInput.addEventListener('input', reenable)
    commentInput.addEventListener('input', reenable)

    gradeBtn.addEventListener('click', async () => {
      const rawStr = scoreInput.value.trim()
      const rawScore = rawStr === '' ? NaN : Number(rawStr)
      const comment = commentInput.value
      if (!Number.isFinite(rawScore) || rawScore < 0 || rawScore > 10) {
        msgEl.textContent = '分数须为 0-10'
        return
      }
      const score = Math.round(rawScore * 10) / 10
      gradeBtn.disabled = true
      try {
        await api.videoRetellGrade(r.id, { score, comment })
        r.status = 'graded'
        r.teacher_score = score
        r.teacher_comment = comment
        const badge = row.querySelector('[data-badge]')
        if (badge) badge.outerHTML = scoreBadgeHtml(r)
        gradeBtn.textContent = '更新批改'
        msgEl.innerHTML = `✅ 已批改：${score}/10`
        paintStats()
      } catch (e) {
        msgEl.textContent = `失败：${e.message}`
        gradeBtn.disabled = false
      }
    })
    row.querySelector('[data-del]').addEventListener('click', async () => {
      if (!confirm(`确定删除 ${r.user} 的这条复述录音（${fmtDur(r.duration_sec || 0)}）？删除后不可恢复。`)) return
      try {
        await api.videoRetellDelete(r.id)
        const idx = items.indexOf(r)
        if (idx >= 0) items.splice(idx, 1)
        renderLists()
      } catch (e) {
        msgEl.textContent = `删除失败：${e.message}`
      }
    })
    return row
  }

  function filteredItems() {
    return items.filter((r) => {
      if (filterUser && r.user !== filterUser) return false
      if (filterKind === 'pattern' && r.ref_kind !== 'pattern') return false
      if (filterKind === 'video' && r.ref_kind === 'pattern') return false
      return true
    })
  }

  function paintStats() {
    const list = filteredItems()
    const pend = list.filter((r) => r.status === 'pending')
    const done = list.filter((r) => r.status !== 'pending')
    const scored = done.filter((r) => r.teacher_score != null && Number.isFinite(Number(r.teacher_score)))
    const avg = scored.length
      ? Math.round((scored.reduce((s, r) => s + Number(r.teacher_score), 0) / scored.length) * 10) / 10
      : null
    summaryEl.textContent = `待批改 ${pend.length} · 已批改 ${done.length}${avg != null ? ` · 均分 ${avg}/10` : ''}`
    pendTitle.textContent = `🎙 待批改（${pend.length}）`
    doneSum.innerHTML = `📈 已批改成绩记录（${done.length}）<i>点开可回听录音、修改分数与评语或删除</i>`
  }

  function renderLists() {
    const list = filteredItems()
    const pend = list.filter((r) => r.status === 'pending')
    const done = list.filter((r) => r.status !== 'pending')
      .slice()
      .sort((a, b) => (b.id || 0) - (a.id || 0))
    paintStats()
    box.innerHTML = ''
    doneBox.innerHTML = ''
    if (pend.length) {
      for (const r of pend.slice(0, 50)) box.append(buildCard(r))
    } else {
      box.innerHTML = done.length
        ? '<p class="reading-hint">没有待批复述 🎉</p>'
        : '<p class="reading-hint">暂无视频/句型复述提交</p>'
    }
    if (done.length) {
      for (const r of done.slice(0, 100)) doneBox.append(buildCard(r))
    } else {
      doneBox.innerHTML = '<p class="reading-hint">暂无已批改成绩记录</p>'
    }
  }

  const userSel = el.querySelector('#vr-user')
  if (userSel) {
    userSel.addEventListener('change', (e) => {
      filterUser = e.target.value
      renderLists()
    })
  }
  el.querySelectorAll('#vr-kinds [data-kind]').forEach((btn) =>
    btn.addEventListener('click', () => {
      filterKind = btn.dataset.kind
      el.querySelectorAll('#vr-kinds [data-kind]').forEach((x) =>
        x.classList.toggle('on', x === btn))
      renderLists()
    }))

  el.querySelector('#vr-back').addEventListener('click', () => {
    try {
      if (onBack) {
        const result = onBack()
        if (result !== false) return
      }
    } catch { /* 回退到路由 */ }
    location.hash = '/prep?from=videoReview' // 带 query 防同 hash（#/prep）赋值不触发路由
  })

  renderLists()
}

export function videoRetellRow(r) {
  const score = r.status === 'graded'
    ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
    : '<b class="fce-his-score pend">待批改</b>'
  return `
    <div class="fce-his-row reading-rec-row" data-vretell="${r.id}">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${retellSourceHtml(r)}</span>
      ${score}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

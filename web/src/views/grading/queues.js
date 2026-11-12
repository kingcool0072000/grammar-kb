import { api } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { fmtDur } from '../../fmt.js'
import { renderReadingReview } from '../readingReview.js'
import { renderVideoReview, videoRetellRow } from '../videoReview.js'
import { renderNce2Review, nce2SubRow } from '../nce2Review.js'
import { mountEssayReviewInline } from '../fcePapers/teacher.js'

// 备课中心 · 待批队列模块（自原批改看板视图拆出，可独立挂进各学科分区）。
//
// 统一签名：mountXxx(host, ctx)，ctx = { state, snapshots, go, onCount }
//   state.student  当前筛选学生，接口按它过滤
//   snapshots      Map 快照缓存（沿用原批改看板模式，切学生不重拉）。
//                  键：`rec:${student}` / `vretell:${student}` / `essay:${student}`
//   go             预留的跳转出口（当前队列批改全内嵌，未使用）
//   onCount(n)     队列渲染完成后回传待批数（宿主画分区角标用）
//
// 每个队列渲染一个 <section class="g-queue-block">（样式类沿用原批改看板）；
// 「去批改」按钮 / 行点击在队列容器内内嵌打开批改视图，返回时作废该键快照
// 并重挂本队列，批改结果立即反映到待批数。

// 单接口拉取限时兜底（沿用原批改看板惯例）：最坏 8 秒返回空数组，不无限转圈
const timed = (p, ms = 8000) => Promise.race([
  p, new Promise((resolve) => setTimeout(() => resolve([]), ms)),
]).catch(() => [])

const queueBlock = (key, title, n, rows, empty, more) => `
  <section class="g-queue-block ${n ? 'has-pend' : ''}" data-card="${key}">
    <header class="gcard-head">
      <h2>${title}</h2>
      ${n ? `<b class="gcard-pend">待处理 ${n}</b>` : '<b class="gcard-n">0</b>'}
    </header>
    <div class="gcard-list">
      ${rows || `<p class="reading-hint">${empty}</p>`}
    </div>
    ${more || ''}
  </section>`

function recRow(r) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
      ${r.status === 'graded'
        ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
        : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

function fceRow(s) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(s.user)} · T${s.test_id} P${s.part}</span>
      ${s.status === 'auto'
        ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b>`
        : s.status === 'graded'
          ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b>`
          : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${s.duration_sec ? fmtDur(s.duration_sec) + ' · ' : ''}${(s.created_at || '').slice(0, 10)}</span>
    </div>`
}

// ---- 1 · 待批录音队列（精读分区）：行点击 / 按钮内嵌打开录音批改 ----

export async function mountRecordingQueue(host, ctx) {
  const { state, snapshots, onCount } = ctx || {}
  const student = state && state.student
  const key = `rec:${student}`
  const load = async () => {
    let recs = snapshots && snapshots.get(key)
    if (!recs) {
      recs = await timed(api.readingRecordings({ limit: 200, user: student || undefined }))
      recs = Array.isArray(recs) ? recs : []
      if (snapshots) snapshots.set(key, recs)
    }
    return recs
  }
  const openReview = async () => {
    const view = document.createElement('div')
    host.replaceChildren(view)
    view.innerHTML = '<p class="muted">加载录音批改…</p>'
    try {
      const recs = await api.readingRecordings({ limit: 200, user: student || undefined })
      await renderReadingReview(view, recs, {
        onBack: () => {
          if (snapshots) snapshots.delete(key) // 批改后作废快照，重挂拉新
          run()
          return true
        },
      })
    } catch (e) {
      view.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    }
  }
  const run = async () => {
    host.innerHTML = '<p class="muted">加载中…</p>'
    const recs = await load()
    const pend = recs.filter((r) => r.status === 'pending')
    host.innerHTML = queueBlock(
      'recording',
      '待批录音',
      pend.length,
      pend.map((r) => recRow(r)).join(''),
      '没有待批录音',
      pend.length ? `<button class="gcard-more" data-open-review>去批改录音（${pend.length}）→</button>` : '',
    )
    host.querySelector('[data-open-review]')?.addEventListener('click', openReview)
    const list = host.querySelector('.gcard-list')
    if (list) list.addEventListener('click', (e) => {
      if (e.target.closest('[data-rec-del],[data-rec-edit],audio,a')) return
      if (!e.target.closest('.fce-his-row')) return
      openReview()
    })
    onCount?.(pend.length)
    // readingAdmin 等旧入口的联动：sessionStorage 置位时自动内嵌打开批改
    if (sessionStorage.getItem('gkb-open-review') === '1') {
      sessionStorage.removeItem('gkb-open-review')
      openReview()
    }
  }
  await run()
}

// ---- 2 · 待批复述队列（视频分区）：视频复述 + 句型复述（ref_kind=pattern
// 行由 videoRetellRow 自带「句型复述」标注），批改内嵌 renderVideoReview ----

export async function mountRetellQueue(host, ctx) {
  const { state, snapshots, onCount } = ctx || {}
  const student = state && state.student
  const key = `vretell:${student}`
  const load = async () => {
    let retells = snapshots && snapshots.get(key)
    if (!retells) {
      retells = await timed(api.videoRetells({ limit: 200, user: student || undefined }))
      retells = Array.isArray(retells) ? retells : []
      if (snapshots) snapshots.set(key, retells)
    }
    return retells
  }
  const openReview = async () => {
    const view = document.createElement('div')
    host.replaceChildren(view)
    view.innerHTML = '<p class="muted">加载复述批改…</p>'
    try {
      const retells = await api.videoRetells({ limit: 200, user: student || undefined })
      await renderVideoReview(view, retells, {
        onBack: () => {
          if (snapshots) snapshots.delete(key)
          run()
          return true
        },
      })
    } catch (e) {
      view.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    }
  }
  const run = async () => {
    host.innerHTML = '<p class="muted">加载中…</p>'
    const retells = await load()
    const pend = retells.filter((r) => r.status === 'pending')
    host.innerHTML = queueBlock(
      'vretell',
      '待批复述',
      pend.length,
      pend.map((r) => videoRetellRow(r)).join(''),
      '没有待批的视频复述',
      pend.length ? `<button class="gcard-more" data-open-review>去批批复述（${pend.length}）→</button>` : '',
    )
    host.querySelector('[data-open-review]')?.addEventListener('click', openReview)
    const list = host.querySelector('.gcard-list')
    if (list) list.addEventListener('click', (e) => {
      if (e.target.closest('audio,a')) return
      if (!e.target.closest('.fce-his-row')) return
      openReview()
    })
    onCount?.(pend.length)
  }
  await run()
}

// ---- 3 · 待批作文队列（FCE 分区）：批改不再外跳 #/fcePapers，
// 内嵌 fcePapers/teacher.js 的 mountEssayReviewInline ----

export async function mountEssayQueue(host, ctx) {
  const { state, snapshots, onCount } = ctx || {}
  const student = state && state.student
  const key = `essay:${student}`
  let pend = []
  const load = async () => {
    let subs = snapshots && snapshots.get(key)
    if (!subs) {
      subs = await timed(api.fceSubmissions({ limit: 200, user: student || undefined }))
      subs = Array.isArray(subs) ? subs : []
      if (snapshots) snapshots.set(key, subs)
    }
    return subs
  }
  const openInline = () => {
    mountEssayReviewInline(host, {
      pending: pend,
      onBack: () => {
        if (snapshots) snapshots.delete(key) // 批改后作废快照，重挂拉新
        run()
      },
    })
  }
  const run = async () => {
    host.innerHTML = '<p class="muted">加载中…</p>'
    const subs = await load()
    pend = subs.filter((s) => s.status === 'pending')
    host.innerHTML = queueBlock(
      'essay',
      '待批作文',
      pend.length,
      pend.map((s) => fceRow(s)).join(''),
      '没有待批作文',
      pend.length ? `<button class="gcard-more" data-open-review>去批改作文（${pend.length}）→</button>` : '',
    )
    host.querySelector('[data-open-review]')?.addEventListener('click', openInline)
    const list = host.querySelector('.gcard-list')
    if (list) list.addEventListener('click', (e) => {
      if (!e.target.closest('.fce-his-row')) return
      openInline()
    })
    onCount?.(pend.length)
  }
  await run()
}

// ---- 4 · 待批听力跟读队列（NCE2 分区）：跟读录音 + 精听打卡（listen
// 行无音频，批改视图里以「打卡」卡呈现），批改内嵌 renderNce2Review ----

export async function mountNce2Queue(host, ctx) {
  const { state, snapshots, onCount } = ctx || {}
  const student = state && state.student
  const key = `nce2:${student}`
  const pick = (subs) => (student ? subs.filter((r) => r.user === student) : subs)
  const load = async () => {
    let subs = snapshots && snapshots.get(key)
    if (!subs) {
      subs = await timed(api.nce2Queue('all'))
      subs = Array.isArray(subs) ? subs : []
      if (snapshots) snapshots.set(key, subs)
    }
    return subs
  }
  const openReview = async () => {
    const view = document.createElement('div')
    host.replaceChildren(view)
    view.innerHTML = '<p class="muted">加载听力批改…</p>'
    try {
      const subs = pick(await api.nce2Queue('all'))
      await renderNce2Review(view, subs, {
        onBack: () => {
          if (snapshots) snapshots.delete(key) // 批改后作废快照，重挂拉新
          run()
          return true
        },
      })
    } catch (e) {
      view.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    }
  }
  const run = async () => {
    host.innerHTML = '<p class="muted">加载中…</p>'
    const subs = pick(await load())
    const pend = subs.filter((r) => r.status === 'pending')
    host.innerHTML = queueBlock(
      'nce2',
      '待批听力',
      pend.length,
      pend.map((r) => nce2SubRow(r)).join(''),
      '没有待批的听力提交',
      pend.length ? `<button class="gcard-more" data-open-review>去批改听力（${pend.length}）→</button>` : '',
    )
    host.querySelector('[data-open-review]')?.addEventListener('click', openReview)
    const list = host.querySelector('.gcard-list')
    if (list) list.addEventListener('click', (e) => {
      if (e.target.closest('audio,a')) return
      if (!e.target.closest('.fce-his-row')) return
      openReview()
    })
    onCount?.(pend.length)
  }
  await run()
}

// ---- 已批折叠区（紧凑版）：已批录音 / 已批复述 / 已阅作文 / 已批听力各近 3 条。
// 只读共享快照（超时兜底的空数组不写回，避免污染同键队列的数据）。
// 哈一成绩归哈一语法分区、正常专注归学情日志页，都不在这里。 ----

export async function mountRecentGraded(host, ctx) {
  const { state, snapshots } = ctx || {}
  const student = state && state.student
  host.innerHTML = '<p class="muted">加载中…</p>'
  const grab = async (key, fetcher) => {
    let v = snapshots && snapshots.get(key)
    if (!v) {
      v = await timed(fetcher())
      v = Array.isArray(v) ? v : []
    }
    return v
  }
  const [recs, vretells, fceSubs, nce2Subs] = await Promise.all([
    grab(`rec:${student}`, () => api.readingRecordings({ limit: 200, user: student || undefined })),
    grab(`vretell:${student}`, () => api.videoRetells({ limit: 200, user: student || undefined })),
    grab(`essay:${student}`, () => api.fceSubmissions({ limit: 200, user: student || undefined })),
    grab(`nce2:${student}`, () => api.nce2Queue('all')),
  ])
  const pickUser = (list) => (student ? list.filter((r) => r.user === student) : list)
  const doneRec = recs.filter((r) => r.status !== 'pending')
  const doneVR = vretells.filter((r) => r.status !== 'pending')
  const doneEssay = fceSubs.filter((s) => s.status !== 'pending')
  // 只统计已批跟读；listen 打卡恒 'done'，不算「已批听力」
  const doneNce2 = pickUser(Array.isArray(nce2Subs) ? nce2Subs : []).filter((r) => r.status === 'graded' && r.kind === 'shadow')
  const handledN = doneRec.length + doneVR.length + doneEssay.length + doneNce2.length
  host.innerHTML = `
    <details class="g-handled">
      <summary>已处理（${handledN}）<i>已批录音 · 已批复述 · 已阅作文 · 已批听力（各近 3 条）</i></summary>
      <div class="g-handled-grid">
        <section class="gcard" data-card="recording-done">
          <header class="gcard-head"><h2>已批录音</h2><b class="gcard-n">${doneRec.length}</b></header>
          <div class="gcard-list">${doneRec.slice(0, 3).map((r) => recRow(r)).join('') || '<p class="reading-hint">暂无</p>'}</div>
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>已批复述</h2><b class="gcard-n">${doneVR.length}</b></header>
          <div class="gcard-list">${doneVR.slice(0, 3).map((r) => videoRetellRow(r)).join('') || '<p class="reading-hint">暂无</p>'}</div>
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>已阅作文与练习</h2><b class="gcard-n">${doneEssay.length}</b></header>
          <div class="gcard-list">${doneEssay.slice(0, 3).map((s) => fceRow(s)).join('') || '<p class="reading-hint">暂无</p>'}</div>
        </section>
        <section class="gcard">
          <header class="gcard-head"><h2>已批听力</h2><b class="gcard-n">${doneNce2.length}</b></header>
          <div class="gcard-list">${doneNce2.slice(0, 3).map((r) => nce2SubRow(r)).join('') || '<p class="reading-hint">暂无</p>'}</div>
        </section>
      </div>
    </details>`
}

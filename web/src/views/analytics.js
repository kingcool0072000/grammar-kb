import { api } from '../api.js'
import { escapeHtml, renderMd } from '../render.js'

// 教师版 · 学情分析：静态报告（无 API）。
// 内容 = web/public/analytics-report.html（本地数据脚本产出的快照片段，
// 手动更新该文件随代码推送部署）。上方保留 AI 学情周报（手动触发）。
// 历史实现（四类作业 API 聚合周/月视图）已由静态报告替代，见 git 历史。
export async function mountAnalytics(el, { bare = false, user = '' } = {}) {
  // bare：作为批改中心子 Tab 内嵌时去掉自己的页头（父级已有）
  el.innerHTML = bare ? '<p class="muted">学情报告加载中…</p>'
    : '<div class="view-head"><h1>学情分析</h1><p>加载中…</p></div>'

  // AI 周报（手动触发，独立于静态报告；共用泛读馆 GLM 配置）
  const aiHolder = document.createElement('div')
  mountAiWeekly(aiHolder)

  let frag = ''
  try {
    const resp = await fetch('/analytics-report.html', { cache: 'no-cache' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    frag = await resp.text()
  } catch (e) {
    el.innerHTML = `
      ${bare ? '' : `<div class="view-head"><h1>学情分析</h1><p>静态报告。</p></div>`}
      <p style="color:#b42318">学情报告加载失败：${escapeHtml(e.message)}（资源 analytics-report.html 缺失，随代码部署）</p>`
    return
  }
  el.innerHTML = `
    ${bare ? '' : `
    <div class="view-head">
      <h1>学情分析</h1>
      <p>静态快照报告——手动生成随代码推送，不调 API。</p>
    </div>`}
    <div class="ana-static-note reading-hint">📅 报告为生产数据快照（生成日期见页脚）；更新方式：本地跑数据脚本重新产出 public/analytics-report.html 后随代码部署。</div>
    <div id="ana-ai-slot"></div>
    <div class="ana-report-host">${frag}</div>`
  el.querySelector('#ana-ai-slot').replaceWith(aiHolder)
}

// ---- AI 学情周报（保留原有手动触发逻辑，与静态报告互补）----
async function mountAiWeekly(holder) {
  let generating = false

  function fmtRange(r) {
    return `${(r.startDate || '').slice(5)} ~ ${(r.endDate || '').slice(5)}`
  }

  function cardHtml({ body, meta = '' } = {}) {
    return `
      <section class="gd-section ana-ai">
        <header class="gd-section-head">
          <h2>AI 学情周报</h2>
          <div class="ana-ai-meta">
            ${meta}
            <button class="reading-btn primary" data-ai-gen>${generating ? '生成中…' : '生成上周分析'}</button>
          </div>
        </header>
        <div class="ana-ai-body">${body}</div>
      </section>`
  }

  function render(payload) {
    generating = false
    if (!payload) {
      holder.innerHTML = cardHtml({
        body: `
          <div class="ana-ai-empty">
            <p>还没有生成过 AI 周报。点「生成上周分析」，AI 将基于上一自然周的四类作业数据（哈一成绩 · FCE · 朗读 · 单词）与前四周基线写一份分析。</p>
            <p class="reading-hint">与章节预习共用同一个智谱 Key。</p>
            <a class="reading-btn" href="#/librarySettings">去配置模型</a>
          </div>`,
      })
    } else if (payload.error) {
      const noKey = payload.needKey
      holder.innerHTML = cardHtml({
        body: `
          <div class="ana-ai-error">
            <p>${escapeHtml(payload.error)}</p>
            ${noKey ? '<p class="reading-hint">与章节预习共用同一个智谱 Key。</p><a class="reading-btn" href="#/librarySettings">去配置</a>' : ''}
          </div>`,
      })
    } else {
      holder.innerHTML = cardHtml({
        meta: `<span class="ana-ai-range">📅 ${fmtRange(payload)}</span>
               <span class="ana-ai-info">${escapeHtml(payload.model || '')} · 生成于 ${(payload.createdAt || '').slice(5, 16).replace('T', ' ')}</span>`,
        body: renderMd(payload.content || ''),
      })
    }
    const btn = holder.querySelector('[data-ai-gen]')
    btn.addEventListener('click', trigger)
  }

  async function trigger() {
    if (generating) return
    generating = true
    const btn = holder.querySelector('[data-ai-gen]')
    if (btn) { btn.disabled = true; btn.textContent = '生成中…' }
    try {
      render(await api.analyticsAiTrigger())
    } catch (e) {
      render({ error: e.message || String(e), needKey: /泛读馆设置|API Key/i.test(e.message || '') })
    }
  }

  // 打开页面先展示最近一份存档（不自动生成）
  try {
    const { reports } = await api.analyticsAiReports()
    render(reports && reports[0])
  } catch {
    render(null)
  }
}

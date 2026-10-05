import { escapeHtml } from '../render.js'

// 教师版 · 学情分析：静态报告（无 API）。
// 内容 = web/public/analytics-report.html（本地数据脚本产出的快照片段，
// 手动更新该文件随代码推送部署）。原「AI 学情周报」（API 调 GLM 生成）
// 已按用户要求移除，见 git 历史。
export async function mountAnalytics(el, { bare = false } = {}) {
  // bare：作为批改中心子 Tab 内嵌时去掉自己的页头（父级已有）
  el.innerHTML = bare ? '<p class="muted">学情报告加载中…</p>'
    : '<div class="view-head"><h1>学情分析</h1><p>加载中…</p></div>'

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
    <div class="ana-report-host">${frag}</div>`
}

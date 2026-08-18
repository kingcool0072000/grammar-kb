import { escapeHtml } from '../render.js'
import { api } from '../api.js'

// 知识点体系视图：按 语法大类 → 主题 两级树聚合零散知识点。
// 数据来自后端 /taxonomy（subcategory + 规则归类），点击知识点打开详情抽屉。
export async function mountTaxonomy(el, { pointsById, openKp }) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div>加载知识体系…</div>'

  let tree
  try {
    tree = await api.taxonomy()
  } catch (e) {
    el.innerHTML = `<div class="error-box">加载知识体系失败：${escapeHtml(e.message)}</div>`
    return
  }

  el.innerHTML = `
    <div class="view-head">
      <h1>知识体系</h1>
      <p>共 ${tree.total} 个知识点，按 语法大类 → 主题 两级聚合。点击主题展开，点知识点看详情。</p>
    </div>
    <div id="tax-tree"></div>
  `

  const $tree = el.querySelector('#tax-tree')
  $tree.innerHTML = tree.groups
    .map((g) => {
      const color = GROUP_COLOR[g.group] || '#94a3b8'
      const themes = g.themes
        .map(
          (t) => `
        <div class="tax-theme" data-theme="${escapeHtml(t.theme)}">
          <button class="tax-theme-head">
            <span class="tax-arrow">▸</span>
            <span class="tax-theme-name">${escapeHtml(t.theme)}</span>
            <span class="tax-count">${t.count}</span>
          </button>
          <div class="tax-theme-body" style="display:none">
            ${t.items
              .map(
                (it) => `
              <article class="kp-card tax-kp" data-kp="${it.id}">
                <div class="kp-title">${escapeHtml(it.title)}</div>
                <div class="kp-foot"><span class="kp-lecture">第 ${it.lecture} 讲</span></div>
              </article>`,
              )
              .join('')}
          </div>
        </div>`,
        )
        .join('')
      return `
      <section class="tax-group" style="--cat-color:${color}">
        <button class="tax-group-head">
          <span class="cat-dot" style="background:${color}"></span>
          <span class="tax-group-name">${escapeHtml(g.group)}</span>
          <span class="group-count">${g.count} 个知识点 · ${g.themes.length} 主题</span>
        </button>
        <div class="tax-group-body">${themes}</div>
      </section>`
    })
    .join('')

  // 交互：组/主题折叠 + 知识点点击
  $tree.addEventListener('click', (e) => {
    const kpCard = e.target.closest('.tax-kp')
    if (kpCard) {
      const p = pointsById.get(Number(kpCard.dataset.kp))
      if (p) openKp(p)
      return
    }
    const themeHead = e.target.closest('.tax-theme-head')
    if (themeHead) {
      const body = themeHead.nextElementSibling
      const arrow = themeHead.querySelector('.tax-arrow')
      const open = body.style.display !== 'none'
      body.style.display = open ? 'none' : ''
      arrow.textContent = open ? '▸' : '▾'
      return
    }
    const groupHead = e.target.closest('.tax-group-head')
    if (groupHead) {
      const body = groupHead.nextElementSibling
      body.style.display = body.style.display === 'none' ? '' : 'none'
    }
  })

}

const GROUP_COLOR = {
  词法: '#3b82f6', 时态: '#8b5cf6', 语态: '#06b6d4', 非谓语: '#f59e0b',
  句法: '#ec4899', 综合复习: '#10b981', 固定搭配: '#64748b',
}

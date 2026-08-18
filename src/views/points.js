import { CATEGORIES, catColor, sortByCategory } from '../theme.js'
import { escapeHtml } from '../render.js'

// 按知识点浏览：分类筛选 + 关键词搜索，卡片点击打开知识点详情抽屉。
export function mountPoints(el, { points, openKp }) {
  const present = CATEGORIES.filter((c) => points.some((p) => p.category === c))
  const cats = ['全部', ...sortByCategory(present)]
  let activeCat = '全部'
  let q = ''

  el.innerHTML = `
    <div class="view-head">
      <h1>按知识点浏览</h1>
      <p>共 ${points.length} 个知识点，可按分类筛选或搜索标题、正文、标签。</p>
    </div>
    <div class="filter-bar" id="kp-filters">
      ${cats
        .map(
          (c) =>
            `<button class="chip ${c === '全部' ? 'active' : ''}" data-cat="${c}">${escapeHtml(
              c,
            )}</button>`,
        )
        .join('')}
      <div class="search-box">🔍 <input id="kp-search" placeholder="搜索知识点 / 标签 / 正文" /></div>
    </div>
    <div id="kp-list"></div>
  `

  const $list = el.querySelector('#kp-list')
  const $search = el.querySelector('#kp-search')
  const $filters = el.querySelector('#kp-filters')

  function paintChips() {
    $filters.querySelectorAll('.chip').forEach((c) => {
      const on = c.dataset.cat === activeCat
      c.classList.toggle('active', on)
      if (on && activeCat !== '全部') {
        c.style.background = catColor(activeCat)
        c.style.color = '#fff'
        c.style.borderColor = 'transparent'
      } else {
        c.style.background = ''
        c.style.color = ''
        c.style.borderColor = ''
      }
    })
  }

  function render() {
    const ql = q.trim().toLowerCase()
    let list = points
    if (activeCat !== '全部') list = list.filter((p) => p.category === activeCat)
    if (ql) {
      list = list.filter(
        (p) =>
          p.title.toLowerCase().includes(ql) ||
          p.bodyMd.toLowerCase().includes(ql) ||
          p.tags.some((t) => t.toLowerCase().includes(ql)),
      )
    }
    if (!list.length) {
      $list.innerHTML = '<div class="empty">没有匹配的知识点，换个关键词试试。</div>'
      return
    }
    $list.innerHTML = `<div class="kp-grid">${list.map(cardHtml).join('')}</div>`
    $list.querySelectorAll('[data-kp]').forEach((card) => {
      card.addEventListener('click', () => {
        const id = Number(card.dataset.kp)
        const p = points.find((x) => x.id === id)
        if (p) openKp(p)
      })
    })
  }

  $filters.addEventListener('click', (e) => {
    const b = e.target.closest('.chip')
    if (!b) return
    activeCat = b.dataset.cat
    paintChips()
    render()
  })

  let timer
  $search.addEventListener('input', (e) => {
    clearTimeout(timer)
    timer = setTimeout(() => {
      q = e.target.value
      render()
    }, 180)
  })

  paintChips()
  render()
}

function cardHtml(p) {
  const color = catColor(p.category)
  const snippet = (p.bodyMd || '')
    .replace(/[#>*`_\-\[\]\(\)!]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 86)
  return `
    <article class="kp-card" data-kp="${p.id}">
      <div class="kp-title">${escapeHtml(p.title)}</div>
      <div class="kp-snippet">${escapeHtml(snippet)}</div>
      <div class="kp-foot">
        <span class="tag" style="--cat-color:${color}">${escapeHtml(p.category)}</span>
        <span class="kp-lecture">第 ${p.lectureNumber} 讲</span>
        ${p.tags
          .slice(0, 2)
          .map((t) => `<span class="kp-lecture">#${escapeHtml(t)}</span>`)
          .join('')}
      </div>
    </article>
  `
}

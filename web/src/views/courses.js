import { catColor, sortByCategory } from '../theme.js'
import { escapeHtml } from '../render.js'

// 按课程浏览：48 讲按语法分类分组，卡片点击打开整讲内容抽屉。
export function mountCourses(el, { lectures, openLecture }) {
  const groups = new Map()
  for (const l of lectures) {
    const c = l.category || '其它'
    ;(groups.get(c) || groups.set(c, []).get(c)).push(l)
  }
  const cats = sortByCategory([...groups.keys()])

  el.innerHTML = `
    <div class="view-head">
      <h1>初中语法课</h1>
      <p>共 ${lectures.length} 讲讲义，按语法体系分为 ${cats.length} 类。点开任意一讲查看完整内容。</p>
    </div>
    ${cats.map((c) => groupHtml(c, groups.get(c))).join('')}
  `

  el.querySelectorAll('[data-lecture]').forEach((card) => {
    card.addEventListener('click', () => {
      const num = Number(card.dataset.lecture)
      const meta = lectures.find((l) => l.number === num)
      openLecture(num, meta)
    })
  })
}

function groupHtml(cat, items) {
  const color = catColor(cat)
  items.sort((a, b) => a.number - b.number)
  return `
    <section class="course-group">
      <h2>
        <span class="cat-dot" style="background:${color}"></span>${escapeHtml(cat)}
        <span class="group-count">${items.length} 讲</span>
      </h2>
      <div class="course-grid">
        ${items.map(cardHtml).join('')}
      </div>
    </section>
  `
}

function cardHtml(l) {
  const color = catColor(l.category)
  return `
    <article class="course-card" data-lecture="${l.number}" style="--cat-color:${color}">
      <div class="course-num">第 ${l.number} 讲</div>
      <div class="course-title">${escapeHtml(l.title)}</div>
      <div class="course-meta">
        <span class="tag" style="--cat-color:${color}">${escapeHtml(
          l.subcategory || l.category,
        )}</span>
        <span>${l.page_count} 页</span>
      </div>
    </article>
  `
}

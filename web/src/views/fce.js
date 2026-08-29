import { escapeHtml } from '../render.js'
import { FCE_DAYS, FCE_PHRASES, FCE_CLOZE } from '../data/fce.js'

// FCE 知识体系：《FCE 冲刺宝典》19 天（语法讲解 + 直击考点练习）+ 语用词组 + 选词填空。
// 纯静态数据（web/src/data/fce.js），不依赖后端；练习题点击显示答案。
export function mountFce(el) {
  const lessons = FCE_DAYS.filter((d) => d.kind === 'lesson')
  const drills = FCE_DAYS.filter((d) => d.kind === 'exercise')
  const nEx = drills.reduce((s, d) => s + d.items.length, 0)

  el.innerHTML = `
    <div class="view-head">
      <h1>FCE 知识体系</h1>
      <p>《FCE 冲刺宝典》：${lessons.length} 个语法专题 · ${drills.length} 个直击考点（${nEx} 题，点击显示答案）· ${FCE_PHRASES.length} 条常考语用词组 · ${FCE_CLOZE.length} 题选词填空。</p>
    </div>
    <div id="fce-tree"></div>
  `

  const $tree = el.querySelector('#fce-tree')
  $tree.innerHTML = groupHtml('📘 语法专题', lessons) + groupHtml('🎯 直击考点', drills) + phrasesHtml() + clozeHtml()

  // 折叠交互 + 答案显隐
  $tree.addEventListener('click', (e) => {
    const ansBtn = e.target.closest('.fce-ans-btn')
    if (ansBtn) {
      const box = ansBtn.closest('.fce-item')
      box.classList.toggle('revealed')
      ansBtn.textContent = box.classList.contains('revealed') ? '收起答案' : '显示答案'
      return
    }
    const head = e.target.closest('.fce-day-head, .fce-group-head')
    if (head) {
      const body = head.nextElementSibling
      const arrow = head.querySelector('.tax-arrow')
      if (arrow) {
        const open = body.style.display !== 'none'
        body.style.display = open ? 'none' : ''
        arrow.textContent = open ? '▸' : '▾'
      } else {
        body.style.display = body.style.display === 'none' ? '' : 'none'
      }
    }
  })
}

function groupHtml(name, days) {
  return `
    <section class="fce-group">
      <div class="fce-group-title">${name}</div>
      ${days.map(dayHtml).join('')}
    </section>`
}

function dayHtml(d) {
  const kindCls = d.kind === 'exercise' ? 'fce-badge-drill' : 'fce-badge-lesson'
  const kindTxt = d.kind === 'exercise' ? '考点' : '语法'
  if (d.kind === 'exercise') d.items.forEach((it, i) => (it._n = i + 1))
  return `
    <div class="fce-day">
      <button class="fce-day-head">
        <span class="tax-arrow">▸</span>
        <span class="fce-badge ${kindCls}">${kindTxt}</span>
        <span class="fce-day-name">Day ${d.day} · ${escapeHtml(d.title)}</span>
        <span class="fce-day-sub">${d.kind === 'exercise' ? d.items.length + ' 题' : d.sections.length + ' 节'}</span>
      </button>
      <div class="fce-day-body" style="display:none">
        ${d.intro ? `<div class="fce-intro">${escapeHtml(d.intro)}</div>` : ''}
        ${d.kind === 'lesson'
          ? d.sections.map(sectionHtml).join('')
          : `<div class="fce-items">${d.items.map(itemHtml).join('')}</div>`}
      </div>
    </div>`
}

function sectionHtml(s) {
  let h = `<div class="fce-section"><div class="fce-sec-name">${escapeHtml(s.name)}</div>`
  if (s.table) h += tableHtml(s.table)
  if (s.points) {
    h += s.points
      .map((p) => {
        let ph = `<div class="fce-point">${escapeHtml(p.rule)}</div>`
        if (p.examples && p.examples.length) {
          ph += `<div class="ex-list">${p.examples
            .map(([en, zh]) => `<div class="ex-item"><p class="ex-en">${escapeHtml(en)}</p>${zh && zh !== '—' ? `<p class="ex-zh">${escapeHtml(zh)}</p>` : ''}</div>`)
            .join('')}</div>`
        }
        return ph
      })
      .join('')
  }
  return h + '</div>'
}

function tableHtml(t) {
  return `<div class="fce-table"><table>
    <thead><tr>${t.head.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead>
    <tbody>${t.rows.map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(c)}</td>`).join('')}</tr>`).join('')}</tbody>
  </table></div>`
}

function itemHtml(it) {
  return `
    <div class="fce-item">
      <div class="fce-q">
        <span class="fce-q-n">${it._n}.</span>
        <div class="fce-q-body">
          <p class="fce-q-stem">${escapeHtml(it.q)}</p>
          ${it.stem && it.stem !== '—' ? `<p class="fce-q-fill">${escapeHtml(it.stem)}</p>` : ''}
          <button class="fce-ans-btn">显示答案</button>
          <div class="fce-answer"><b>答案</b>：${escapeHtml(it.a)}</div>
        </div>
      </div>
    </div>`
}

function phrasesHtml() {
  return `
    <section class="fce-group">
      <div class="fce-group-title">📖 常考语用词组（${FCE_PHRASES.length}）</div>
      <div class="fce-day">
        <button class="fce-day-head">
          <span class="tax-arrow">▸</span>
          <span class="fce-badge fce-badge-lesson">词组</span>
          <span class="fce-day-name">语用常考词组与句型</span>
          <span class="fce-day-sub">${FCE_PHRASES.length} 条</span>
        </button>
        <div class="fce-day-body" style="display:none">
          <div class="collo-table">
            <div class="collo-group">
              <div class="collo-items">
                ${FCE_PHRASES.map(([en, zh], i) => `<div class="collo-row"><span class="collo-en">${i + 1}. ${escapeHtml(en)}</span><span class="collo-zh">${escapeHtml(zh)}</span></div>`).join('')}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>`
}

function clozeHtml() {
  // 给每题编号
  FCE_CLOZE.forEach((c, i) => (c._n = i + 1))
  return `
    <section class="fce-group">
      <div class="fce-group-title">✏️ 直击考点 · 选词填空（${FCE_CLOZE.length} 题）</div>
      <div class="fce-day">
        <button class="fce-day-head">
          <span class="tax-arrow">▸</span>
          <span class="fce-badge fce-badge-drill">考点</span>
          <span class="fce-day-name">选词填空</span>
          <span class="fce-day-sub">${FCE_CLOZE.length} 题</span>
        </button>
        <div class="fce-day-body" style="display:none">
          <div class="fce-items">
            ${FCE_CLOZE.map(
              (c) => `
              <div class="fce-item">
                <div class="fce-q">
                  <span class="fce-q-n">${c._n}.</span>
                  <div class="fce-q-body">
                    <p class="fce-q-stem">${escapeHtml(c.q)}</p>
                    <div class="fce-options">${c.options.map((o) => `<span class="fce-opt">${escapeHtml(o)}</span>`).join('')}</div>
                    <button class="fce-ans-btn">显示答案</button>
                    <div class="fce-answer"><b>答案</b>：${escapeHtml(c.a)} —— ${escapeHtml(c.note)}</div>
                  </div>
                </div>
              </div>`,
            ).join('')}
          </div>
        </div>
      </div>
    </section>`
}

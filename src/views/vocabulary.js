import { escapeHtml } from '../render.js'

// 词汇表视图：基于后端 /vocabulary（讲义语料中英对照例句提取的单词表）。
// 支持词性筛选、关键词搜索、按词频/字母排序；点击单词在抽屉里看释义、词形变化与来源知识点。
const POS_LIST = ['全部', 'v', 'n', 'adj', 'adv', 'prep', 'conj', 'pron', 'num', 'proper', '其它']
const POS_CN = {
  v: '动词', n: '名词', adj: '形', adv: '副', prep: '介',
  conj: '连', pron: '代', num: '数', proper: '专名', 其它: '其它',
}

export function mountVocabulary(el, { vocab, openWord }) {
  // 给每个词条打上原数组下标，卡片点击后用它回查
  vocab.forEach((e, i) => { e.__i = i })

  let pos = '全部'
  let q = ''
  let sort = 'freq'

  el.innerHTML = `
    <div class="view-head">
      <h1>词汇表</h1>
      <p>共 ${vocab.length} 个词，基于讲义中英对照例句自动提取，含词频、词性、释义与词形变化。</p>
    </div>
    <div class="filter-bar" id="voc-filters">
      ${POS_LIST.map((p) => `<button class="chip ${p === '全部' ? 'active' : ''}" data-pos="${p}">${POS_CN[p] || p}</button>`).join('')}
      <button class="chip" id="voc-sort" title="切换排序">按词频 ↓</button>
      <div class="search-box">🔍 <input id="voc-search" placeholder="搜索单词 / 释义" /></div>
    </div>
    <div id="voc-list"></div>
  `

  const $list = el.querySelector('#voc-list')
  const $search = el.querySelector('#voc-search')
  const $filters = el.querySelector('#voc-filters')
  const $sort = el.querySelector('#voc-sort')

  function matchPos(e) {
    if (pos === '全部') return true
    if (pos === '其它') return !e.pos || !e.pos.length
    return (e.pos || []).includes(pos)
  }

  function render() {
    const ql = q.trim().toLowerCase()
    let list = vocab.filter(matchPos)
    if (ql) {
      list = list.filter(
        (e) =>
          e.word.toLowerCase().includes(ql) ||
          (e.meanings || []).some((m) => m.toLowerCase().includes(ql)),
      )
    }
    list = list.slice().sort((a, b) =>
      sort === 'freq' ? b.freq - a.freq || a.word.localeCompare(b.word) : a.word.localeCompare(b.word),
    )
    if (!list.length) {
      $list.innerHTML = '<div class="empty">没有匹配的单词。</div>'
      return
    }
    $list.innerHTML = `<div class="vocab-grid">${list.map(cardHtml).join('')}</div>`
    $list.querySelectorAll('[data-i]').forEach((card) => {
      card.addEventListener('click', () => {
        const e = vocab[Number(card.dataset.i)]
        if (e) openWord(e)
      })
    })
  }

  $filters.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip[data-pos]')
    if (chip) {
      pos = chip.dataset.pos
      $filters.querySelectorAll('.chip[data-pos]').forEach((c) => c.classList.toggle('active', c === chip))
      render()
      return
    }
    if (e.target === $sort) {
      sort = sort === 'freq' ? 'alpha' : 'freq'
      $sort.textContent = sort === 'freq' ? '按词频 ↓' : '按字母 A-Z'
      render()
    }
  })

  let timer
  $search.addEventListener('input', (e) => {
    clearTimeout(timer)
    timer = setTimeout(() => { q = e.target.value; render() }, 180)
  })

  render()
}

function cardHtml(e) {
  const pos = (e.pos || []).map((p) => `<span class="vocab-pos">${escapeHtml(p)}</span>`).join('')
  const meaning =
    (e.gloss_lines && e.gloss_lines[0] && e.gloss_lines[0].text) ||
    e.gloss || (e.meanings || [])[0] || '—'
  const formsBrief = Object.values(e.forms || {})
    .slice(0, 2)
    .map((v) => `<span class="vocab-form">${escapeHtml(v)}</span>`)
    .join('')
  return `
    <article class="vocab-card" data-i="${e.__i}">
      <div class="vocab-top">
        <span class="vocab-word">${escapeHtml(e.word)}</span>
        <span class="vocab-freq" title="在讲义中出现次数">${e.freq}</span>
      </div>
      <div class="vocab-posrow">${pos}</div>
      <div class="vocab-meaning">${escapeHtml(meaning)}</div>
      ${formsBrief ? `<div class="vocab-formrow">${formsBrief}</div>` : ''}
    </article>`
}

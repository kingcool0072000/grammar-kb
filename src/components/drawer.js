import { api } from '../api.js'
import { renderMd, escapeHtml } from '../render.js'
import { catColor } from '../theme.js'

// 右侧详情抽屉：
//  - openKp(point)：知识点详情，含「🎯 考点信号」双向区块
//  - openLecture(number, meta)：整讲课程
//  - showBySignal(kind, key, fromPoint)：反向——某时态/标志词关联的所有知识点
//
// ctx 在 main.js 中先以空索引创建，知识点加载后再填充 byTense / byMarker / points。
// 因 drawer 闭包持有同一 ctx 引用，读取时自动拿到最新索引。
export function createDrawer(ctx = {}) {
  const mask = document.createElement('div')
  mask.className = 'drawer-mask'
  const drawer = document.createElement('aside')
  drawer.className = 'drawer'
  drawer.innerHTML = `
    <div class="drawer-head">
      <button class="drawer-back" id="d-back" style="display:none" aria-label="返回">←</button>
      <div class="titles"><h2 id="d-title"></h2><div class="sub" id="d-sub"></div></div>
      <button class="drawer-close" aria-label="关闭">×</button>
    </div>
    <div class="drawer-body" id="d-body"></div>
  `
  const root = document.createElement('div')
  root.append(mask, drawer)

  const $title = drawer.querySelector('#d-title')
  const $sub = drawer.querySelector('#d-sub')
  const $body = drawer.querySelector('#d-body')
  const $back = drawer.querySelector('#d-back')

  let currentPoint = null

  function open() {
    mask.classList.add('show')
    drawer.classList.add('show')
    document.body.style.overflow = 'hidden'
  }
  function close() {
    mask.classList.remove('show')
    drawer.classList.remove('show')
    document.body.style.overflow = ''
  }
  mask.addEventListener('click', close)
  drawer.querySelector('.drawer-close').addEventListener('click', close)
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close() })

  // 统一处理 body 内的点击：考点信号反向跳转、关联知识点卡片跳转
  $body.addEventListener('click', (e) => {
    const sig = e.target.closest('[data-signal]')
    if (sig) {
      showBySignal(sig.dataset.signal, sig.dataset.key, currentPoint)
      return
    }
    const card = e.target.closest('[data-kp]')
    if (card) {
      const id = Number(card.dataset.kp)
      const p = (ctx.points || []).find((x) => x.id === id)
      if (p) openKp(p)
    }
  })

  function setHead(title, subHtml, backToPoint) {
    $title.innerHTML = escapeHtml(title)
    $sub.innerHTML = subHtml || ''
    if (backToPoint) {
      $back.style.display = ''
      $back.title = `返回「${backToPoint.title}」`
      $back.onclick = () => openKp(backToPoint)
    } else {
      $back.style.display = 'none'
      $back.onclick = null
    }
  }

  // ---------- 知识点详情 ----------
  function openKp(p) {
    currentPoint = p
    const cat = catColor(p.category)
    setHead(
      p.title,
      `<span class="tag" style="--cat-color:${cat}">${escapeHtml(p.category)}</span>` +
        `<span>第 ${p.lectureNumber} 讲</span>` +
        (p.sectionPath ? `<span>· ${escapeHtml(p.sectionPath)}</span>` : '') +
        (p.sourcePage ? `<span>· P${p.sourcePage}</span>` : '') +
        (p.tags && p.tags.length ? `<span>· ${p.tags.map(escapeHtml).join(' / ')}</span>` : ''),
      null,
    )
    let md = p.bodyMd || ''
    if (p.examplesMd) md += `\n\n### 例题\n${p.examplesMd}`
    if (p.tableMd) md += `\n\n${p.tableMd}`
    $body.innerHTML = renderMd(md) + renderSignal(p) + renderRelations(p)
    $body.scrollTop = 0
    open()
  }

  // 🎯 考点信号：正向——本知识点涉及的标志词，按时态分组；时态/标志词均可点击反查
  function renderSignal(p) {
    const markers = (p.markers || []).filter((m) => m.marker)
    let html = `<h4>🎯 考点信号</h4>`
    if (!markers.length) {
      html += `<div class="help">本知识点无特定时态标志词。</div>`
      return html
    }
    html +=
      `<div class="help">看到这些标志词 / 时态，就是在考本知识点；点击任一项可反向查看所有相关知识点。</div>`

    // 按时态分组（无时态的归入「其它信号」）
    const groups = new Map()
    for (const m of markers) {
      const t = m.tense || '其它信号'
      ;(groups.get(t) || groups.set(t, []).get(t)).push(m)
    }

    html += `<div class="signal-block">`
    for (const [tense, ms] of groups) {
      const uniqMarkers = [...new Set(ms.map((m) => m.marker))]
      html += `
        <div class="signal-row">
          <button class="signal-tense" data-signal="tense" data-key="${escapeHtml(tense)}">${escapeHtml(tense)}</button>
          <div class="signal-markers">
            ${uniqMarkers
              .map(
                (mk) =>
                  `<button class="signal-marker" data-signal="marker" data-key="${escapeHtml(
                    mk,
                  )}">${escapeHtml(mk)}</button>`,
              )
              .join('')}
          </div>
        </div>`
    }
    html += `</div>`
    return html
  }

  function renderRelations(p) {
    if (!p.relations || !p.relations.length) return ''
    const items = p.relations
      .map(
        (r) =>
          `<div class="relation-item"><span class="rtype">${escapeHtml(
            r.type,
          )}</span>${escapeHtml(r.note || '')}</div>`,
      )
      .join('')
    return `<h4>语法关系</h4><div class="relations-list">${items}</div>`
  }

  // ---------- 反向：某时态 / 标志词关联的所有知识点 ----------
  function showBySignal(kind, key, fromPoint) {
    currentPoint = fromPoint
    const index = kind === 'tense' ? ctx.byTense : ctx.byMarker
    const list = (index && index.get(key)) || []
    setHead(
      `考【${key}】的所有知识点`,
      `<span>${kind === 'tense' ? '时态' : '标志词'}信号 · 共 ${list.length} 个知识点 · 点击进入</span>`,
      fromPoint,
    )
    if (!list.length) {
      $body.innerHTML = `<div class="empty">没有其它知识点涉及「${escapeHtml(key)}」。</div>`
    } else {
      $body.innerHTML = `<div class="kp-grid">${list.map(signalCardHtml).join('')}</div>`
    }
    $body.scrollTop = 0
  }

  function signalCardHtml(p) {
    const color = catColor(p.category)
    const snippet = (p.bodyMd || '')
      .replace(/[#>*`_\-\[\]\(\)!]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 70)
    return `
      <article class="kp-card" data-kp="${p.id}">
        <div class="kp-title">${escapeHtml(p.title)}</div>
        <div class="kp-snippet">${escapeHtml(snippet)}</div>
        <div class="kp-foot">
          <span class="tag" style="--cat-color:${color}">${escapeHtml(p.category)}</span>
          <span class="kp-lecture">第 ${p.lectureNumber} 讲</span>
        </div>
      </article>`
  }

  // ---------- 单词详情（词汇表）----------
  const FORM_CN = {
    past: '过去式',
    past_participle: '过去分词',
    present_participle: '现在分词',
    third_singular: '三单',
    plural: '复数',
    comparative: '比较级',
    superlative: '最高级',
  }
  const POS_CN = {
    v: '动词', n: '名词', adj: '形容词', adv: '副词', prep: '介词',
    conj: '连词', pron: '代词', num: '数词', proper: '专有名词',
  }

  // 例句中把目标词（含词形变化）加粗：先 escape 再匹配，按长度降序避免短词先命中
  function highlightWord(en, word, forms) {
    const variants = [...new Set([word, ...(forms || [])])]
      .filter(Boolean)
      .sort((a, b) => b.length - a.length)
    if (!variants.length) return escapeHtml(en)
    const pattern = variants
      .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
      .join('|')
    const re = new RegExp('\\b(' + pattern + ')\\b', 'gi')
    return escapeHtml(en).replace(re, '<b class="hl">$1</b>')
  }

  function showWord(e) {
    currentPoint = null
    const posTags = (e.pos || [])
      .map((p) => `<span class="tag" style="--cat-color:#64748b">${escapeHtml(POS_CN[p] || p)}</span>`)
      .join('')
    setHead(
      e.word,
      (e.phonetic ? `<span class="phonetic">/${escapeHtml(e.phonetic)}/</span>` : '') +
        `<span>词频 ${e.freq}</span>` + posTags,
      null,
    )

    let html = ''
    const glossLines = (e.gloss_lines && e.gloss_lines.length)
      ? e.gloss_lines
      : (e.gloss ? [{ pos: '', text: e.gloss }] : [])
    if (glossLines.length) {
      html +=
        `<h4>词典释义</h4><div class="gloss">` +
        glossLines
          .map(
            (g) =>
              `<div class="gloss-line">${g.pos ? `<span class="gloss-pos">${escapeHtml(g.pos)}</span>` : ''}<span class="gloss-text">${escapeHtml(g.text)}</span></div>`,
          )
          .join('') +
        `</div>`
    }
    if (e.examples && e.examples.length) {
      const seenForms = Object.values(e.forms || {})
      html +=
        `<h4>例句（${e.examples.length}）</h4><div class="ex-list">` +
        e.examples
          .map(
            (x) =>
              `<div class="ex-item"><p class="ex-en">${highlightWord(
                x.en, e.word, seenForms,
              )}</p><p class="ex-zh">${escapeHtml(x.zh)}</p></div>`,
          )
          .join('') +
        `</div>`
    } else if (e.meanings && e.meanings.length) {
      html += `<h4>释义</h4><ol class="mean-list">${e.meanings
        .map((m) => `<li>${escapeHtml(m)}</li>`)
        .join('')}</ol>`
    }
    const forms = Object.entries(e.forms || {})
    if (forms.length) {
      html += `<h4>词形变化</h4><div class="forms-grid">${forms
        .map(
          ([k, v]) =>
            `<div class="form-cell"><span class="form-key">${escapeHtml(
              FORM_CN[k] || k,
            )}</span><span class="form-val">${escapeHtml(v)}</span></div>`,
        )
        .join('')}</div>`
      if (e.forms_note) {
        html += `<div class="help">⚠️ ${escapeHtml(e.forms_note)}</div>`
      }
    }
    if (e.sources && e.sources.length) {
      html +=
        `<h4>讲义出处（${e.sources.length}）</h4>` +
        `<div class="kp-grid">${e.sources
          .map(
            (s) =>
              `<article class="kp-card" data-kp="${s.kp_id}"><div class="kp-title">${escapeHtml(
                s.title,
              )}</div><div class="kp-foot"><span class="kp-lecture">第 ${s.lecture} 讲</span></div></article>`,
          )
          .join('')}</div>`
    }
    if (!html) html = '<div class="empty">暂无详细信息。</div>'
    $body.innerHTML = html
    $body.scrollTop = 0
    open()
  }

  // ---------- 课程详情 ----------
  async function openLecture(number, meta) {
    setHead(meta ? meta.full_title : `第 ${number} 讲`, `<span>正在载入正文…</span>`, null)
    $body.innerHTML = '<div class="loading"><div class="spinner"></div>加载课程内容</div>'
    open()
    try {
      const data = await api.lecture(number, 'markdown')
      const cat = catColor(data.category)
      setHead(
        data.title ? `第 ${number} 讲 · ${data.title}` : `第 ${number} 讲`,
        `<span class="tag" style="--cat-color:${cat}">${escapeHtml(data.category)}</span>` +
          (meta?.subcategory ? `<span>· ${escapeHtml(meta.subcategory)}</span>` : '') +
          `<span>· ${data.format}</span>`,
        null,
      )
      $body.innerHTML = renderMd(data.content)
      $body.scrollTop = 0
    } catch (e) {
      $body.innerHTML = `<div class="error-box">加载失败：${escapeHtml(e.message)}</div>`
    }
  }

  return { el: root, openKp, openLecture, showWord, close }
}

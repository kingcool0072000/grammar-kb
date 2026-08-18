import * as echarts from 'echarts'
import { CATEGORIES, catColor } from '../theme.js'
import { escapeHtml } from '../render.js'

// 知识点关系网络图。
//
// 后端的 relations 字段几乎不携带显式连线目标（to_kp_id 全为空），
// 因此「网状关系」由前端基于知识点的共享属性推断连边。
// 提供三种可叠加的连边维度：
//   · 同讲：同一课程内的知识点彼此相关（体现课程结构）
//   · 同主题标签：共享细分标签（如「特殊疑问句」），跨讲的主题聚集
//   · 同时态标志：共享标志词时态（如「现在完成时」），跨讲的时态脉络
//
// 为控制图密度，每个属性组内按「讲序最近」把每个节点连向最近的 2 个邻居，
// 而非两两全连，避免组合爆炸。

const CATEGORY_SET = new Set(CATEGORIES)

// 三种连边维度的定义：每个 point 命中的「键」集合；同键即视为相关。
const EDGE_MODES = {
  lecture: {
    label: '同讲连边',
    hint: '同一课程内的知识点相连，对应讲义结构',
    keys: (p) => [`L${p.lectureNumber}`],
  },
  tag: {
    label: '同主题标签',
    hint: '共享细分标签（排除过宽的分类级标签），跨讲形成主题簇',
    keys: (p) => p.tags.filter((t) => !CATEGORY_SET.has(t)),
  },
  tense: {
    label: '同时态标志词',
    hint: '共享标志词所属时态，跨讲呈现时态脉络',
    keys: (p) => p.markers.map((m) => m.tense).filter(Boolean),
  },
}

export function mountNetwork(el, { points, openKp }) {
  const activeModes = new Set(['lecture'])

  el.innerHTML = `
    <div class="view-head">
      <h1>知识点关系网络</h1>
      <p>每个圆点是一个知识点，颜色代表语法分类。连线表示两个知识点存在关联——可在右侧切换关联依据。</p>
    </div>
    <div class="network-wrap">
      <div id="network-chart"></div>
      <div class="net-panel">
        <h3>连边依据</h3>
        <div class="help">勾选后图会重排。可多选叠加。</div>
        <div class="edge-mode" id="edge-modes">
          ${Object.entries(EDGE_MODES)
            .map(
              ([key, m]) => `
            <label>
              <input type="checkbox" value="${key}" ${activeModes.has(key) ? 'checked' : ''} />
              <span>${m.label}<small>${m.hint}</small></span>
            </label>`,
            )
            .join('')}
        </div>
        <h3>分类图例</h3>
        <div class="legend" id="legend"></div>
        <div class="help" id="net-stats" style="margin-top:14px"></div>
        <div class="help" id="net-selected" style="margin-top:8px;color:var(--ink-soft)">点击节点查看详情；拖拽可平移，滚轮缩放。</div>
      </div>
    </div>
  `

  const $chart = el.querySelector('#network-chart')
  const $legend = el.querySelector('#legend')
  const $stats = el.querySelector('#net-stats')
  const $selected = el.querySelector('#net-selected')
  const chart = echarts.init($chart)

  // 图例
  $legend.innerHTML = CATEGORIES.map(
    (c) =>
      `<div class="legend-row"><span class="cat-dot" style="background:${catColor(
        c,
      )}"></span>${escapeHtml(c)}</div>`,
  ).join('')

  const resize = () => chart.resize()
  window.addEventListener('resize', resize)

  function build() {
    const { nodes, links, byDegree } = buildGraph(points, activeModes)
    const catIdx = new Map(CATEGORIES.map((c, i) => [c, i]))
    const maxDeg = Math.max(1, ...byDegree.values())

    const seriesNodes = nodes.map((p) => {
      const deg = byDegree.get(p.id) || 0
      return {
        id: String(p.id),
        name: p.title,
        category: catIdx.has(p.category) ? catIdx.get(p.category) : CATEGORIES.length,
        itemStyle: { color: catColor(p.category) },
        symbolSize: 7 + (deg / maxDeg) * 18,
        value: deg,
        _p: p,
      }
    })

    chart.setOption(
      {
        tooltip: {
          formatter: (p) =>
            p.dataType === 'node'
              ? `<b>${escapeHtml(p.data.name)}</b><br/>${escapeHtml(
                  p.data._p.category,
                )} · 第 ${p.data._p.lectureNumber} 讲 · 连接 ${p.data.value}`
              : '',
        },
        legend: { show: false },
        series: [
          {
            type: 'graph',
            layout: 'force',
            animationDuration: 800,
            data: seriesNodes,
            links,
            categories: CATEGORIES.map((c) => ({ name: c })),
            roam: true,
            label: { show: false },
            emphasis: {
              focus: 'adjacency',
              label: { show: true, position: 'right', formatter: (d) => d.data.name },
              lineStyle: { width: 2 },
            },
            force: {
              repulsion: 70,
              edgeLength: [14, 48],
              gravity: 0.04,
              friction: 0.6,
            },
            lineStyle: { color: '#aab3c7', width: 0.7, opacity: 0.55, curveness: 0.08 },
          },
        ],
      },
      true,
    )

    $stats.innerHTML = `节点 <b>${nodes.length}</b> · 连线 <b>${links.length}</b>`
  }

  chart.on('click', (params) => {
    if (params.dataType === 'node' && params.data._p) {
      openKp(params.data._p)
    }
  })
  chart.on('mouseover', (params) => {
    if (params.dataType === 'node' && params.data._p) {
      $selected.innerHTML = `<b>${escapeHtml(params.data._p.title)}</b> · 第 ${params.data._p.lectureNumber} 讲`
    }
  })

  el.querySelector('#edge-modes').addEventListener('change', (e) => {
    const cb = e.target
    if (cb.checked) activeModes.add(cb.value)
    else {
      if (activeModes.size === 1) {
        // 至少保留一种，避免空图
        cb.checked = true
        return
      }
      activeModes.delete(cb.value)
    }
    build()
  })

  build()

  // 返回卸载函数
  return () => {
    window.removeEventListener('resize', resize)
    chart.dispose()
  }
}

// 根据选中的维度集合，构建节点与（去重后的）边，并统计度数。
function buildGraph(points, activeModes) {
  const seenEdge = new Set()
  const links = []
  const byDegree = new Map(points.map((p) => [p.id, 0]))

  const addEdge = (a, b) => {
    const lo = Math.min(a.id, b.id)
    const hi = Math.max(a.id, b.id)
    if (lo === hi) return
    const k = `${lo}-${hi}`
    if (seenEdge.has(k)) return
    seenEdge.add(k)
    links.push({ source: String(lo), target: String(hi) })
    byDegree.set(a.id, (byDegree.get(a.id) || 0) + 1)
    byDegree.set(b.id, (byDegree.get(b.id) || 0) + 1)
  }

  for (const modeKey of activeModes) {
    const mode = EDGE_MODES[modeKey]
    if (!mode) continue
    // 按「键」分桶
    const buckets = new Map()
    for (const p of points) {
      for (const k of mode.keys(p)) {
        ;(buckets.get(k) || buckets.set(k, []).get(k)).push(p)
      }
    }
    for (const group of buckets.values()) {
      if (group.length < 2) continue
      // 按讲序排序，每个节点连向最近的 2 个邻居，控制密度
      group.sort((a, b) => a.lectureNumber - b.lectureNumber || a.id - b.id)
      for (let i = 0; i < group.length; i++) {
        if (i - 1 >= 0) addEdge(group[i], group[i - 1])
        if (i - 2 >= 0) addEdge(group[i], group[i - 2])
      }
    }
  }

  // 只保留至少有一条边的节点，孤立点单独保留少量以免太空（保留全部更完整）
  return { nodes: points, links, byDegree }
}

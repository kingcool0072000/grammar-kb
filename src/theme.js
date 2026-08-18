// 6 大语法分类的配色。与 styles.css 中的 --c-* 保持一致，
// 这里给 JS（ECharts 等）提供实际 hex 值（CSS 变量无法传给 canvas）。
export const CATEGORIES = ['词法', '时态', '语态', '非谓语', '句法', '综合复习']

const COLORS = {
  词法: '#3b82f6',
  时态: '#8b5cf6',
  语态: '#06b6d4',
  非谓语: '#f59e0b',
  句法: '#ec4899',
  综合复习: '#10b981',
}

export function catColor(cat) {
  return COLORS[cat] || '#94a3b8'
}

// 课堂展示顺序（综合复习穿插在相应阶段后）
export const CAT_ORDER = CATEGORIES

export function sortByCategory(list) {
  const idx = new Map(CATEGORIES.map((c, i) => [c, i]))
  return [...list].sort((a, b) => (idx.get(a) ?? 99) - (idx.get(b) ?? 99))
}

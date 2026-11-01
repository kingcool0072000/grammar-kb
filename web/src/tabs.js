// 子 Tab 栏：批改中心（data-gtab）/ 计划管理（data-ptab）/ 视频管理（data-atab）
// 三处原本各写一份同一套模式——一排 reading-btn 按钮 + 点击写入 state 后重渲染。
// attr 是 data- 后缀名（如 'gtab'）；tabs 是 [key, label] 数组。

/** 渲染子 Tab 栏 HTML，active 对应的按钮加 primary 高亮。 */
export function subTabBar(attr, tabs, active) {
  return `<div class="ana-tabs plan-subtabs">${tabs
    .map(
      ([key, label]) =>
        `<button class="reading-btn ${active === key ? 'primary' : ''}" data-${attr}="${key}">${label}</button>`,
    )
    .join('')}</div>`
}

/** 给子 Tab 栏绑定点击，回调拿到被点的 key；写 state / 重渲染由调用方负责。 */
export function bindSubTabs(root, attr, onSwitch) {
  root.querySelectorAll(`[data-${attr}]`).forEach((b) =>
    b.addEventListener('click', () => onSwitch(b.dataset[attr])))
}

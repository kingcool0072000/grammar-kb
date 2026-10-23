export function h(tag, cls, html) {
  const e = document.createElement(tag)
  if (cls) e.className = cls
  if (html != null) e.innerHTML = html
  return e
}

// hash 路径切段：#/libraryReader/7 -> ['libraryReader','7']
export function routeSegs() {
  return (location.hash.replace(/^#\/?/, '') || '')
    .split('?')[0]
    .split('/')
    .filter(Boolean)
}

/** 换视图容器。旧容器上登记的清理（护眼 class / 监听 / 计时器）先跑掉。 */
export function swapView(viewEl) {
  const prev = viewEl.firstElementChild
  if (prev && Array.isArray(prev._cleanups)) prev._cleanups.forEach((fn) => fn())
  const container = h('div')
  viewEl.replaceChildren(container)
  return container
}

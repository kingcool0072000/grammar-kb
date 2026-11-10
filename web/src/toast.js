// 全站统一 toast：8 处逐字重复的本地实现（主前端 5 份 + 泛读馆 3 份）收口到这里。
// 纯结构搬移——两种形态的 DOM/class/时序与原实现完全一致，样式仍由
// styles.css 的 .plan-toast 与各 library/*.css 的 .lib-toast* 提供，本模块零 CSS。
//
// - 默认（主前端）：.plan-toast 直接挂 body，3.2s 后移除（type 不参与样式）。
// - 泛读馆（传 root）：root 顶部 .lib-toast-wrap 包裹，class 为
//   `lib-toast ${type}`（默认 info）；error 停 5s、其余 3s，消失前先加
//   .out 播 260ms 退场动画。
export function toast(msg, opts = {}) {
  const { type = 'info', root = null } = opts
  if (!root) {
    const t = document.createElement('div')
    t.className = 'plan-toast'
    t.textContent = msg
    document.body.appendChild(t)
    setTimeout(() => t.remove(), 3200)
    return
  }
  let wrap = root.querySelector(':scope > .lib-toast-wrap')
  if (!wrap) {
    wrap = document.createElement('div')
    wrap.className = 'lib-toast-wrap'
    root.append(wrap)
  }
  const t = document.createElement('div')
  t.className = `lib-toast ${type}`
  t.textContent = msg
  wrap.append(t)
  setTimeout(() => {
    t.classList.add('out')
    setTimeout(() => t.remove(), 260)
  }, type === 'error' ? 5000 : 3000)
}

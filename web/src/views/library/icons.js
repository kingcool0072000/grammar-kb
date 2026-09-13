// 泛读馆共享线性图标（Phosphor 风格：24 viewBox、stroke 2、round cap/join，
// 与 reader.js 顶栏既有 ICON_BACK/ICON_LIST 同一套参数）。
// 使用方以 className 控制尺寸/颜色（stroke=currentColor）。

function svg(inner, size = 18) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`
}

export const ICONS = {
  back: (s) => svg('<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>', s),
  list: (s) =>
    svg('<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>', s),
  // 耳机（预习/已生成标记）
  headphones: (s) =>
    svg(
      '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 14a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-1a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z"/><path d="M3 14a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h1a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1z"/>',
      s,
    ),
  // 音量（发音）
  sound: (s) =>
    svg(
      '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
      s,
    ),
  // 放大镜（查词/搜索）
  search: (s) => svg('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>', s),
  // 复制
  copy: (s) =>
    svg(
      '<rect width="13" height="13" x="9" y="9" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
      s,
    ),
  // 上传
  upload: (s) =>
    svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>', s),
  // 书本（无封面兜底/空态）
  book: (s) =>
    svg(
      '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
      s,
    ),
  // 书堆（泛读馆标题）
  books: (s) =>
    svg(
      '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/><path d="M2 2h16"/><path d="M2 22h16"/>',
      s,
    ),
  // 垃圾桶（删除）
  trash: (s) =>
    svg(
      '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
      s,
    ),
  // 闪电（一键生成）
  bolt: (s) => svg('<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>', s),
  // 幼苗（轻松空态）
  seedling: (s) =>
    svg(
      '<path d="M12 22V11"/><path d="M12 11C12 7 9 4 5 4H2v3c0 4 3 7 7 7h3z"/><path d="M12 11c0-4 3-7 7-7h3v3c0 4-3 7-7 7h-3z"/>',
      s,
    ),
  // 警示（错误态）
  warn: (s) =>
    svg('<path d="m21.7 18.3-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-2.7z"/><path d="M12 9v4"/><path d="M12 17h.01"/>', s),
}

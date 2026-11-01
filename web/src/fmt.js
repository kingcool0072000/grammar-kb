// 全站共享的格式化工具：时长（多种口径）+ 词性/词形中文映射。
// 只放纯函数与常量，不依赖任何其它模块（避免循环引用）。

// ---- 时长格式化 ------------------------------------------------------------- //
// 历史上各视图各写了一份，口径并不相同（钟表式 / 中文口语 / 图表刻度 / 粗粒度
// 分钟），因此这里按口径分别导出，而不是强行合成一个带开关的函数。

/** 秒 → m:ss（分钟不补零，如 5:07）。列表/历史记录里最常用的钟表格式。 */
export function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

/** 秒 → mm:ss（分钟补零，如 05:07）。练习结果页需要定宽展示。 */
export function fmtDurPad(sec) {
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}

/** 秒 → 中文口语，整分时省略秒（"30 秒" / "5 分" / "5 分 30 秒"）。 */
export function fmtDurCn(sec) {
  const m = Math.floor(sec / 60)
  return m >= 1 ? `${m} 分${sec % 60 ? ` ${sec % 60} 秒` : ''}` : `${sec} 秒`
}

/** 秒 → 中文口语，支持小时，分钟段始终带秒（"45 秒" / "2 分 5 秒" / "1 时 3 分"）。 */
export function fmtDuration(sec) {
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
  return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`
}

/** 秒 → 图表坐标刻度文字（"45s" / "2:05"），秒四舍五入。 */
export function fmtTick(sec) {
  const m = Math.floor(sec / 60)
  const r = Math.round(sec % 60)
  return m === 0 ? `${r}s` : `${m}:${String(r).padStart(2, '0')}`
}

/** 秒 → 粗粒度分钟（四舍五入到整分："90 秒" / "5 分钟"），空值按 0 处理。 */
export function fmtMinCn(sec) {
  const m = Math.round((sec || 0) / 60)
  return m >= 1 ? `${m} 分钟` : `${sec || 0} 秒`
}

// ---- 词性 / 词形中文映射 ---------------------------------------------------- //
// 各视图原文案不完全一致（详见各变体注释），基准版取背单词（recite）的全称口径；
// 差异以「基准 + 派生覆盖」的方式并存，保证改动不触碰任何现有界面文案。

/** 词性 → 中文（全称口径，与背单词卡片原版逐键一致；作过滤谓词用，勿加兜底键）。 */
export const POS_CN = {
  v: '动词', n: '名词', adj: '形容词', adv: '副词', prep: '介词',
  conj: '连词', pron: '代词', num: '数词', proper: '专名',
}

/** 词性 → 中文（筛选 chip 用的单字缩写 + 「其它」兜底键，词汇表视图口径）。 */
export const POS_CN_SHORT = {
  ...POS_CN, adj: '形', adv: '副', prep: '介', conj: '连', num: '数', 其它: '其它',
}

/** 词性 → 中文（proper 用全称「专有名词」，词条详情抽屉口径）。 */
export const POS_CN_FULLNAME = { ...POS_CN, proper: '专有名词' }

/** 词形变化键 → 中文（全称口径）。 */
export const FORM_CN = {
  past: '过去式', past_participle: '过去分词', present_participle: '现在分词',
  third_singular: '第三人称单数', plural: '复数', comparative: '比较级', superlative: '最高级',
}

/** 词形变化键 → 中文（third_singular 缩写「三单」，词条详情抽屉口径）。 */
export const FORM_CN_SHORT = { ...FORM_CN, third_singular: '三单' }

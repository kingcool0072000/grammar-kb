// 学生专题页共用的两件小事：标题里不出现开放名单，列表和详情用同一套完成数。

// 「malin,mxy 的专属专题」整段是开放徽章，不是专题标题。名字可以是
// 用户名或中文名，用逗号 / 顿号分开。真正的标题（如
// 「哈1 · 第22～28讲 动词时态专题」）不含这句，原样留下。
const NAME = '[A-Za-z0-9_\\u4e00-\\u9fff]{1,32}'
const EXCLUSIVE_BADGE = new RegExp(
  `(?:^|\\s|[·•|｜])(?:${NAME}(?:\\s*[,，、]\\s*${NAME})*)\\s*的专属专题`,
  'g',
)

export function studentTopicText(text) {
  const raw = String(text ?? '')
  if (!raw.trim()) return ''
  return raw
    .replace(EXCLUSIVE_BADGE, '')
    .replace(/\s*[·•|｜]\s*$/g, '')
    .replace(/^\s*[·•|｜]\s*/g, '')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\s*[·•|｜]\s*[·•|｜]\s*/g, ' · ')
    .trim()
}

/**
 * 已完成 / 总节点。doneKeys 与节点 key 取交集，多出来的旧 key 不计入。
 * nodeKeys 缺省时只能数 doneKeys 本身（total 为 null，调用方用 node_count）。
 */
export function topicDoneCount(doneKeys, nodeKeys) {
  const doneSet = new Set(
    (doneKeys || [])
      .map((k) => String(k ?? '').trim())
      .filter(Boolean),
  )
  if (!Array.isArray(nodeKeys)) return { done: doneSet.size, total: null }
  const seen = new Set()
  let done = 0
  for (const key of nodeKeys) {
    const k = String(key ?? '').trim()
    if (!k || seen.has(k)) continue
    seen.add(k)
    if (doneSet.has(k)) done += 1
  }
  return { done, total: seen.size }
}

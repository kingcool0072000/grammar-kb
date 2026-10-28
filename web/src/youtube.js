// YouTube IFrame Player API 懒加载（视频学习受限播放器用）。
// 失败/超时可重试（清缓存重新注入 <script>）。
let apiPromise = null

export function loadYouTubeApi() {
  if (apiPromise) return apiPromise
  apiPromise = new Promise((resolve, reject) => {
    if (window.YT && window.YT.Player) return resolve(window.YT)
    const prev = window.onYouTubeIframeAPIReady
    window.onYouTubeIframeAPIReady = () => {
      try { prev && prev() } catch { /* 上一个等待者的回调异常不阻塞 */ }
      resolve(window.YT)
    }
    const s = document.createElement('script')
    s.src = 'https://www.youtube.com/iframe_api'
    s.onerror = () => reject(new Error('YouTube API 加载失败（需可访问 YouTube 的网络）'))
    document.head.append(s)
    setTimeout(() => {
      if (!(window.YT && window.YT.Player)) {
        reject(new Error('YouTube API 加载超时'))
      }
    }, 9000)
  }).catch((e) => {
    apiPromise = null // 允许下次重试
    throw e
  })
  return apiPromise
}

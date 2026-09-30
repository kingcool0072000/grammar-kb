import { defineConfig } from 'vite'

// 开发期把 /api 代理到 grammar-kb 后端，避免浏览器跨域。
// 生产部署时由反向代理（nginx 等）承担同样的转发。
export default defineConfig({
  server: {
    port: 5180,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    // 修复：同名 chunk（如 readingReview-C88fsjRt.js）内容更新后浏览器强缓存不刷新。
    // vite 默认 hash 只看模块内容，构建配置变化不参与——改为显式带时间戳的文件名，
    // 每次构建必然换名，no-cache 的 index.html 会引用新名，彻底破缓存。
    rollupOptions: {
      output: {
        chunkFileNames: (ci) => `assets/${ci.name}-${Date.now().toString(36)}.js`,
        entryFileNames: `assets/[name]-${Date.now().toString(36)}.js`,
        assetFileNames: `assets/[name]-${Date.now().toString(36)}[extname]`,
      },
    },
  },
})

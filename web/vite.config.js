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
})

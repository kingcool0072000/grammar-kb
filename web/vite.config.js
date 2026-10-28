import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function readAppVersion() {
  try {
    return readFileSync(resolve(repoRoot, 'grammar_kb/VERSION'), 'utf8').trim()
  } catch {
    return ''
  }
}

function gitShort() {
  try {
    return execSync('git rev-parse --short HEAD', {
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).toString().trim()
  } catch {
    return ''
  }
}

// 开发期把 /api 代理到 grammar-kb 后端，避免浏览器跨域。
// 生产部署时由反向代理（nginx 等）承担同样的转发。
// 版本号从仓库 grammar_kb/VERSION 注入，不在前端再写一份。
export default defineConfig({
  define: {
    __GKB_VERSION__: JSON.stringify(readAppVersion()),
    __GKB_GIT_SHA__: JSON.stringify(gitShort()),
  },
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

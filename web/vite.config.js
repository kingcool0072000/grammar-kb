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
    // 产物文件名用内容 hash：内容不变则文件名不变，跨发版浏览器缓存继续
    // 命中；内容变则名变，no-cache 的 index.html 必然引用到新文件。
    // （曾用 Date.now() 时间戳强制每次换名——代价是所有用户每次发版都全量
    // 重新下载；服务端对带 hash 资源已回 immutable 一年，内容 hash 是正解。）
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
})

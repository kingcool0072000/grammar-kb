// 构建时由 vite.config.js 从 grammar_kb/VERSION 注入。改版本只改那个文件。
export const APP_VERSION = typeof __GKB_VERSION__ === 'string' ? __GKB_VERSION__ : 'dev'
export const APP_GIT_SHA = typeof __GKB_GIT_SHA__ === 'string' ? __GKB_GIT_SHA__ : ''

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { cpSync, existsSync, mkdirSync } from 'fs'
import { fileURLToPath, URL } from 'url'

const copyPdfJsAssets = () => ({
  name: 'copy-pdfjs-assets',
  closeBundle() {
    const distPdfjs = fileURLToPath(new URL('./dist/pdfjs', import.meta.url))
    mkdirSync(distPdfjs, { recursive: true })
    for (const dir of ['cmaps', 'standard_fonts']) {
      const source = fileURLToPath(new URL(`./node_modules/pdfjs-dist/${dir}`, import.meta.url))
      const target = fileURLToPath(new URL(`./dist/pdfjs/${dir}`, import.meta.url))
      if (existsSync(source)) {
        cpSync(source, target, { recursive: true })
      }
    }
  }
})

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue(), copyPdfJsAssets()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '/api')
      }
    }
  },
  preview: {
    proxy: {
      '/api': {
        target: process.env.VITE_PREVIEW_API_TARGET || 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    // 代码分割优化
    rollupOptions: {
      output: {
        manualChunks: {
          'element-plus': ['element-plus'],
          'markdown': ['marked', 'markdown-it', 'markdown-it-katex'],
          'office': ['mammoth', 'xlsx'],
          'pdf': ['pdfjs-dist'],
          'utils': ['jszip', 'axios', 'uuid']
        }
      }
    },
    chunkSizeWarningLimit: 1000
  },
  esbuild: {
    // 生产环境移除 console 和 debugger
    drop: process.env.NODE_ENV === 'production' ? ['console', 'debugger'] : []
  }
})

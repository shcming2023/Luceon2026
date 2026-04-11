import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/cms/',
  plugins: [
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  assetsInclude: ['**/*.svg', '**/*.csv'],
  server: {
    host: '0.0.0.0',
    allowedHosts: 'all',
    headers: {
      // 仅开发环境：允许 iframe 嵌入以支持 Manus 等平台预览。
      // 生产环境安全策略由 Nginx 配置决定，此处设置不生效。
      // 注意：X-Frame-Options 已被 CSP frame-ancestors 取代，不再设置
      'Content-Security-Policy': "frame-ancestors *",
    },
    proxy: {
      // ⚠️ mineru-cdn 必须在 mineru 之前，否则被前缀匹配吞掉
      '/__proxy/mineru-cdn': {
        target: 'https://cdn-mineru.openxlab.org.cn',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace('/__proxy/mineru-cdn', ''),
      },
      '/__proxy/mineru': {
        target: 'https://mineru.net',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace('/__proxy/mineru', ''),
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            const chunks: Buffer[] = [];
            req.on('data', (chunk: Buffer) => chunks.push(chunk));
            req.on('end', () => {
              const body = Buffer.concat(chunks).toString();
              let display = body;
              try {
                const parsed = JSON.parse(body) as Record<string, unknown>;
                if (typeof parsed['file_base64'] === 'string') {
                  parsed['file_base64'] = `<base64 ${(parsed['file_base64'] as string).length} chars>`;
                }
                display = JSON.stringify(parsed, null, 2);
              } catch { /* not json */ }
              console.log('\n\x1b[36m[MinerU Proxy] ➜ Request\x1b[0m');
              console.log(`  ${req.method} ${req.url}`);
              console.log(`  Authorization: ${proxyReq.getHeader('authorization')?.toString().slice(0, 20)}…`);
              if (display && display !== '{}') console.log('  Body:', display.slice(0, 600));
            });
          });
          proxy.on('proxyRes', (proxyRes, req) => {
            const chunks: Buffer[] = [];
            proxyRes.on('data', (chunk: Buffer) => chunks.push(chunk));
            proxyRes.on('end', () => {
              const body = Buffer.concat(chunks).toString();
              console.log('\n\x1b[33m[MinerU Proxy] ← Response\x1b[0m');
              console.log(`  ${req.method} ${req.url} → HTTP ${proxyRes.statusCode}`);
              console.log('  Body:', body.slice(0, 800));
            });
          });
          proxy.on('error', (err, req) => {
            console.error('\n\x1b[31m[MinerU Proxy] ✗ Error\x1b[0m', req.url, err.message);
          });
        },
      },
      '/__proxy/mineru-local': {
        target: process.env.LOCAL_MINERU_URL || 'http://192.168.31.33:8083',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace('/__proxy/mineru-local', ''),
      },
      '/__proxy/kimi': {
        target: 'https://api.kimi.ai',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace('/__proxy/kimi', ''),
      },
      '/__proxy/moonshot': {
        target: 'https://api.moonshot.cn',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace('/__proxy/moonshot', ''),
      },
      '/__proxy/upload': {
        target: process.env.UPLOAD_PROXY_TARGET || 'http://localhost:8788',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/__proxy\/upload/, ''),
      },
      '/__proxy/db': {
        target: process.env.DB_PROXY_TARGET || 'http://localhost:8789',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/__proxy\/db/, ''),
      },
      '/__proxy/tmpfiles': {
        target: 'https://tmpfiles.org',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace('/__proxy/tmpfiles', ''),
      },
      // Overleaf 备份后端 API 代理（开发环境）
      // 生产环境（Docker）由 nginx.conf 中的 /api/ 代理规则处理
      // 请将 BACKUP_API_TARGET 设置为实际后端地址，如 http://localhost:3001
      '/api': {
        target: process.env.BACKUP_API_TARGET || 'http://localhost:3001',
        changeOrigin: true,
        secure: false,
        // 不 rewrite，保持 /api/xxx 路径原样转发
      },
    },
  },
})

/**
 * proxy-server.mjs — 局域网访问反向代理
 *
 * 监听 0.0.0.0:8081，路由规则：
 *   /__proxy/upload/*  →  upload-server (localhost:8788)
 *   /__proxy/db/*      →  db-server     (localhost:8789)（注入 X-Internal-Token 头）
 *   /health            →  本地健康检查
 *   /minio/*           →  MinIO API     (MINIO_HOST:9000)
 *   /__proxy/*         →  按子路径代理外部服务
 *   /*                 →  dist/ 静态文件 (SPA fallback to index.html)
 *
 * 用法：
 *   node server/proxy-server.mjs
 *
 * 环境变量：
 *   PROXY_PORT           代理监听端口，默认 8081
 *   UPLOAD_PORT          upload-server 端口，默认 8788
 *   DB_PORT              db-server 端口，默认 8789
 *   MINIO_HOST           MinIO 主机，默认 192.168.31.33
 *   MINIO_PORT           MinIO API 端口，默认 9000
 *   MINERU_HOST          MinerU 服务主机，默认 192.168.31.33
 *   MINERU_PORT          MinerU 服务端口，默认 8083
 *   INTERNAL_API_TOKEN   内网服务间共享密钥（db-server 同值），防止 db-server 裸露
 *   NODE_ENV             设为 production 时启用 HTTPS 证书校验
 */

import http from 'http';
import https from 'https';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.join(__dirname, '..', 'dist');

// ── 配置 ──────────────────────────────────────────────────────
const PROXY_PORT  = Number(process.env.PROXY_PORT  || 8081);
const UPLOAD_PORT = Number(process.env.UPLOAD_PORT || 8788);
const DB_PORT     = Number(process.env.DB_PORT     || 8789);
const MINIO_HOST  = process.env.MINIO_HOST || '192.168.31.33';
const MINIO_PORT  = Number(process.env.MINIO_PORT  || 9000);
const MINERU_HOST = process.env.MINERU_HOST || '192.168.31.33';
const MINERU_PORT = Number(process.env.MINERU_PORT || 8083);

// 内网服务间共享密钥，转发到 db-server 时自动注入 X-Internal-Token 头
// db-server 须配置相同值的 INTERNAL_API_TOKEN 环境变量
const INTERNAL_API_TOKEN = process.env.INTERNAL_API_TOKEN || '';

// 生产模式下启用 HTTPS 证书校验
const IS_PRODUCTION = process.env.NODE_ENV === 'production';

// MIME 类型映射
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.mjs':  'application/javascript',
  '.css':  'text/css',
  '.json': 'application/json',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif':  'image/gif',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
  '.woff': 'font/woff',
  '.woff2':'font/woff2',
  '.ttf':  'font/ttf',
  '.webp': 'image/webp',
  '.pdf':  'application/pdf',
  '.txt':  'text/plain',
  '.map':  'application/json',
};

// ── 工具函数 ──────────────────────────────────────────────────

/**
 * 将请求反向代理到目标服务器
 */
function proxyRequest(req, res, options) {
  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error(`[proxy] ${options.host}:${options.port}${options.path} - ${err.message}`);
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Bad Gateway', detail: err.message }));
    }
  });

  req.pipe(proxyReq);
}

/**
 * 代理到 HTTPS 目标
 * 生产模式（NODE_ENV=production）启用证书校验；开发/UAT 模式关闭，兼容自签名证书。
 */
function proxyHttpsRequest(req, res, options) {
  const proxyReq = https.request({ ...options, rejectUnauthorized: IS_PRODUCTION }, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error(`[proxy-https] ${options.host}${options.path} - ${err.message}`);
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Bad Gateway', detail: err.message }));
    }
  });

  req.pipe(proxyReq);
}

/**
 * 提供静态文件，找不到时 fallback 到 index.html（SPA 模式）
 */
function serveStatic(req, res) {
  let urlPath = req.url.split('?')[0]; // 去掉 query string

  // 去掉 /cms 前缀（如果有）
  if (urlPath.startsWith('/cms')) {
    urlPath = urlPath.slice(4) || '/';
  }

  const filePath = path.join(DIST_DIR, urlPath);

  // ── 路径越界检查：防止 ../../ 路径遍历 ──────────────────────
  const resolvedPath = path.resolve(filePath);
  const resolvedDist = path.resolve(DIST_DIR);
  if (!resolvedPath.startsWith(resolvedDist + path.sep) && resolvedPath !== resolvedDist) {
    res.writeHead(400, { 'Content-Type': 'text/plain' });
    res.end('400 Bad Request\n');
    return;
  }

  // 尝试直接访问文件
  const tryFile = (fp) => {
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      const ext = path.extname(fp).toLowerCase();
      const mime = MIME[ext] || 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': mime });
      fs.createReadStream(fp).pipe(res);
      return true;
    }
    return false;
  };

  if (tryFile(filePath)) return;
  if (tryFile(filePath + '.html')) return;
  if (tryFile(path.join(filePath, 'index.html'))) return;

  // SPA fallback
  const indexPath = path.join(DIST_DIR, 'index.html');
  if (fs.existsSync(indexPath)) {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    fs.createReadStream(indexPath).pipe(res);
  } else {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('404 Not Found (dist/ not built yet, run: pnpm build)\n');
  }
}

// ── 主路由 ────────────────────────────────────────────────────

const server = http.createServer((req, res) => {
  const url = req.url || '/';
  const method = req.method;

  // CORS 预检
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS,PATCH');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With');
  if (method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // ── 健康检查 ──
  if (url === '/health' || url === '/health/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, service: 'proxy-server', port: PROXY_PORT }));
    return;
  }

  // ── upload-server：/__proxy/upload/* → upload-server（strip 前缀）──
  if (url.startsWith('/__proxy/upload')) {
    const backendPath = url.replace(/^\/__proxy\/upload/, '') || '/';
    proxyRequest(req, res, {
      host: 'localhost',
      port: UPLOAD_PORT,
      path: backendPath,
      method,
      headers: { ...req.headers, host: `localhost:${UPLOAD_PORT}` },
    });
    return;
  }

  // ── db-server：/__proxy/db/* → db-server（strip 前缀）──
  if (url.startsWith('/__proxy/db')) {
    const backendPath = url.replace(/^\/__proxy\/db/, '') || '/';
    // 注入内网 Token，db-server 凭此验证请求来自受信任的 proxy
    const dbHeaders = { ...req.headers, host: `localhost:${DB_PORT}` };
    if (INTERNAL_API_TOKEN) {
      dbHeaders['x-internal-token'] = INTERNAL_API_TOKEN;
    }
    proxyRequest(req, res, {
      host: 'localhost',
      port: DB_PORT,
      path: backendPath,
      method,
      headers: dbHeaders,
    });
    return;
  }

  // ── MinIO 反向代理 /minio/* → MinIO API ──
  if (url.startsWith('/minio/')) {
    const minioPath = url.slice(6); // '/minio/bucket/...' → '/bucket/...'
    proxyRequest(req, res, {
      host: MINIO_HOST,
      port: MINIO_PORT,
      path: minioPath,
      method,
      headers: { ...req.headers, host: `${MINIO_HOST}:${MINIO_PORT}` },
    });
    return;
  }

  // ── MinerU 代理 /__proxy/mineru-local/* ──
  if (url.startsWith('/__proxy/mineru-local/')) {
    const mineruPath = url.slice('/__proxy/mineru-local'.length);
    proxyRequest(req, res, {
      host: MINERU_HOST,
      port: MINERU_PORT,
      path: mineruPath || '/',
      method,
      headers: { ...req.headers, host: MINERU_HOST },
    });
    return;
  }

  // ── MinerU CDN 代理 /__proxy/mineru-cdn/* ──
  if (url.startsWith('/__proxy/mineru-cdn')) {
    const p = url.replace('/__proxy/mineru-cdn', '') || '/';
    proxyHttpsRequest(req, res, { host: 'cdn-mineru.openxlab.org.cn', path: p, method, headers: { ...req.headers, host: 'cdn-mineru.openxlab.org.cn' } });
    return;
  }

  // ── MinerU API 代理 /__proxy/mineru/* ──
  if (url.startsWith('/__proxy/mineru')) {
    const p = url.replace('/__proxy/mineru', '') || '/';
    proxyHttpsRequest(req, res, { host: 'mineru.net', path: p, method, headers: { ...req.headers, host: 'mineru.net' } });
    return;
  }

  // ── Kimi API 代理 /__proxy/kimi/* ──
  if (url.startsWith('/__proxy/kimi')) {
    const p = url.replace('/__proxy/kimi', '') || '/';
    proxyHttpsRequest(req, res, { host: 'api.kimi.ai', path: p, method, headers: { ...req.headers, host: 'api.kimi.ai' } });
    return;
  }

  // ── Moonshot API 代理 /__proxy/moonshot/* ──
  if (url.startsWith('/__proxy/moonshot')) {
    const p = url.replace('/__proxy/moonshot', '') || '/';
    proxyHttpsRequest(req, res, { host: 'api.moonshot.cn', path: p, method, headers: { ...req.headers, host: 'api.moonshot.cn' } });
    return;
  }

  // ── tmpfiles.org 代理 /__proxy/tmpfiles/* ──
  if (url.startsWith('/__proxy/tmpfiles/')) {
    const tmpPath = url.slice('/__proxy/tmpfiles'.length);
    proxyHttpsRequest(req, res, {
      host: 'tmpfiles.org',
      path: tmpPath || '/',
      method,
      headers: { ...req.headers, host: 'tmpfiles.org' },
    });
    return;
  }

  // ── 根路径重定向到 /cms/ ──
  if (url === '/' || url === '') {
    res.writeHead(302, { Location: '/cms/' });
    res.end();
    return;
  }

  // ── 静态文件（前端 dist/） ──
  serveStatic(req, res);
});

server.on('error', (err) => {
  console.error('[proxy-server] Error:', err.message);
  process.exit(1);
});

server.listen(PROXY_PORT, '0.0.0.0', () => {
  console.log(`
╔══════════════════════════════════════════════════════╗
║         EduAsset CMS — 局域网反向代理已启动           ║
╠══════════════════════════════════════════════════════╣
║  访问地址：http://${MINIO_HOST}:${PROXY_PORT}              ║
║  代理规则：                                           ║
║    /minio/*              → MinIO (${MINIO_HOST}:${MINIO_PORT})    ║
║    /__proxy/upload/*     → upload-server (:${UPLOAD_PORT})     ║
║    /__proxy/db/*         → db-server (:${DB_PORT})         ║
║    /__proxy/mineru*      → MinerU (外部 API)          ║
║    /__proxy/kimi*        → Kimi AI API               ║
║    /__proxy/moonshot*    → Moonshot AI API           ║
║    /*                    → dist/ 静态文件 (SPA)      ║
╚══════════════════════════════════════════════════════╝
`);
});

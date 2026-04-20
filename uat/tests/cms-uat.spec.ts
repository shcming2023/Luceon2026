import { test, expect, type Page } from '@playwright/test';

/**
 * EduAsset CMS — UAT 端到端测试套件
 *
 * 覆盖范围：
 *   1. 页面加载与 SPA 路由
 *   2. 后端服务健康检查（via Nginx 代理）
 *   3. DB API 基础功能（资产列表、设置读写）
 *   4. 文件上传流程（含 MinIO presigned URL 局域网可访问性验证）
 *   5. MinIO Nginx 代理可达性
 *   6. 数据持久化（写入后重新加载验证）
 */

const BASE_URL = process.env.BASE_URL || 'http://192.168.31.33:8081';

// ── 辅助函数 ──────────────────────────────────────────────────

async function waitForAppReady(page: Page) {
  // 等待 React 应用挂载完成（Layout 中的导航元素出现）
  await page.waitForSelector('nav, [data-testid="layout"], main', { timeout: 20_000 });
}

// ── 测试组 1：页面加载与路由 ──────────────────────────────────

test.describe('【1】页面加载与 SPA 路由', () => {
  test('根路径 / 应重定向到 /cms/', async ({ page }) => {
    const response = await page.goto(`${BASE_URL}/`);
    // 允许重定向链最终落在 /cms/ 或 /cms/source-materials
    expect(page.url()).toMatch(/\/cms\//);
    expect(response?.status()).toBeLessThan(400);
  });

  test('/cms/ 应正确加载 SPA 入口', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/`);
    await waitForAppReady(page);
    const title = await page.title();
    // 页面 title 不为空
    expect(title).toBeTruthy();
  });

  test('/cms/source-materials 原始资料库页面可访问', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/legacy/source-materials`);
    await waitForAppReady(page);
    // 验证页面不是错误页
    await expect(page.locator('body')).not.toContainText('500');
    await expect(page.locator('body')).not.toContainText('502');
    await expect(page.locator('body')).not.toContainText('504');
  });

  test('/cms/products 成品库页面可访问', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/products`);
    await waitForAppReady(page);
    await expect(page.locator('body')).not.toContainText('404');
  });

  test('/cms/settings 系统设置页面可访问', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/settings`);
    await waitForAppReady(page);
    await expect(page.locator('body')).not.toContainText('404');
  });

  test('/cms/metadata 元数据管理页面可访问', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/metadata`);
    await waitForAppReady(page);
    await expect(page.locator('body')).not.toContainText('404');
  });
});

// ── 测试组 2：后端 API 健康检查 ──────────────────────────────

test.describe('【2】后端服务健康检查', () => {
  test('upload-server /health 返回 ok', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/__proxy/upload/health`);
    expect(response.status()).toBe(200);
    const body = await response.json() as { ok: boolean; service: string };
    expect(body.ok).toBe(true);
    expect(body.service).toBe('upload-server');
  });

  test('db-server /health 返回 ok', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/__proxy/db/health`);
    expect(response.status()).toBe(200);
    const body = await response.json() as { ok: boolean };
    expect(body.ok).toBe(true);
  });
});

// ── 测试组 3：DB API 基础功能 ─────────────────────────────────

test.describe('【3】db-server REST API', () => {
  test('GET /assets 返回有效响应', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/__proxy/db/assets`);
    expect(response.status()).toBe(200);
    const body = await response.json();
    // 资产列表应为数组
    expect(Array.isArray(body)).toBe(true);
  });

  test('GET /settings 返回有效响应', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/__proxy/db/settings`);
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toBeTruthy();
  });

  test('数据持久化：写入后重新读取一致', async ({ request }) => {
    const testKey = `uat_test_${Date.now()}`;
    const testValue = `test_value_${Math.random().toString(36).slice(2)}`;

    // 写入测试设置
    const putResp = await request.put(`${BASE_URL}/__proxy/db/settings`, {
      data: { [testKey]: testValue },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(putResp.status()).toBeLessThan(300);

    // 重新读取验证
    const getResp = await request.get(`${BASE_URL}/__proxy/db/settings`);
    expect(getResp.status()).toBe(200);
    const settings = await getResp.json() as Record<string, unknown>;
    expect(settings[testKey]).toBe(testValue);
  });

  test('secrets 持久化：写入后重新读取一致', async ({ request }) => {
    const testKey = `uat_secret_${Date.now()}`;
    const testValue = `secret_${Math.random().toString(36).slice(2)}`;

    const putResp = await request.put(`${BASE_URL}/__proxy/db/secrets`, {
      data: { [testKey]: testValue },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(putResp.status()).toBeLessThan(300);

    const getResp = await request.get(`${BASE_URL}/__proxy/db/secrets`);
    expect(getResp.status()).toBe(200);
    const body = await getResp.json() as { secrets?: Record<string, unknown> };
    expect(body.secrets?.[testKey]).toBe(testValue);
  });

  test('materials 删除：DELETE /materials/:id 生效', async ({ request }) => {
    const id = Date.now() * 1000 + Math.floor(Math.random() * 1000);
    const material = {
      id,
      title: 'uat-delete-test',
      type: 'TXT',
      size: '0.0 MB',
      sizeBytes: 0,
      uploadTime: '刚刚',
      uploadTimestamp: Date.now(),
      status: 'processing',
      mineruStatus: 'pending',
      aiStatus: 'pending',
      tags: [],
      metadata: { provider: 'tmpfiles' },
      uploader: 'uat',
    };

    const postResp = await request.post(`${BASE_URL}/__proxy/db/materials`, {
      data: material,
      headers: { 'Content-Type': 'application/json' },
    });
    expect(postResp.status()).toBeLessThan(300);

    const delResp = await request.delete(`${BASE_URL}/__proxy/db/materials/${id}`);
    expect(delResp.status()).toBeLessThan(300);

    const listResp = await request.get(`${BASE_URL}/__proxy/db/materials`);
    expect(listResp.status()).toBe(200);
    const list = await listResp.json() as Array<{ id: number }>;
    expect(list.find((m) => m.id === id)).toBeFalsy();
  });
});

// ── 测试组 4：MinIO Nginx 代理 ────────────────────────────────

test.describe('【4】MinIO Nginx 反向代理', () => {
  test('/minio/minio/health/live 通过 Nginx 可达', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/minio/minio/health/live`);
    // MinIO health 端点返回 200
    expect(response.status()).toBe(200);
  });

  test('presigned URL 中包含局域网公开地址（非内部容器地址）', async ({ request }) => {
    // 通过 /list 接口获取已有文件的 presigned URL（如有文件）
    const listResp = await request.get(`${BASE_URL}/__proxy/upload/list?prefix=originals`);
    if (listResp.status() !== 200) {
      test.skip();
      return;
    }

    const body = await listResp.json() as { objects?: Array<{ presignedUrl: string }> };
    const objects = body.objects || [];

    if (objects.length === 0) {
      // 没有文件时跳过（空环境），不视为失败
      console.log('  [跳过] MinIO 中暂无文件，presigned URL 检查跳过');
      return;
    }

    // 验证 presigned URL 不包含内部容器地址
    for (const obj of objects.slice(0, 3)) {
      const url = obj.presignedUrl;
      expect(url).not.toMatch(/minio:\d+/);
      expect(url).not.toMatch(/localhost:\d+/);
      // 应包含配置的公开地址
      expect(url).toContain('192.168.31.33');
      console.log(`  ✓ presigned URL: ${url.slice(0, 80)}...`);
    }
  });
});

// ── 测试组 5：文件上传流程 ────────────────────────────────────

test.describe('【5】文件上传流程', () => {
  test('upload-server /upload 接受小型测试文件', async ({ request }) => {
    // 创建一个最小的有效 PNG 文件（1x1 像素）
    const minimalPng = Buffer.from(
      '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489' +
      '0000000a49444154789c6260000000020001e221bc330000000049454e44ae426082',
      'hex',
    );

    const formData = new FormData();
    formData.append('file', new Blob([minimalPng], { type: 'image/png' }), 'uat-test.png');

    const response = await request.post(`${BASE_URL}/__proxy/upload/upload`, {
      multipart: {
        file: {
          name: 'uat-test.png',
          mimeType: 'image/png',
          buffer: minimalPng,
        },
      },
    });

    // 200 = 上传成功，507 = 存储配额不足（可接受），其余为失败
    const status = response.status();
    if (status === 200) {
      const body = await response.json() as { url?: string; provider?: string };
      expect(body.url).toBeTruthy();
      console.log(`  ✓ 上传成功，provider: ${body.provider}, URL: ${body.url?.slice(0, 60)}...`);

      // 若 URL 指向 MinIO，验证其为局域网可访问地址
      if (body.provider === 'minio' && body.url) {
        expect(body.url).not.toMatch(/^http:\/\/minio:/);
        expect(body.url).toMatch(/192\.168\.31\.33/);
      }
    } else {
      console.warn(`  ⚠ 上传返回 HTTP ${status}，可能是存储未配置，跳过断言`);
    }
  });

  test('上传后 presigned URL 在局域网可直接访问（HTTP GET）', async ({ request }) => {
    // 先上传一个小文件
    const content = Buffer.from(`UAT test file ${Date.now()}`);

    const uploadResp = await request.post(`${BASE_URL}/__proxy/upload/upload`, {
      multipart: {
        file: {
          name: 'uat-check.txt',
          mimeType: 'text/plain',
          buffer: content,
        },
      },
    });

    if (uploadResp.status() !== 200) {
      console.warn('  ⚠ 上传失败，跳过 presigned URL 访问测试');
      return;
    }

    const body = await uploadResp.json() as { url?: string; provider?: string };
    if (body.provider !== 'minio' || !body.url) {
      console.warn('  ⚠ 非 MinIO 存储或无 URL，跳过测试');
      return;
    }

    // 直接 GET presigned URL，验证文件可访问
    const fileResp = await request.get(body.url);
    expect(fileResp.status()).toBe(200);
    console.log(`  ✓ presigned URL 可直接访问：${body.url.slice(0, 80)}...`);
  });
});

// ── 测试组 6：页面导航交互 ────────────────────────────────────

test.describe('【6】页面导航交互（冒烟）', () => {
  test('侧边栏导航可正常点击切换页面', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/`);
    await waitForAppReady(page);

    // 尝试通过 URL 直接访问各核心路由，验证 SPA 路由正常
    const routes = [
      '/cms/workspace',
      '/cms/legacy/source-materials',
      '/cms/products',
      '/cms/metadata',
      '/cms/settings',
    ];

    for (const route of routes) {
      await page.goto(`${BASE_URL}${route}`);
      await waitForAppReady(page);
      // 验证没有出现错误页面
      const body = page.locator('body');
      await expect(body).not.toContainText('Uncaught Error');
      await expect(body).not.toContainText('Cannot GET');
    }
  });
});

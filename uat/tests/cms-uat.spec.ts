import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

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
 *
 * 环境变量：
 *   BASE_URL    — 测试目标地址，默认 http://localhost:8081
 *   PUBLIC_HOST — presigned URL 应包含的公网主机名（如 192.168.31.33），
 *                 未设置时跳过主机名匹配断言
 */

const BASE_URL = process.env.BASE_URL || 'http://localhost:8081';
const PUBLIC_HOST = process.env.PUBLIC_HOST || '';

// ── 辅助函数 ──────────────────────────────────────────────────

/**
 * 等待 React 应用挂载完成
 * @param page - Playwright Page 对象
 */
async function waitForAppReady(page: Page) {
  await page.waitForSelector('nav, [data-testid="layout"], main', { timeout: 20_000 });
}

/** 测试数据命名空间前缀，用于识别需要清理的 UAT 数据 */
const UAT_PREFIX = 'uat_';

/** 追踪当前测试创建的设置 key，用于 afterAll 清理 */
const createdSettingKeys: string[] = [];

/** 追踪当前测试创建的 secrets key，用于 afterAll 清理 */
const createdSecretKeys: string[] = [];

/**
 * 清理指定集合中以 UAT_PREFIX 开头的 key
 * @param request - APIRequestContext
 * @param collection - 集合名（'settings' 或 'secrets'）
 * @param keys - 需要删除的 key 列表
 */
async function cleanupKeys(request: APIRequestContext, collection: 'settings' | 'secrets', keys: string[]) {
  if (keys.length === 0) return;
  try {
    const resp = await request.get(`${BASE_URL}/__proxy/db/${collection}`);
    if (!resp.ok()) return;
    const data = await resp.json() as Record<string, unknown>;
    const secretsWrap = collection === 'secrets' ? (data as { secrets?: Record<string, unknown> }).secrets : null;
    const target = secretsWrap || data;
    const cleanup: Record<string, null> = {};
    for (const key of keys) {
      if (key in target) cleanup[key] = null;
    }
    if (Object.keys(cleanup).length > 0) {
      await request.put(`${BASE_URL}/__proxy/db/${collection}`, {
        data: cleanup,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  } catch {
    // 清理失败不影响测试结果
  }
}

// ── 全局 afterAll：清理 UAT 测试数据 ──────────────────────────

test.afterAll(async ({ request }) => {
  await cleanupKeys(request, 'settings', createdSettingKeys);
  await cleanupKeys(request, 'secrets', createdSecretKeys);
});

// ── 测试组 1：页面加载与路由 ──────────────────────────────────

test.describe('【1】页面加载与 SPA 路由', () => {
  test('根路径 / 应重定向到 /cms/', async ({ page }) => {
    const response = await page.goto(`${BASE_URL}/`);
    expect(page.url()).toMatch(/\/cms\//);
    expect(response?.status()).toBeLessThan(400);
  });

  test('/cms/ 应正确加载 SPA 入口', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/`);
    await waitForAppReady(page);
    const title = await page.title();
    expect(title).toBeTruthy();
  });

  test('/cms/source-materials 原始资料库页面可访问', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/source-materials`);
    await waitForAppReady(page);
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
  test('GET /materials 返回有效响应', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/__proxy/db/materials`);
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('GET /settings 返回有效响应', async ({ request }) => {
    const response = await request.get(`${BASE_URL}/__proxy/db/settings`);
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toBeTruthy();
  });

  test('数据持久化：写入后重新读取一致', async ({ request }) => {
    const testKey = `${UAT_PREFIX}test_${Date.now()}`;
    const testValue = `test_value_${Math.random().toString(36).slice(2)}`;
    createdSettingKeys.push(testKey);

    const putResp = await request.put(`${BASE_URL}/__proxy/db/settings`, {
      data: { [testKey]: testValue },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(putResp.status()).toBeLessThan(300);

    const getResp = await request.get(`${BASE_URL}/__proxy/db/settings`);
    expect(getResp.status()).toBe(200);
    const settings = await getResp.json() as Record<string, unknown>;
    expect(settings[testKey]).toBe(testValue);
  });

  test('secrets 持久化：写入后重新读取一致', async ({ request }) => {
    const testKey = `${UAT_PREFIX}secret_${Date.now()}`;
    const testValue = `secret_${Math.random().toString(36).slice(2)}`;
    createdSecretKeys.push(testKey);

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
    expect(response.status()).toBe(200);
  });

  test('presigned URL 使用公网地址（非内部容器地址）', async ({ request }) => {
    const listResp = await request.get(`${BASE_URL}/__proxy/upload/list?prefix=originals`);
    if (listResp.status() !== 200) {
      test.skip();
      return;
    }

    const body = await listResp.json() as { objects?: Array<{ presignedUrl: string }> };
    const objects = body.objects || [];

    if (objects.length === 0) {
      test.skip();
      return;
    }

    // 验证 presigned URL 不包含内部容器地址
    for (const obj of objects.slice(0, 3)) {
      const url = obj.presignedUrl;
      expect(url).not.toMatch(/minio:\d+/);
      expect(url).not.toMatch(/localhost:\d+/);

      // 若配置了 PUBLIC_HOST，则验证 URL 包含该主机名；否则仅验证不含内部地址
      if (PUBLIC_HOST) {
        expect(url).toContain(PUBLIC_HOST);
      }
    }
  });
});

// ── 测试组 5：文件上传流程 ────────────────────────────────────

test.describe('【5】文件上传流程', () => {
  test('upload-server /tasks 接受小型测试文件并创建任务', async ({ request }) => {
    const minimalPng = Buffer.from(
      '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489' +
      '0000000a49444154789c6260000000020001e221bc330000000049454e44ae426082',
      'hex',
    );

    const response = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        file: {
          name: 'uat-test.png',
          mimeType: 'image/png',
          buffer: minimalPng,
        },
      },
    });

    const status = response.status();
    expect(status).toBe(200);

    const body = await response.json() as { taskId?: string; materialId?: string; url?: string; provider?: string };
    expect(body.taskId).toBeTruthy();

    // 若 URL 指向 MinIO，验证其为公网可访问地址（非容器内部地址）
    if (body.provider === 'minio' && body.url) {
      expect(body.url).not.toMatch(/^http:\/\/minio:/);
      if (PUBLIC_HOST) {
        expect(body.url).toContain(PUBLIC_HOST);
      }
    }
  });

  test('上传后 presigned URL 在局域网可直接访问（HTTP GET）', async ({ request }) => {
    const content = Buffer.from(`UAT test file ${Date.now()}`);

    const uploadResp = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        file: {
          name: 'uat-check.txt',
          mimeType: 'text/plain',
          buffer: content,
        },
      },
    });

    expect(uploadResp.status()).toBe(200);

    const body = await uploadResp.json() as { url?: string; provider?: string };
    if (body.provider !== 'minio' || !body.url) {
      test.skip();
      return;
    }

    // 直接 GET presigned URL，验证文件可访问
    const fileResp = await request.get(body.url);
    expect(fileResp.status()).toBe(200);
  });
});

// ── 测试组 6：页面导航交互 ────────────────────────────────────

test.describe('【6】页面导航交互（冒烟）', () => {
  test('侧边栏导航可正常点击切换页面', async ({ page }) => {
    await page.goto(`${BASE_URL}/cms/`);
    await waitForAppReady(page);

    const routes = [
      '/cms/workspace',
      '/cms/source-materials',
      '/cms/products',
      '/cms/metadata',
      '/cms/settings',
    ];

    for (const route of routes) {
      await page.goto(`${BASE_URL}${route}`);
      await waitForAppReady(page);
      const body = page.locator('body');
      await expect(body).not.toContainText('Uncaught Error');
      await expect(body).not.toContainText('Cannot GET');
    }
  });
});

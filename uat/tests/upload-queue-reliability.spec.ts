import { test, expect, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8081';
const TEST_PDF_DIR = process.env.TEST_PDF_DIR || path.resolve(process.cwd(), '..', 'testpdf');

test.describe.configure({ retries: 0 });

function listTestPdfs() {
  if (!fs.existsSync(TEST_PDF_DIR)) {
    throw new Error(
      [
        `TEST_PDF_DIR 不存在：${TEST_PDF_DIR}`,
        '请创建本地样本目录，例如：',
        `mkdir -p ${TEST_PDF_DIR}`,
        '并放入至少 10 个 PDF 文件用于回归测试。',
      ].join('\n'),
    );
  }

  const names = fs.readdirSync(TEST_PDF_DIR);
  return names
    .filter((n) => n.toLowerCase().endsWith('.pdf'))
    .filter((n) => n !== '.DS_Store' && !n.startsWith('._'))
    .map((n) => path.join(TEST_PDF_DIR, n))
    .sort((a, b) => a.localeCompare(b, 'zh-CN'));
}

async function resetBatchProcessingPersistence(request: APIRequestContext) {
  const empty = { items: [], running: false, paused: false, uiOpen: false };
  const resp = await request.put(`${BASE_URL}/__proxy/db/settings/batchProcessing`, { data: empty });
  if (!resp.ok()) {
    throw new Error(`reset batchProcessing failed: PUT ${BASE_URL}/__proxy/db/settings/batchProcessing HTTP ${resp.status()} ${await resp.text()}`);
  }
}

async function getDbSnapshot(request: APIRequestContext) {
  const [matsResp, tasksResp] = await Promise.all([
    request.get(`${BASE_URL}/__proxy/db/materials`),
    request.get(`${BASE_URL}/__proxy/db/tasks`),
  ]);
  expect(matsResp.ok()).toBe(true);
  expect(tasksResp.ok()).toBe(true);
  const mats = await matsResp.json();
  const tasks = await tasksResp.json();
  const materials = Array.isArray(mats) ? mats : [];
  const parseTasks = Array.isArray(tasks) ? tasks : [];
  return {
    materials,
    parseTasks,
  };
}

test.describe('【P0】上传队列可靠性与 aborted 可观测', () => {
  test('多轮提交 + abort + 重试：前端成功数与后端新增任务数一致', async ({ page, request }, testInfo) => {
    const runId = `upload-queue-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    testInfo.setTimeout(20 * 60 * 1000);

    await resetBatchProcessingPersistence(request);

    await page.addInitScript(() => {
      localStorage.removeItem('app_batch_processing');
      localStorage.removeItem('app_materials');
      localStorage.removeItem('app_tasks');
      localStorage.removeItem('app_process_tasks');
      localStorage.removeItem('app_asset_details');
    });

    const pdfs = listTestPdfs();
    if (pdfs.length < 10) {
      throw new Error(`TEST_PDF_DIR 至少需要 10 个 PDF，当前仅发现 ${pdfs.length} 个：${TEST_PDF_DIR}`);
    }
    const selected = pdfs.slice(0, 10);

    const before = await getDbSnapshot(request);
    const beforeTaskIds = new Set(before.parseTasks.map((t: any) => String(t?.id || '')));
    const beforeMaterialIds = new Set(before.materials.map((m: any) => String(m?.id || '')));

    let aborted = 0;
    await page.route('**/__proxy/upload/tasks', async (route) => {
      const r = route.request();
      if (r.method() === 'POST' && aborted < 2) {
        aborted += 1;
        await route.abort();
        return;
      }
      await route.continue();
    });

    await page.goto(`${BASE_URL}/cms/workspace`);

    const input = page.locator('input[type="file"]').first();
    // --- 三轮提交，总计 10 个文件 ---
    const round1 = selected.slice(0, 4);
    const round2 = selected.slice(4, 7);
    const round3 = selected.slice(7, 10);

    const uploadBtn = page.locator('button:has-text("上传文件")');
    const fileInput = page.locator('input[type="file"]').first();

    console.log(`[${runId}] Round 1: 4 files (via Workspace)`);
    await uploadBtn.click();
    await fileInput.setInputFiles(round1);

    const appendBtn = page.locator('button:has-text("继续添加文件")');
    const appendInput = page.getByTestId('batch-append-file-input');

    console.log(`[${runId}] Round 2: 3 files (via Modal)`);
    await expect(appendBtn).toBeVisible({ timeout: 10000 });
    await appendBtn.click();
    await appendInput.setInputFiles(round2);

    console.log(`[${runId}] Round 3: 3 files (via Modal)`);
    await appendBtn.click();
    await appendInput.setInputFiles(round3);

    const dumpQueue = async (label: string) => {
      const raw = await page.evaluate(() => localStorage.getItem('app_batch_processing'));
      const data = raw ? JSON.parse(raw) : { items: [] };
      console.log(`[${runId}] [DUMP] ${label}:`, JSON.stringify(data, null, 2));
      return data;
    };

    // P0: 三轮提交后立即校验队列长度，不得丢项
    console.log(`[${runId}] Validating queue length (expect 10)...`);
    let queueReady = false;
    for (let i = 0; i < 10; i++) {
      const data = await dumpQueue(`CHECK_LEN_${i}`);
      if (data.items.length === 10) {
        queueReady = true;
        break;
      }
      await page.waitForTimeout(1000);
    }

    if (!queueReady) {
      console.error(`[${runId}] ERROR: Queue length mismatch. Selected 10 files but queue only has fewer.`);
      await dumpQueue('FINAL_LENGTH_FAILURE');
      throw new Error('Queue dropping items detected after 3 rounds of upload');
    }

    // P0: 改为显式轮询，避免 Playwright 内部 timeout 误截断
    const waitForQueueStable = async (timeoutMs = 15 * 60 * 1000, expectAllTerminal = true) => {
      const start = Date.now();
      const deadline = start + timeoutMs;
      // 终端状态定义：完成了或者明确失败了（包含 error，即上传阶段失败）
      const terminal = new Set(['completed', 'review-pending', 'failed', 'canceled', 'error', 'skipped']);

      while (Date.now() < deadline) {
        const data = await page.evaluate(() => {
          const raw = localStorage.getItem('app_batch_processing');
          return raw ? JSON.parse(raw) : null;
        });

        if (!data || !Array.isArray(data.items)) {
          await page.waitForTimeout(2000);
          continue;
        }

        const items = data.items;
        // active 定义：既不在终端状态，也不是 tracking (正在后端解析中)
        const active = items.filter((it: any) => {
          const s = String(it?.status);
          if (terminal.has(s)) return false;
          if (s === 'tracking' || s === 'task-created') return false;
          return true;
        });
        
        const doneOrTrackingCount = items.length - active.length;
        
        console.log(`[${runId}] Queue progress: ${doneOrTrackingCount}/${items.length} (Active: ${active.length}), Elapsed: ${Math.round((Date.now() - start) / 1000)}s`);

        // 如果不要求全量终态，只要所有项都进入了 tracking 或 terminal 即可
        if (items.length >= 10 && active.length === 0) {
          if (!expectAllTerminal) {
            console.log(`[${runId}] Queue is tracking/terminal.`);
            return data;
          }
          // 如果要求全量终态，则需要检查是否还有 tracking
          const tracking = items.filter((it: any) => it.status === 'tracking' || it.status === 'task-created');
          if (tracking.length === 0) {
            console.log(`[${runId}] Queue stabilized (All Terminal).`);
            return data;
          }
        }

        await page.waitForTimeout(5000);
      }

      await dumpQueue('STABILITY_TIMEOUT');
      throw new Error(`Queue did not reach target state within ${timeoutMs / 1000}s`);
    };

    // 第一阶段：等待所有任务提交完毕（可能是 tracking、terminal 或 error）
    await waitForQueueStable(5 * 60 * 1000, false);

    // 第二阶段：处理 aborted 导致的 error 项，进行重试
    console.log(`[${runId}] Checking for retryable errors...`);
    const retryButtons = page.locator('button[title="重试上传"]');
    let retryCount = await retryButtons.count();
    console.log(`[${runId}] Found ${retryCount} retryable items.`);
    
    // 预期至少有 2 个被 abort 的项
    expect(retryCount).toBeGreaterThanOrEqual(2);

    while (retryCount > 0) {
      console.log(`[${runId}] Clicking retry button (Remaining: ${retryCount})...`);
      await retryButtons.first().click();
      await page.waitForTimeout(1000); // 等待状态切换
      retryCount = await retryButtons.count();
    }

    // 第三阶段：等待重试的任务也进入 tracking 或 terminal
    console.log(`[${runId}] Waiting for retried items to enter tracking/terminal...`);
    await waitForQueueStable(5 * 60 * 1000, false);

    const queue = await page.evaluate(() => {
      const raw = localStorage.getItem('app_batch_processing');
      return raw ? JSON.parse(raw) : null;
    });
    const items = Array.isArray(queue?.items) ? queue.items : [];
    expect(items.length).toBe(10);

    const created = items.filter((it: any) => Boolean(String(it?.taskId || '').trim())).length;
    const tracking = items.filter((it: any) => String(it?.status) === 'tracking' || String(it?.status) === 'task-created').length;
    const terminalCount = items.filter((it: any) => 
      ['completed', 'review-pending', 'failed', 'canceled', 'error', 'skipped'].includes(String(it?.status))
    ).length;

    const after = await getDbSnapshot(request);
    const newTasks = after.parseTasks.filter((t: any) => !beforeTaskIds.has(String(t?.id || '')));
    const newMaterials = after.materials.filter((m: any) => !beforeMaterialIds.has(String(m?.id || '')));

    const printAudit = () => {
      console.log(`[${runId}] Summary: newTasks=${newTasks.length} newMaterials=${newMaterials.length}`);
      console.log(`[${runId}] Queue stats: created=${created} tracking=${tracking} terminal=${terminalCount}`);
    };

    try {
      // 核心断言：最终 10 个文件必须都成功创建了 Task 和 Material，且都有 objectName
      expect(newTasks.length).toBe(10);
      expect(newMaterials.length).toBe(10);
      expect(created).toBe(10);
      
      for (const item of items) {
        expect(item.taskId).toBeTruthy();
        expect(item.materialId).toBeTruthy();
        expect(item.objectName).toBeTruthy();
        // 不得停留在 error 状态
        expect(item.status).not.toBe('error');
      }
      
      for (const m of newMaterials) {
        expect(m?.metadata?.objectName).toBeTruthy();
      }
    } catch (err) {
      await dumpQueue('ASSERTION_FAILURE');
      printAudit();
      throw err;
    }

    printAudit();
  });
});


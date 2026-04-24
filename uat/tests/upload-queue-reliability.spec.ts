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

    console.log(`[${runId}] Round 1: 4 files`);
    await uploadBtn.click();
    await fileInput.setInputFiles(round1);

    console.log(`[${runId}] Round 2: 3 files`);
    await uploadBtn.click();
    await fileInput.setInputFiles(round2);

    console.log(`[${runId}] Round 3: 3 files`);
    await uploadBtn.click();
    await fileInput.setInputFiles(round3);

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
    const waitForQueueStable = async (timeoutMs = 15 * 60 * 1000) => {
      const start = Date.now();
      const deadline = start + timeoutMs;
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
        const active = items.filter((it: any) => !terminal.has(String(it?.status)));
        const doneCount = items.length - active.length;
        
        console.log(`[${runId}] Queue progress: ${doneCount}/${items.length} (Active: ${active.length}), Elapsed: ${Math.round((Date.now() - start) / 1000)}s`);

        if (items.length >= 10 && active.length === 0) {
          console.log(`[${runId}] Queue stabilized.`);
          return data;
        }

        await page.waitForTimeout(3000);
      }

      await dumpQueue('STABILITY_TIMEOUT');
      throw new Error(`Queue did not become stable within ${timeoutMs / 1000}s`);
    };

    await waitForQueueStable();

    const retryButtons = page.locator('button[title="重试上传"]');
    let retryCount = await retryButtons.count();
    expect(retryCount).toBeGreaterThanOrEqual(1);
    while (retryCount > 0) {
      await retryButtons.first().click();
      await page.waitForTimeout(200);
      retryCount = await retryButtons.count();
    }

    await page.waitForTimeout(300);
    await waitForQueueStable();

    const queue = await page.evaluate(() => {
      const raw = localStorage.getItem('app_batch_processing');
      return raw ? JSON.parse(raw) : null;
    });
    const items = Array.isArray(queue?.items) ? queue.items : [];
    expect(items.length).toBe(10);

    const created = items.filter((it: any) => Boolean(String(it?.taskId || '').trim())).length;
    const completed = items.filter((it: any) => String(it?.status) === 'completed').length;
    const reviewPending = items.filter((it: any) => String(it?.status) === 'review-pending').length;
    const failed = items.filter((it: any) => String(it?.status) === 'failed').length;
    const canceled = items.filter((it: any) => String(it?.status) === 'canceled').length;
    const uploadFailed = items.filter((it: any) => String(it?.status) === 'error').length;
    const skipped = items.filter((it: any) => String(it?.status) === 'skipped').length;
    const terminalCount = completed + reviewPending + failed + canceled + uploadFailed + skipped;
    const after = await getDbSnapshot(request);
    const newTasks = after.parseTasks.filter((t: any) => !beforeTaskIds.has(String(t?.id || '')));
    const newMaterials = after.materials.filter((m: any) => !beforeMaterialIds.has(String(m?.id || '')));

    const newTasksSummary = newTasks.map((t: any) => ({
      id: t?.id,
      materialId: t?.materialId,
      state: t?.state,
      stage: t?.stage,
      progress: t?.progress,
      message: t?.message,
      createdAt: t?.createdAt,
    }));
    const newMaterialsSummary = newMaterials.map((m: any) => ({
      id: m?.id,
      title: m?.title,
      fileName: m?.fileName,
      status: m?.status,
      mineruStatus: m?.mineruStatus,
      aiStatus: m?.aiStatus,
      objectName: m?.metadata?.objectName,
    }));

    const printAudit = () => {
      console.log(`[${runId}] newTasks=${newTasks.length} newMaterials=${newMaterials.length}`);
      console.log(`[${runId}] queue: created=${created} completed=${completed} reviewPending=${reviewPending} failed=${failed} canceled=${canceled} uploadFailed=${uploadFailed} skipped=${skipped} terminal=${terminalCount}`);
      console.log(`[${runId}] newTasks: ${JSON.stringify(newTasksSummary, null, 2)}`);
      console.log(`[${runId}] newMaterials: ${JSON.stringify(newMaterialsSummary, null, 2)}`);
    };

    try {
      expect(newTasks.length).toBe(10);
      expect(newMaterials.length).toBe(10);
      expect(created).toBe(10);
      expect(uploadFailed).toBe(0);
      expect(skipped).toBe(0);
      expect(terminalCount).toBe(10);
      expect(completed + reviewPending + failed + canceled).toBe(10);
      const failedItems = items.filter((it: any) => ['failed', 'canceled'].includes(String(it?.status)));
      for (const it of failedItems) {
        expect(String(it?.message || '').trim().length).toBeGreaterThan(0);
      }
      for (const m of newMaterials) {
        expect(String(m?.metadata?.objectName || '').trim().length).toBeGreaterThan(0);
      }
      const materialIdSet = new Set(newMaterials.map((m: any) => String(m?.id || '')));
      const taskMaterialIdSet = new Set(newTasks.map((t: any) => String(t?.materialId || '')));
      expect(materialIdSet.size).toBe(10);
      expect(taskMaterialIdSet.size).toBe(10);
      for (const id of materialIdSet) {
        expect(taskMaterialIdSet.has(id)).toBe(true);
      }
    } catch (err) {
      await dumpQueue('ASSERTION_FAILURE');
      printAudit();
      throw err;
    }

    printAudit();
  });
});

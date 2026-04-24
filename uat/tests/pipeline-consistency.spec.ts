import { test, expect } from '@playwright/test';
import { PDFDocument, rgb } from 'pdf-lib';
import JSZip from 'jszip';

/**
 * pipeline-consistency.spec.ts - 核心处理链路一致性测试
 * 
 * 验证：
 * 1. PDF 链路：上传 -> processing -> (MinerU) -> (AI) -> completed/reviewing
 * 2. Markdown 链路：上传 -> processing -> (Skip MinerU) -> (AI) -> completed/reviewing
 * 3. 状态一致性：Material.status 与 Task.state 的映射关系
 */

const BASE_URL = process.env.BASE_URL || 'http://192.168.31.33:8081';

test.describe('【7】处理链路与状态一致性', () => {
  // 设置该测试组的整体超时时间为 3 分钟，防止本地 MinerU/AI 处理较慢导致失败
  test.setTimeout(180_000);

  test('PDF 链路一致性：上传后状态流转验证', async ({ request }) => {
    // 1. 使用 pdf-lib 生成一个有效的小型单页 PDF
    const pdfDoc = await PDFDocument.create();
    const page = pdfDoc.addPage([600, 400]);
    page.drawText('UAT Consistency Test PDF Content', { x: 50, y: 350, size: 20, color: rgb(0, 0, 0) });
    const pdfBytes = await pdfDoc.save();
    const pdfBuffer = Buffer.from(pdfBytes);

    const materialId = `uat-pdf-${Date.now()}`;
    
    const uploadResp = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        file: {
          name: 'uat-consistency.pdf',
          mimeType: 'application/pdf',
          buffer: pdfBuffer,
        },
        materialId
      },
    });

    expect(uploadResp.status()).toBe(200);
    const { taskId } = await uploadResp.json();
    expect(taskId).toBeTruthy();

    // 2. 初始状态检查 (增强断言：考虑 Worker 拾取速度，允许 pending 或更高状态，但不能为 undefined)
    const matResp = await request.get(`${BASE_URL}/__proxy/db/materials/${materialId}`);
    const mat = await matResp.json();
    expect(mat.status).toBe('processing');
    
    expect(['pending', 'processing', 'completed']).toContain(mat.mineruStatus);
    expect(['pending', 'analyzing', 'analyzed']).toContain(mat.aiStatus);
    expect(mat.mineruStatus).not.toBeUndefined();
    expect(mat.aiStatus).not.toBeUndefined();

    // 3. 轮询等待任务到达终态 (最多等待 120s: 24 * 5s)
    let finalTaskState = '';
    for (let i = 0; i < 24; i++) {
      const tResp = await request.get(`${BASE_URL}/__proxy/db/tasks/${taskId}`);
      const task = await tResp.json();
      finalTaskState = task.state;
      if (['completed', 'review-pending', 'failed'].includes(task.state)) break;
      await new Promise(r => setTimeout(r, 5000));
    }

    console.log(`  PDF Task finished with state: ${finalTaskState}`);

    // 3.1 解析产物入库验证：parsed/{materialId}/ 下对象数量必须 > 1（至少包含 canonical full.md 与 MinerU 原始结果载体）
    const prefix = `parsed/${materialId}/`;
    const listResp = await request.get(`${BASE_URL}/__proxy/upload/list?prefix=${encodeURIComponent(prefix)}`);
    expect(listResp.status()).toBe(200);
    const listPayload = await listResp.json();
    expect(listPayload.total).toBeGreaterThan(1);
    const objectNames = (listPayload.objects || []).map((o: { objectName: string }) => o.objectName);
    expect(objectNames).toContain(`${prefix}full.md`);
    const hasMineruZip = objectNames.includes(`${prefix}mineru-result.zip`);
    const hasMineruJson = objectNames.includes(`${prefix}mineru-result.json`);
    expect(hasMineruZip || hasMineruJson).toBeTruthy();

    // 3.2 /parsed-zip 打包一致性：ZIP 内文件数应与对象列表一致，且保留相对路径
    const zipResp = await request.post(`${BASE_URL}/__proxy/upload/parsed-zip`, { data: { materialId } });
    expect(zipResp.status()).toBe(200);
    const zipBuffer = await zipResp.body();
    const zip = await JSZip.loadAsync(zipBuffer);
    const zipFileNames = Object.values(zip.files).filter((f) => !f.dir).map((f) => f.name).sort();
    const listedRelativePaths = objectNames
      .map((name: string) => (name.startsWith(prefix) ? name.slice(prefix.length) : name))
      .sort();
    expect(zipFileNames.length).toBe(listPayload.total);
    expect(zipFileNames.sort()).toEqual(listedRelativePaths.sort());
    expect(zipFileNames).toContain('full.md');
    
    // 3.3 深度校验 MinerU 原始产物完整性 (P0 Patch 验证)
    if (hasMineruZip) {
      const mineruZipObj = zip.file('mineru-result.zip');
      expect(mineruZipObj).toBeTruthy();
      const mineruZipBuffer = await mineruZipObj.async('nodebuffer');
      const mineruZip = await JSZip.loadAsync(mineruZipBuffer);
      
      // MinerU 原始 ZIP 内所有非目录文件
      const mineruZipFiles = Object.values(mineruZip.files).filter((f) => !f.dir).map((f) => f.name);
      
      // 断言: mineruZipFiles ⊆ zipFileNames
      for (const name of mineruZipFiles) {
        // 由于 local-adapter 存入 MinIO 时会 sanitizeRelativePath，确保这里比对逻辑一致
        const safeName = name.replace(/\\/g, '/').replace(/^\/+/, '').replace(/\.\.\//g, '');
        if (safeName && safeName !== 'mineru-result.zip' && safeName !== 'mineru-result.json' && safeName !== 'full.md') {
          expect(zipFileNames).toContain(safeName);
        }
      }

      // 如果 MinerU 真实产出了以下辅助文件，验证它们被成功入库导出
      const artifactsToCheck = ['_middle.json', '_model.json', '_content_list.json', '_content_list_v2.json', '_origin.pdf'];
      for (const checkItem of artifactsToCheck) {
        const hasItemInMineru = mineruZipFiles.some((f) => f.toLowerCase().endsWith(checkItem));
        if (hasItemInMineru) {
          expect(zipFileNames.some((f) => f.toLowerCase().endsWith(checkItem))).toBeTruthy();
        }
      }
    }

    // 4. 验证 Material 状态一致性
    const matFinalResp = await request.get(`${BASE_URL}/__proxy/db/materials/${materialId}`);
    const matFinal = await matFinalResp.json();

    if (finalTaskState === 'completed') {
      expect(matFinal.status).toBe('completed');
    } else if (finalTaskState === 'review-pending') {
      // 验证 Task 3 的修复：review-pending 映射到 reviewing
      expect(matFinal.status).toBe('reviewing');
    }

    // 5. metadata 可追踪 parsed 产物清单
    const taskFinalResp = await request.get(`${BASE_URL}/__proxy/db/tasks/${taskId}`);
    expect(taskFinalResp.status()).toBe(200);
    const taskFinal = await taskFinalResp.json();
    expect(taskFinal.metadata?.markdownObjectName).toBe(`${prefix}full.md`);
    expect(taskFinal.metadata?.parsedPrefix).toBe(prefix);
    expect(taskFinal.metadata?.parsedFilesCount).toBe(listPayload.total);
    expect(Array.isArray(taskFinal.metadata?.parsedArtifacts)).toBeTruthy();
    expect(taskFinal.metadata?.parsedArtifacts?.length).toBe(listPayload.total);

    expect(matFinal.metadata?.markdownObjectName).toBe(`${prefix}full.md`);
    expect(matFinal.metadata?.parsedPrefix).toBe(prefix);
    expect(matFinal.metadata?.parsedFilesCount).toBe(listPayload.total);
    expect(Array.isArray(matFinal.metadata?.parsedArtifacts)).toBeTruthy();
    expect(matFinal.metadata?.parsedArtifacts?.length).toBe(listPayload.total);
  });

  test('Markdown 链路一致性：跳过解析验证', async ({ request }) => {
    // 1. 上传 MD
    const mdContent = Buffer.from('# UAT Markdown\nThis is a test.');
    const materialId = `uat-md-${Date.now()}`;
    
    const uploadResp = await request.post(`${BASE_URL}/__proxy/upload/tasks`, {
      multipart: {
        file: {
          name: 'uat-consistency.md',
          mimeType: 'text/markdown',
          buffer: mdContent,
        },
        materialId
      },
    });

    expect(uploadResp.status()).toBe(200);
    const { taskId } = await uploadResp.json();

    // 2. 轮询等待 (最多等待 90s: 30 * 3s)
    let finalTaskState = '';
    for (let i = 0; i < 30; i++) {
      const tResp = await request.get(`${BASE_URL}/__proxy/db/tasks/${taskId}`);
      const task = await tResp.json();
      finalTaskState = task.state;
      if (['completed', 'review-pending', 'failed'].includes(task.state)) break;
      await new Promise(r => setTimeout(r, 3000));
    }

    // 3. 验证
    const matFinalResp = await request.get(`${BASE_URL}/__proxy/db/materials/${materialId}`);
    const matFinal = await matFinalResp.json();

    if (finalTaskState === 'completed') {
      expect(matFinal.status).toBe('completed');
    } else if (finalTaskState === 'review-pending') {
      expect(matFinal.status).toBe('reviewing');
    }
    
    // Markdown 应该直接标记为分析完成（跳过 MinerU）
    expect(matFinal.mineruStatus).toBe('completed');
  });

});

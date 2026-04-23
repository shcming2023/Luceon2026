import { test, expect } from '@playwright/test';
import { PDFDocument, rgb } from 'pdf-lib';

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

    // 4. 验证 Material 状态一致性
    const matFinalResp = await request.get(`${BASE_URL}/__proxy/db/materials/${materialId}`);
    const matFinal = await matFinalResp.json();

    if (finalTaskState === 'completed') {
      expect(matFinal.status).toBe('completed');
    } else if (finalTaskState === 'review-pending') {
      // 验证 Task 3 的修复：review-pending 映射到 reviewing
      expect(matFinal.status).toBe('reviewing');
    }
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

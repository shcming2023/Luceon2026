import http from 'http';
import { processWithLocalMinerU } from '../services/mineru/local-adapter.mjs';

// 模拟 global fetch
function setupFetchMock() {
  let callCount = 0;
  const originalFetch = globalThis.fetch;
  
  globalThis.fetch = async (url, options) => {
    const urlStr = String(url);
    if (urlStr.endsWith('/health')) {
      return new Response('ok', { status: 200 });
    } else if (urlStr.endsWith('/tasks') && options?.method === 'POST') {
      return new Response(JSON.stringify({ task_id: 'mock-task-123' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    } else if (urlStr.includes('/tasks/mock-task-123/result')) {
      return new Response(JSON.stringify({
        md_content: '# Test Markdown\n\nDone.',
        artifacts: []
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    } else if (urlStr.includes('/tasks/mock-task-123')) {
      callCount++;
      if (callCount === 1) {
        return new Response(JSON.stringify({ status: 'pending', queued_ahead: 2, started_at: null }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      } else if (callCount === 2) {
        return new Response(JSON.stringify({ status: 'processing', started_at: new Date().toISOString(), queued_ahead: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      } else {
        return new Response(JSON.stringify({ status: 'done' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    }
    
    // 如果不是我们 mock 的 URL，可以回退或抛错
    throw new Error(`Unhandled mock fetch: ${urlStr}`);
  };

  return () => {
    globalThis.fetch = originalFetch;
  };
}

// 模拟流
async function* mockStream() {
  yield Buffer.from('mock pdf content');
}

async function main() {
  console.log('=== MinerU Queue Status Smoke Test ===');
  const restoreFetch = setupFetchMock();
  const endpoint = 'http://mock-mineru-internal:56789';
  console.log(`Mock Fetch installed. Virtual endpoint: ${endpoint}`);

  const task = {
    id: 'cms-task-001',
    materialId: 'mat-001',
    optionsSnapshot: {
      localEndpoint: endpoint,
      backend: 'pipeline',
      maxPages: 10
    },
    metadata: {}
  };

  const materialInfo = {
    fileSize: 1024,
    fileName: 'test.pdf',
    mimeType: 'application/pdf',
    metadata: {}
  };

  const minioContext = {
    saveMarkdown: async () => true,
    saveObject: async () => true,
  };

  const history = [];

  try {
    await processWithLocalMinerU({
      task,
      material: materialInfo,
      fileStream: mockStream(),
      fileName: 'test.pdf',
      mimeType: 'application/pdf',
      timeoutMs: 30000,
      minioContext,
      updateProgress: async (updateInfo) => {
        console.log(`[Update] stage=${updateInfo.stage || 'N/A'}, status=${updateInfo.metadata?.mineruStatus || 'N/A'}, msg=${updateInfo.message}`);
        history.push(updateInfo);
      }
    });

    // 验证
    const submitted = history.find(h => h.metadata?.mineruStatus === 'submitted');
    const queued = history.find(h => h.metadata?.mineruStatus === 'queued' && h.stage === 'mineru-queued');
    const processing = history.find(h => h.metadata?.mineruStatus === 'processing' && h.stage === 'mineru-processing');

    if (!submitted || submitted.metadata.mineruTaskId !== 'mock-task-123') {
      throw new Error('提交失败，未能正确记录 mineruTaskId 和 submitted 状态');
    }
    if (!queued || queued.metadata.mineruQueuedAhead !== 2) {
      throw new Error('未能正确识别和记录 MinerU queued 状态及排队数量');
    }
    if (!processing || processing.metadata.mineruStartedAt == null) {
      throw new Error('未能正确识别和记录 MinerU processing 状态');
    }

    console.log('✅ 队列状态对账语义验证通过！');
    restoreFetch();
    process.exit(0);
  } catch (err) {
    console.error('❌ 测试失败:', err);
    restoreFetch();
    process.exit(1);
  }
}

main();

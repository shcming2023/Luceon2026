import { parseLatestMineruProgress } from '../server/lib/ops-mineru-log-parser.mjs';

const UPLOAD_SERVER_URL = process.env.UPLOAD_SERVER_URL || 'http://localhost:8788';
const INTERVAL = Number(process.env.OBSERVE_INTERVAL_MS) || 2000;

async function observeLoop() {
  console.log(`[host-observer] Starting host log observer... (interval: ${INTERVAL}ms, upload-server: ${UPLOAD_SERVER_URL})`);

  while (true) {
    try {
      // 获取当前 active task 上下文，以便精准过滤
      let minObservedAt = null;
      let previousObservation = null;
      let executionProfile = null;

      try {
        const res = await fetch(`${UPLOAD_SERVER_URL}/ops/mineru/active-task`, { signal: AbortSignal.timeout(1000) });
        if (res.ok) {
          const taskData = await res.json();
          if (taskData.activeTask) {
             minObservedAt = taskData.activeTask.metadata?.mineruStartedAt || taskData.activeTask.createdAt;
             previousObservation = taskData.activeTask.metadata?.mineruObservedProgress || null;
             executionProfile = taskData.activeTask.metadata?.mineruExecutionProfile || null;
          }
        }
      } catch (err) {
        // 忽略 fetch active task 的错误，继续解析
      }

      const snapshot = await parseLatestMineruProgress(minObservedAt, previousObservation, executionProfile);

      if (snapshot) {
        const payload = {
          observer: 'host-mineru-log-observer',
          ...snapshot
        };

        const postRes = await fetch(`${UPLOAD_SERVER_URL}/ops/mineru-log-observation`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: AbortSignal.timeout(2000)
        });

        if (!postRes.ok) {
           console.warn(`[host-observer] POST failed: HTTP ${postRes.status}`);
        }
      }
    } catch (e) {
      console.error('[host-observer] Loop error:', e.message);
    }
    
    await new Promise(resolve => setTimeout(resolve, INTERVAL));
  }
}

observeLoop();

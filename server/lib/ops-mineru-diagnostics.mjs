import { parseLatestMineruProgress } from './ops-mineru-log-parser.mjs';

export function registerMineruDiagnosticsRoutes(app, getDbBaseUrl) {
  app.get('/ops/mineru/diagnostics', async (req, res) => {
    const dbBaseUrl = getDbBaseUrl();
    
    let localEndpoint = process.env.LOCAL_MINERU_ENDPOINT || 'http://host.docker.internal:8083';
    try {
      const setResp = await fetch(`${dbBaseUrl}/settings`, { signal: AbortSignal.timeout(1000) });
      if (setResp.ok) {
        const settings = await setResp.json();
        if (settings?.mineruConfig?.localEndpoint) localEndpoint = settings.mineruConfig.localEndpoint;
      }
    } catch (ee) { /* ignore */ }

    if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
      localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
    }
    localEndpoint = localEndpoint.replace(/\/+$/, '');

    const result = {
      ok: true,
      mineru: { endpoint: localEndpoint, healthy: false, processingTasks: 0, queuedTasks: 0, maxConcurrentRequests: 1 },
      luceon: { activeTasks: [], knownMineruTaskIds: [], mineruQueuedTasks: [], mineruProcessingTasks: [] },
      diagnosis: { status: 'unknown', kind: 'unknown', message: '', blockingMineruTaskId: null, safeToAutoRecover: false },
      logObservation: null
    };

    // logObservation will be populated later if there is an active processing task

    // 1. Fetch MinerU health
    try {
      const healthRes = await fetch(`${localEndpoint}/health`, { signal: AbortSignal.timeout(3000) });
      if (healthRes.ok) {
        const data = await healthRes.json();
        result.mineru.healthy = true;
        result.mineru.processingTasks = data.processing_tasks || 0;
        result.mineru.queuedTasks = data.queued_tasks || 0;
        result.mineru.maxConcurrentRequests = data.max_concurrent_requests || 1;
      } else {
        result.diagnosis.status = 'unreachable';
        result.diagnosis.message = `MinerU HTTP ${healthRes.status}`;
        return res.json(result);
      }
    } catch (e) {
      result.diagnosis.status = 'unreachable';
      result.diagnosis.message = `MinerU 连接失败: ${e.message}`;
      return res.json(result);
    }

    // 2. Fetch Luceon Tasks
    try {
      const tasksRes = await fetch(`${dbBaseUrl}/tasks`, { signal: AbortSignal.timeout(3000) });
      if (tasksRes.ok) {
        const tasks = await tasksRes.json();
        const activeStates = ['pending', 'running', 'result-store', 'ai-pending', 'ai-running'];
        
        result.luceon.activeTasks = tasks.filter(t => activeStates.includes(t.state)).map(t => t.id);
        result.luceon.mineruQueuedTasks = tasks.filter(t => t.stage === 'mineru-queued').map(t => t.id);
        result.luceon.mineruProcessingTasks = tasks.filter(t => t.stage === 'mineru-processing').map(t => t.id);
        
        if (result.luceon.mineruProcessingTasks.length > 0) {
          const targetTaskId = result.luceon.mineruProcessingTasks[0];
          const targetTask = tasks.find(t => t.id === targetTaskId);
          if (targetTask) {
            const minObservedAt = targetTask.metadata?.mineruStartedAt || targetTask.updatedAt || targetTask.createdAt;
            result.logObservation = await parseLatestMineruProgress(minObservedAt).catch(() => null);
          }
        }
        
        // Find all known MinerU task IDs
        const knownIds = new Set();
        for (const t of tasks) {
          if (t.metadata?.mineruTaskId) knownIds.add(t.metadata.mineruTaskId);
        }
        result.luceon.knownMineruTaskIds = Array.from(knownIds);
      }
      } catch (e) {
      // If DB fails, we can't fully diagnose
      result.diagnosis.status = 'error';
      result.diagnosis.message = '无法连接到 db-server 获取 Luceon 任务';
      return res.json(result);
    }
    // 3. Diagnosis Logic
    if (result.mineru.processingTasks === 0) {
      result.diagnosis.status = 'healthy';
      result.diagnosis.kind = 'idle';
      result.diagnosis.message = 'MinerU 当前空闲';
      return res.json(result);
    }

    // processingTasks > 0
    let actualProcessingMineruTaskId = null;
    let foundLuceonTaskProcessing = result.luceon.mineruProcessingTasks.length > 0;

    // Deep check known MinerU tasks if MinerU says it's processing
    if (!foundLuceonTaskProcessing) {
      for (const mTaskId of result.luceon.knownMineruTaskIds) {
        try {
          const tRes = await fetch(`${localEndpoint}/tasks/${mTaskId}`, { signal: AbortSignal.timeout(1000) });
          if (tRes.ok) {
            const tData = await tRes.json();
            if (tData.status === 'processing' || tData.started_at && !['done', 'success', 'completed', 'succeeded', 'failed'].includes(tData.status)) {
              actualProcessingMineruTaskId = mTaskId;
              break;
            }
          }
        } catch (e) { /* ignore */ }
      }
    }

    if (foundLuceonTaskProcessing || actualProcessingMineruTaskId) {
      result.diagnosis.status = 'busy';
      result.diagnosis.kind = 'luceon-processing';
      result.diagnosis.message = 'MinerU 正被 Luceon 已知任务占用';
      result.diagnosis.blockingMineruTaskId = actualProcessingMineruTaskId || 'known-luceon-task';
    } else {
      result.diagnosis.status = 'blocked';
      result.diagnosis.kind = 'orphan-processing-blocker';
      result.diagnosis.message = 'MinerU 当前被未知任务占用，Luceon 队列暂停推进';
      result.diagnosis.blockingMineruTaskId = 'unknown (MinerU API 当前不提供任务列表)';
      result.diagnosis.safeToAutoRecover = false;
    }

    res.json(result);
  });

  app.post('/ops/mineru/recover', async (req, res) => {
    const isDryRun = req.body?.dryRun !== false; // default true
    const confirm = req.body?.confirm === true;
    
    if (isDryRun || !confirm) {
      return res.json({
        ok: true,
        dryRun: true,
        wouldRestartMineru: true,
        reason: "orphan-processing-blocker",
        instructions: [
          "停止 mineru_api tmux session",
          "重新启动 conda mineru-api",
          "运行 node server/tests/mineru-deep-check.mjs",
          "确认 queued tasks 继续推进"
        ]
      });
    }

    // For safety, this task doesn't require actual restart implementation if dry-run is provided
    return res.json({
      ok: false,
      error: '服务端直接重启尚未实现，请参考 dryRun 提供的 instructions 手动清障。'
    });
  });
}

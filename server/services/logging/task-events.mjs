/**
 * task-events.mjs - 任务事件记录服务
 * 
 * 负责将任务状态变更等关键信息写入 db-server 的 taskEvents 集合。
 */

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';

export async function logTaskEvent({ taskId, taskType = 'parse', level = 'info', event, message, payload = {} }) {
  const eventId = `evt-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
  
  // P0 Patch 2: payload 瘦身防御
  let slimPayload = { ...payload };
  if (slimPayload.parsedArtifacts) delete slimPayload.parsedArtifacts;
  if (slimPayload.metadata?.parsedArtifacts) delete slimPayload.metadata.parsedArtifacts;
  
  // 如果 payload 携带了完整的 metadata 实体，提取摘要字段并丢弃大实体
  if (slimPayload.metadata) {
    const meta = slimPayload.metadata;
    slimPayload.parsedFilesCount = slimPayload.parsedFilesCount || meta.parsedFilesCount;
    slimPayload.parsedPrefix = slimPayload.parsedPrefix || meta.parsedPrefix;
    slimPayload.artifactManifestObjectName = slimPayload.artifactManifestObjectName || meta.artifactManifestObjectName;
    slimPayload.mineruTaskId = slimPayload.mineruTaskId || meta.mineruTaskId;
    delete slimPayload.metadata;
  }

  // 大小保护：单条 payload 超过 2KB (2048 characters) 即触发裁剪
  let payloadStr = JSON.stringify(slimPayload);
  let payloadTruncated = false;
  if (payloadStr.length > 2048) {
    slimPayload = {
      state: slimPayload.state,
      stage: slimPayload.stage,
      progress: slimPayload.progress,
      message: typeof slimPayload.message === 'string' ? slimPayload.message.substring(0, 500) : undefined,
      parsedFilesCount: slimPayload.parsedFilesCount,
      parsedPrefix: slimPayload.parsedPrefix,
      artifactManifestObjectName: slimPayload.artifactManifestObjectName,
      mineruTaskId: slimPayload.mineruTaskId,
      error: typeof slimPayload.error === 'string' ? slimPayload.error.substring(0, 500) : undefined,
      errorMessage: typeof slimPayload.errorMessage === 'string' ? slimPayload.errorMessage.substring(0, 500) : undefined,
      originalEvent: event
    };
    payloadTruncated = true;
  }

  const eventData = {
    id: eventId,
    taskId,
    taskType,
    level,
    event,
    message,
    payload: slimPayload,
    payloadTruncated,
    createdAt: new Date().toISOString()
  };

  try {
    const resp = await fetch(`${DB_BASE_URL}/task-events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(eventData)
    });

    if (!resp.ok) {
      console.error(`[task-events] Failed to log event to db-server: HTTP ${resp.status}`);
    }
  } catch (error) {
    // 约束 4: 写入失败不能导致 worker 崩溃，但必须记录服务端日志
    console.error(`[task-events] Network error logging event: ${error.message}`);
  }
}

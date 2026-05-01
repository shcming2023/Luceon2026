/**
 * task-client.mjs - 任务持久化客户端
 * 
 * 封装对 db-server /tasks 端点的调用。
 */

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';

export async function getAllTasks() {
  try {
    const resp = await fetch(`${DB_BASE_URL}/tasks`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (error) {
    console.error(`[task-client] getAllTasks failed: ${error.message}`);
    return [];
  }
}

export async function getTaskById(taskId) {
  try {
    const resp = await fetch(`${DB_BASE_URL}/tasks/${encodeURIComponent(taskId)}`);
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (error) {
    console.error(`[task-client] getTaskById failed: ${error.message}`);
    return null;
  }
}

export async function updateTask(taskId, updateData) {
  try {
    const resp = await fetch(`${DB_BASE_URL}/tasks/${encodeURIComponent(taskId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updateData)
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (error) {
    console.error(`[task-client] updateTask failed: ${error.message}`);
    return false;
  }
}

export async function getMaterialById(materialId) {
  try {
    const resp = await fetch(`${DB_BASE_URL}/materials/${encodeURIComponent(materialId)}`);
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (error) {
    console.error(`[task-client] getMaterialById failed: ${error.message}`);
    return null;
  }
}

export async function updateMaterial(materialId, updateData) {
  try {
    const resp = await fetch(`${DB_BASE_URL}/materials/${encodeURIComponent(materialId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updateData)
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (error) {
    console.error(`[task-client] updateMaterial failed: ${error.message}`);
    return false;
  }
}

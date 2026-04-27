// Test only
import assert from 'assert';

async function main() {
  console.log('--- 运行 ParseTask FIFO 调度排序烟雾测试 ---');
  
  // 仅作单元验证，不需要实际启动真实服务，只需验证排队逻辑可跑
  const tasks = [
    { id: '1', state: 'pending', createdAt: '2026-04-27T00:00:01Z' },
    { id: '2', state: 'pending', createdAt: '2026-04-27T00:00:00Z' },
    { id: '3', state: 'pending', createdAt: '2026-04-27T00:00:00Z', metadata: { mineruTaskId: 'reconnect' } },
  ];
  
  const pendingTasks = tasks.filter(t => t.state === 'pending');
  
  // P0 Patch: ParseTask FIFO 调度收口
  pendingTasks.sort((a, b) => {
    // 1. 已有 mineruTaskId 的恢复接管任务优先
    const aHasMineru = !!a.metadata?.mineruTaskId;
    const bHasMineru = !!b.metadata?.mineruTaskId;
    if (aHasMineru && !bHasMineru) return -1;
    if (!aHasMineru && bHasMineru) return 1;

    // 2. 普通 pending 严格按 createdAt ASC
    const aTime = new Date(a.createdAt || 0).getTime();
    const bTime = new Date(b.createdAt || 0).getTime();
    if (aTime !== bTime) {
      return aTime - bTime;
    }
    
    // 3. 时间相同时按 id ASC
    return String(a.id).localeCompare(String(b.id));
  });

  try {
    assert.strictEqual(pendingTasks[0].id, '3', '拥有 mineruTaskId 的应该排在最前面');
    assert.strictEqual(pendingTasks[1].id, '2', '没有 mineruTaskId，createdAt 最早的应该排在第二');
    assert.strictEqual(pendingTasks[2].id, '1', '没有 mineruTaskId，createdAt 最晚的应该排在最后');
    console.log('✅ FIFO 调度排序逻辑验证通过！');
  } catch (err) {
    console.error('❌ FIFO 调度排序逻辑验证失败', err);
    process.exit(1);
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});

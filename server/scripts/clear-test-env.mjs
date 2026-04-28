import 'dotenv/config';

const UPLOAD_SERVER_URL = process.env.UPLOAD_SERVER_URL || 'http://127.0.0.1:8082';
const DB_BASE_URL = process.env.DB_BASE_URL || 'http://127.0.0.1:8789';

async function main() {
  const args = process.argv.slice(2);
  const isExecute = args.includes('--execute');
  const isForce = args.includes('--force');

  console.log(`\n=== 🧪 Luceon 运维级环境清空工具 ===`);
  console.log(`Target DB: ${DB_BASE_URL}`);
  console.log(`Target API: ${UPLOAD_SERVER_URL}\n`);

  try {
    // 1. 获取所有 Materials
    const matResp = await fetch(`${DB_BASE_URL}/materials`).catch(() => null);
    if (!matResp || !matResp.ok) {
      console.error('❌ 无法连接到 DB Server。');
      process.exit(1);
    }
    const materials = await matResp.json();
    const materialIds = Object.values(materials).map((m) => m.id);

    if (materialIds.length === 0) {
      console.log('✅ 当前环境无任何素材与任务记录，无需清理。');
      process.exit(0);
    }

    console.log(`📦 发现 ${materialIds.length} 条素材记录准备清理...`);

    const payload = {
      materialIds,
      mode: 'cascade',
      dryRun: !isExecute,
      force: isForce
    };

    console.log(`\n${isExecute ? '⚠️ 执行模式 (EXECUTE)' : '🔍 演练模式 (DRY RUN)'}`);
    console.log(`调用统一清理接口: POST ${UPLOAD_SERVER_URL}/__proxy/upload/delete/materials`);
    
    const res = await fetch(`${UPLOAD_SERVER_URL}/__proxy/upload/delete/materials`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).catch(err => {
      console.error(`❌ 请求失败: ${err.message}`);
      process.exit(1);
    });

    if (res.status === 409) {
      const errData = await res.json();
      console.error(`\n❌ [冲突拦截] ${errData.error}`);
      console.log(`💡 提示: 存在运行中的任务，若需强制清理，请使用 --force 参数。`);
      process.exit(1);
    }

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      console.error(`\n❌ [服务端错误] HTTP ${res.status}: ${errData.error || '未知错误'}`);
      process.exit(1);
    }

    const result = await res.json();
    const sum = result.summary;

    console.log(`\n📊 级联清理统计结果:`);
    console.log(`----------------------------------------`);
    console.log(`- 记录 (Materials):     ${sum.materials}`);
    console.log(`- 资产详情 (Assets):    ${sum.assetDetails}`);
    console.log(`- 任务 (Tasks):         ${sum.tasks}`);
    console.log(`- AI 元数据工作 (Jobs): ${sum.aiJobs}`);
    console.log(`- 任务事件 (Events):    ${sum.taskEvents}`);
    console.log(`- MinIO 原始文件对象:   ${sum.originalObjects}`);
    console.log(`- MinIO 解析产物对象:   ${sum.parsedObjects}`);
    console.log(`- 运行中任务数:         ${sum.runningTasks}`);
    console.log(`----------------------------------------`);

    if (!isExecute) {
      console.log(`\n💡 提示: 当前为 DRY RUN 模式，未执行实际删除。`);
      console.log(`执行清理请添加 --execute 参数${sum.runningTasks > 0 ? ' 和 --force 参数' : ''}。`);
    } else {
      console.log(`\n✅ 清理完成！`);
    }
  } catch (err) {
    console.error(`\n❌ 发生意外错误:`, err);
    process.exit(1);
  }
}

main();

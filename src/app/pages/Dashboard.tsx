import { useNavigate } from 'react-router-dom';
import { FolderOpen, Cpu, CheckSquare, Clock, TrendingUp } from 'lucide-react';
import { useAppStore } from '../../store/appContext';
import { StatCard } from '../components/StatCard';
import { StatusBadge } from '../components/StatusBadge';

export function Dashboard() {
  const { state } = useAppStore();
  const navigate = useNavigate();

  const { materials, processTasks, tasks } = state;

  // 统计数据
  const totalMaterials = materials.length;
  const processingCount = materials.filter((m) => m.status === 'processing').length;
  const completedMaterials = materials.filter((m) => m.status === 'completed').length;
  const activeTasks = tasks.filter((t) => t.status === 'processing' || t.status === 'pending').length;

  // 最近5条资料
  const recentMaterials = [...materials]
    .sort((a, b) => b.uploadTimestamp - a.uploadTimestamp)
    .slice(0, 5);

  // 进行中任务
  const inProgressTasks = processTasks
    .filter((t) => t.status === 'processing' || t.status === 'reviewing')
    .slice(0, 4);

  return (
    <div className="p-6 space-y-6">
      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">仪表盘</h1>
        <p className="text-sm text-gray-500 mt-1">教育资料管理系统概览</p>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <StatCard
          title="原始资料总数"
          value={totalMaterials}
          icon={<FolderOpen size={20} />}
          color="blue"
          subtitle={`${completedMaterials} 已完成`}
        />
        <StatCard
          title="处理中"
          value={processingCount}
          icon={<Cpu size={20} />}
          color="orange"
          subtitle="资料正在处理"
        />
        <StatCard
          title="活跃任务"
          value={activeTasks}
          icon={<CheckSquare size={20} />}
          color="purple"
          subtitle="待处理 + 进行中"
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* 最近上传资料 */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-gray-800 flex items-center gap-2">
              <Clock size={16} className="text-blue-500" />
              最近上传资料
            </h2>
            <button
              onClick={() => navigate('/source-materials')}
              className="text-sm text-blue-600 hover:text-blue-700 font-medium"
            >
              查看全部
            </button>
          </div>
          <div className="space-y-3">
            {recentMaterials.map((m) => (
              <div
                key={m.id}
                className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0 cursor-pointer hover:bg-gray-50 rounded px-2 -mx-2 transition-colors"
                onClick={() => navigate(`/asset/${m.id}`)}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-800 truncate">{m.title}</p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {m.type} · {m.size} · {m.uploadTime}
                  </p>
                </div>
                <StatusBadge status={m.status} className="ml-3 flex-shrink-0" />
              </div>
            ))}
          </div>
        </div>

        {/* 进行中任务 */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-gray-800 flex items-center gap-2">
              <TrendingUp size={16} className="text-orange-500" />
              进行中任务
            </h2>
            <button
              onClick={() => navigate('/process-workbench')}
              className="text-sm text-blue-600 hover:text-blue-700 font-medium"
            >
              查看全部
            </button>
          </div>
          <div className="space-y-3">
            {inProgressTasks.length === 0 && (
              <p className="text-sm text-gray-400 text-center py-4">暂无进行中任务</p>
            )}
            {inProgressTasks.map((task) => (
              <div key={task.id} className="py-2 border-b border-gray-50 last:border-0">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-sm font-medium text-gray-800 truncate flex-1 mr-2">{task.name}</p>
                  <StatusBadge status={task.status} className="flex-shrink-0" />
                </div>
                <div className="flex items-center gap-2 mt-1.5">
                  <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                    <div
                      className="bg-blue-500 h-1.5 rounded-full transition-all"
                      style={{ width: `${task.progress}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-400 flex-shrink-0">{task.progress}%</span>
                </div>
                <p className="text-xs text-gray-400 mt-1">{task.stage}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

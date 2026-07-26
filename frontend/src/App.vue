<script setup lang="ts">
import { Collection, Connection, Document, HomeFilled, MagicStick, Setting, View } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import { useCurrentUser } from '@/utils/user'

const route = useRoute()
const currentUser = useCurrentUser()

const menuItems = computed(() => [
  { icon: HomeFilled, path: '/', label: '工作概览' },
  { icon: Document, path: '/assets', label: 'PDF 资产' },
  { icon: Collection, path: '/pipeline/runs', label: '解析任务' },
  { icon: MagicStick, path: '/workflow/jobs', label: '精修任务 V2.3' },
  { icon: Connection, path: '/workflow-v3/runs', label: '技能原生 V3' },
  { icon: View, path: '/review/compare', label: '比对审阅' },
  ...(currentUser.value?.capabilities?.runtime_admin ? [{ icon: Setting, path: '/settings', label: '运行设置' }] : [])
])

const activeMenu = computed(() => {
  const path = route.path
  if (path === '/') return '/'
  if (path.startsWith('/files/preview') || path.startsWith('/assets/preview')) return '/assets'
  if (path.startsWith('/review/preview')) return '/review/compare'
  if (path.startsWith('/review') || path === '/review') return '/review/compare'
  if (path.startsWith('/settings')) return '/settings'
  if (path.startsWith('/assets') || path.startsWith('/files')) return '/assets'
  if (path.startsWith('/pipeline')) return '/pipeline/runs'
  if (path.startsWith('/workflow-v3')) return '/workflow-v3/runs'
  if (path.startsWith('/workflow')) return '/workflow/jobs'
  return menuItems.value.find(item => path === item.path)?.path || path
})

const immersiveView = computed(() => route.name === 'FilePreview' || route.name === 'ReviewPreview')
const workbenchView = computed(() => ['/assets', '/pipeline/runs', '/workflow/jobs', '/workflow-v3/runs', '/review/compare'].includes(route.path))
const authView = computed(() => route.name === 'Login')
</script>

<template>
  <router-view v-if="authView" />
  <div v-else class="app-layout">
    <aside :class="['app-sidebar', { 'app-sidebar-compact': immersiveView }]">
      <router-link class="brand" to="/" aria-label="Luceon 首页">
        <span class="brand-mark">
          <img src="/logo.png" alt="logo" class="logo" />
        </span>
        <span class="brand-copy">
          <strong>Luceon</strong>
          <small>2026</small>
        </span>
      </router-link>
      
      <nav class="app-nav" aria-label="主导航">
        <span class="nav-caption">工作空间</span>
        <router-link
          v-for="item in menuItems" 
          :key="item.path" 
          :to="item.path"
          class="nav-item"
          :class="{ active: activeMenu === item.path }"
          :aria-current="activeMenu === item.path ? 'page' : undefined"
          :title="item.label"
        >
          <el-icon :size="18"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      
      <div class="sidebar-bottom">
        <div class="runtime-indicator">
          <span class="runtime-dot"></span>
          <span>本机服务</span>
        </div>
      </div>
    </aside>

    <div class="main-area">
      <template v-if="immersiveView">
        <router-view />
      </template>
      <template v-else>
        <main :class="['content-area', { 'content-area-workbench': workbenchView }]">
          <div :class="['content-wrapper', { 'content-wrapper-workbench': workbenchView }]">
            <router-view />
          </div>
        </main>
      </template>
    </div>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  width: 100%;
  min-height: 100vh;
  background: var(--bg-secondary);
}

.app-sidebar {
  width: 216px;
  min-height: 100vh;
  padding: 18px 14px 16px;
  background: var(--bg-primary);
  border-right: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  z-index: 100;
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 44px;
  padding: 0 8px;
  color: var(--text-primary);
}

.brand:hover {
  color: var(--text-primary);
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  overflow: hidden;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: var(--bg-primary);
}

.logo {
  width: 34px;
  height: 34px;
}

.brand-copy {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.brand-copy strong {
  font-size: 17px;
  font-weight: 720;
  letter-spacing: 0;
}

.brand-copy small {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
}

.app-nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 34px;
}

.nav-caption {
  margin: 0 10px 8px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 650;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 40px;
  padding: 0 11px;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 520;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--primary-tint);
  color: var(--primary-dark);
  font-weight: 650;
}

.sidebar-bottom {
  margin-top: auto;
  padding: 14px 8px 0;
  border-top: 1px solid var(--border-light);
}

.runtime-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 12px;
}

.runtime-dot {
  width: 7px;
  height: 7px;
  background: var(--success-color);
  border-radius: 50%;
}

.app-sidebar-compact {
  width: 72px;
  align-items: center;
  padding-right: 10px;
  padding-left: 10px;
}

.app-sidebar-compact .brand {
  padding: 0;
}

.app-sidebar-compact .brand-copy,
.app-sidebar-compact .nav-caption,
.app-sidebar-compact .nav-item span,
.app-sidebar-compact .runtime-indicator span:last-child {
  display: none;
}

.app-sidebar-compact .app-nav {
  width: 100%;
}

.app-sidebar-compact .nav-item {
  justify-content: center;
  padding: 0;
}

.app-sidebar-compact .sidebar-bottom {
  width: 100%;
  padding-right: 0;
  padding-left: 0;
}

.app-sidebar-compact .runtime-indicator {
  justify-content: center;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
}

.content-area {
  flex: 1;
  width: 100%;
  min-height: 0;
  overflow: auto;
  padding: 28px 30px;
}

.content-area-workbench {
  overflow: hidden;
  padding: 22px 24px;
}

.content-wrapper {
  width: min(100%, 1180px);
  min-height: 100%;
  margin: 0 auto;
  animation: pageEnter 0.2s ease-out;
}

.content-wrapper-workbench {
  width: 100%;
  height: 100%;
  margin: 0;
  min-height: 0;
}

@keyframes pageEnter {
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 1100px) {
  .app-sidebar {
    width: 188px;
  }

  .content-area {
    padding: 24px;
  }
}

@media (max-width: 768px) {
  .app-layout {
    flex-direction: column;
  }

  .app-sidebar {
    width: 100%;
    min-height: auto;
    height: 58px;
    padding: 8px 10px;
    flex-direction: row;
    align-items: center;
    border-right: 0;
    border-bottom: 1px solid var(--border-light);
  }

  .brand {
    padding: 0 5px;
  }

  .brand-copy {
    display: none;
  }

  .app-nav {
    flex-direction: row;
    flex: 1;
    justify-content: flex-end;
    gap: 4px;
    padding: 0;
  }

  .nav-caption {
    display: none;
  }

  .nav-item {
    justify-content: center;
    width: 42px;
    min-height: 40px;
    padding: 0;
  }

  .nav-item span {
    display: none;
  }

  .sidebar-bottom {
    display: none;
  }

  .main-area {
    height: calc(100vh - 58px);
    min-height: auto;
  }

  .content-area {
    padding: 18px 14px;
  }

  .content-area-workbench {
    padding: 14px 10px;
  }
}
</style>

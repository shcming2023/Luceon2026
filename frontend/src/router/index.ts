import { createRouter, createWebHistory } from 'vue-router'
import { ensureCurrentUser } from '@/utils/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/Login.vue')
    },
    {
      path: '/',
      name: 'Home',
      component: () => import('../views/Home.vue')
    },
    {
      path: '/upload',
      name: 'Upload',
      redirect: '/assets'
    },
    {
      path: '/cms/tasks',
      redirect: '/assets'
    },
    {
      path: '/files',
      redirect: '/assets'
    },
    {
      path: '/assets',
      name: 'PdfAssets',
      component: () => import('../views/PdfAssets.vue')
    },
    {
      path: '/pipeline/runs',
      name: 'PipelineTasks',
      component: () => import('../views/PipelineTasks.vue')
    },
    {
      path: '/workflow/jobs',
      name: 'RefinementTasks',
      component: () => import('../views/RefinementTasks.vue')
    },
    {
      path: '/workflow-v3/runs',
      name: 'WorkflowV3Runs',
      component: () => import('../views/WorkflowV3Runs.vue')
    },
    {
      path: '/files/preview/:id',
      name: 'FilePreview',
      component: () => import('../views/FilePreview.vue'),
      props: route => ({
        fileId: route.params.id,
        page: Number(route.query.page) || 1
      })
    },
    {
      path: '/assets/preview/:id',
      redirect: route => ({
        path: `/files/preview/${String(route.params.id)}`,
        query: route.query
      })
    },
    {
      path: '/review',
      name: 'Review',
      redirect: '/review/compare'
    },
    {
      path: '/review/compare',
      name: 'PdfCompareReview',
      component: () => import('../views/CompareReview.vue')
    },
    {
      path: '/review/pdf',
      redirect: '/review/compare'
    },
    {
      path: '/review/outline',
      redirect: '/review/compare'
    },
    {
      path: '/review/standard',
      redirect: '/review/compare'
    },
    {
      path: '/review/final',
      redirect: '/review/compare'
    },
    {
      path: '/review/preview/:id',
      name: 'ReviewPreview',
      redirect: route => ({
        path: '/review/compare',
        query: {
          asset_id: String(route.params.id)
        }
      })
    },
    {
      path: '/settings',
      name: 'Settings',
      component: () => import('../views/Settings.vue'),
      meta: { requiresRuntimeAdmin: true }
    }
  ]
})

router.beforeEach(async (to) => {
  if (to.name === 'Login') return true

  try {
    const user = await ensureCurrentUser()
    if (to.meta.requiresRuntimeAdmin && !user.capabilities?.runtime_admin) {
      return { path: '/' }
    }
    return true
  } catch {
    return {
      path: '/login',
      query: { redirect: to.fullPath }
    }
  }
})

export default router 

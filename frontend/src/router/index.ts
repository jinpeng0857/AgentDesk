import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import LoginView from '../views/LoginView.vue'
import TicketsView from '../views/TicketsView.vue'
import ApprovalView from '../views/ApprovalView.vue'
import TraceViewerView from '../views/TraceViewerView.vue'
import EvalDashboardView from '../views/EvalDashboardView.vue'

export const routes = [
  {
    path: '/',
    redirect: '/tickets',
  },
  {
    path: '/login',
    name: 'login',
    component: LoginView,
  },
  {
    path: '/tickets',
    name: 'tickets',
    component: TicketsView,
  },
  {
    path: '/approvals',
    name: 'approvals',
    component: ApprovalView,
  },
  {
    path: '/traces/:ticketId?',
    name: 'traces',
    component: TraceViewerView,
  },
  {
    path: '/eval',
    name: 'eval',
    component: EvalDashboardView,
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore()
  if (to.path !== '/login' && !authStore.isAuthenticated) {
    next('/login')
  } else if (to.path === '/login' && authStore.isAuthenticated) {
    next('/tickets')
  } else if (to.path === '/approvals' && authStore.user && authStore.user.role !== 'approver') {
    next('/tickets')
  } else {
    next()
  }
})

export default router

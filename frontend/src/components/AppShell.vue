<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import UiIcon, { type IconName } from './UiIcon.vue'
defineProps<{
  active: 'tickets' | 'approvals' | 'traces' | 'eval'
  title: string
  subtitle: string
  eyebrow: string
}>()
const router = useRouter()
const auth = useAuthStore()
const navigation: {
  key: 'tickets' | 'approvals' | 'traces' | 'eval'
  label: string
  icon: IconName
  path: string
}[] = [
  { key: 'tickets', label: '工单工作台', icon: 'tickets', path: '/tickets' },
  { key: 'approvals', label: '人工审批', icon: 'shield', path: '/approvals' },
  { key: 'traces', label: '执行轨迹', icon: 'trace', path: '/traces' },
  { key: 'eval', label: '评估记录', icon: 'chart', path: '/eval' },
]
function navigate(path: string): void {
  void router.push(path)
}
function logout(): void {
  auth.logout()
  void router.push('/login')
}
</script>
<template>
  <div class="app-layout">
    <a class="skip-link" href="#workspace">跳转到工作区</a>
    <aside class="app-sidebar">
      <button
        class="brand-link"
        aria-label="AgentDesk 工单工作台"
        @click="navigate('/tickets')"
      >
        <span class="brand-mark"><span></span><span></span><span></span></span
        ><span>AgentDesk<small>IT SERVICE DESK</small></span>
      </button>
      <div class="sidebar-label">工作空间</div>
      <nav class="app-nav" aria-label="主导航">
        <template v-for="item in navigation" :key="item.key">
          <button
            v-if="item.key !== 'approvals' || auth.user?.role === 'approver'"
            :class="['nav-item', { active: active === item.key }]"
            :aria-current="active === item.key ? 'page' : undefined"
            @click="navigate(item.path)"
          >
            <UiIcon :name="item.icon" /><span>{{ item.label }}</span
            ><span v-if="active === item.key" class="nav-current"></span>
          </button>
        </template>
      </nav>
      <div class="sidebar-note">
        <span class="sidebar-note-label">受治理的自动化</span>
        <p>每一次操作有边界，<br />每一步处理可追溯。</p>
        <div class="role-dots">
          <span>S</span><span>K</span><span>A</span><span>P</span>
        </div>
        <small>四个角色 · 各司其职</small>
      </div>
      <div class="sidebar-user">
        <span class="avatar">{{
          (auth.user?.user_id || 'A').slice(0, 1).toUpperCase()
        }}</span>
        <div class="user-copy">
          <strong>{{ auth.user?.user_id || '当前账号' }}</strong
          ><small>{{
            auth.user?.role === 'approver' ? '审批人' : '请求人'
          }}</small>
        </div>
        <button
          class="icon-button logout-button"
          aria-label="退出登录"
          @click="logout"
        >
          <UiIcon name="logout" :size="18" />
        </button>
      </div>
    </aside>
    <div class="app-main">
      <header class="workspace-topbar">
        <div class="breadcrumb">
          工作空间 <span>/</span> <strong>IT 服务台</strong>
        </div>
        <div class="topbar-actions">
          <span class="environment-label"><span></span>模拟环境</span>
          <details class="scope-details">
            <summary><UiIcon name="shield" :size="16" />能力边界</summary>
            <div class="scope-content">
              <strong>AgentDesk 的工作范围</strong>
              <p>
                处理模拟 IT
                工单，支持知识检索、账户与订单查询、服务状态查询，以及受治理的写操作。
              </p>
              <ul>
                <li>只对本人账户和订单发起操作。</li>
                <li>权限申请只创建请求，不直接授予权限。</li>
                <li>200 美元及以上退款需人工审批。</li>
                <li>审批只能通过或拒绝，不能修改参数。</li>
              </ul>
              <p class="scope-footnote">
                使用模拟数据，不连接真实企业或支付系统。完整端到端验收状态待更新。
              </p>
            </div>
          </details>
          <button
            class="icon-button mobile-logout"
            aria-label="退出登录"
            @click="logout"
          >
            <UiIcon name="logout" :size="16" />
          </button>
        </div>
      </header>
      <main id="workspace" class="workspace" tabindex="-1">
        <div class="page-heading">
          <div>
            <span class="eyebrow">{{ eyebrow }}</span>
            <h1>{{ title }}</h1>
            <p>{{ subtitle }}</p>
          </div>
          <div class="page-actions"><slot name="actions" /></div>
        </div>
        <slot />
      </main>
      <footer class="workspace-footer">
        <span>AgentDesk · 模拟 IT 服务台</span
        ><span>自动处理 · 人工决策 · 可追溯记录</span>
      </footer>
    </div>
  </div>
</template>

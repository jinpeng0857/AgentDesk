<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import UiIcon from '../components/UiIcon.vue'
const router = useRouter()
const authStore = useAuthStore()
const username = ref('')
const password = ref('')
const localError = ref<string | null>(null)
async function handleSubmit(): Promise<void> {
  if (authStore.loading) return
  localError.value = null
  const currentUsername = username.value.trim()
  const currentPassword = password.value
  password.value = ''
  if (!currentUsername || !currentPassword) {
    localError.value = '请输入账号和密码。'
    return
  }
  try {
    await authStore.login(currentUsername, currentPassword)
    await router.push('/tickets')
  } catch {
    localError.value = authStore.error || '登录失败，请检查账号和密码。'
  }
}
</script>
<template>
  <main class="login-layout">
    <section class="login-story" aria-label="AgentDesk 产品介绍">
      <div class="login-brand">
        <span class="brand-mark"><span></span><span></span><span></span></span
        ><strong>AgentDesk</strong
        ><span class="login-environment">模拟环境</span>
      </div>
      <div class="story-content">
        <span class="story-kicker">A SERVICE DESK WITH CLEAR BOUNDARIES</span>
        <h1>把问题交给 Agent，<br />把决策留在掌握中。</h1>
        <p class="story-description">
          从知识咨询到受治理的操作，<br />让每张工单的处理过程清楚可见。
        </p>
        <div class="story-workflow">
          <div>
            <span>01</span><UiIcon name="tickets" :size="21" /><strong
              >描述问题</strong
            >
            <p>一句话提交请求</p>
          </div>
          <div>
            <span>02</span><UiIcon name="shield" :size="21" /><strong
              >按边界处理</strong
            >
            <p>高风险操作等待审批</p>
          </div>
          <div>
            <span>03</span><UiIcon name="trace" :size="21" /><strong
              >回查每一步</strong
            >
            <p>答复、依据和执行记录</p>
          </div>
        </div>
      </div>
      <div class="story-footer">
        <span>四个角色，一个可追溯的工作流。</span
        ><span>SUPERVISOR / KNOWLEDGE / ACTION / POLICY</span>
      </div>
    </section>
    <section class="login-form-area">
      <div class="login-form-wrap">
        <span class="eyebrow">YOUR WORKSPACE</span>
        <h2>登录工作空间</h2>
        <p class="login-subtitle">使用已配置的演示账号继续。</p>
        <div v-if="localError" class="error-banner" role="alert">
          {{ localError }}
        </div>
        <form @submit.prevent="handleSubmit">
          <div class="login-field">
            <label for="username">账号</label
            ><input
              id="username"
              v-model="username"
              type="text"
              required
              autocomplete="username"
              placeholder="请输入账号"
              :disabled="authStore.loading"
            />
          </div>
          <div class="login-field">
            <label for="password">密码</label
            ><input
              id="password"
              v-model="password"
              type="password"
              required
              autocomplete="current-password"
              placeholder="请输入密码"
              :disabled="authStore.loading"
            />
          </div>
          <button
            class="button primary full"
            type="submit"
            :disabled="authStore.loading"
          >
            {{ authStore.loading ? '登录中…' : '登录 AgentDesk'
            }}<UiIcon name="arrow" :size="17" />
          </button>
        </form>
        <div class="login-scope">
          <UiIcon name="lock" :size="16" />
          <p>
            当前使用模拟账户、订单和服务数据。<br />请求人处理本人事务，审批人审核高风险操作。
          </p>
        </div>
      </div>
      <span class="login-bottom">AgentDesk · 作品集演示项目</span>
    </section>
  </main>
</template>
<style scoped>
.login-layout {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 56% 44%;
}
.login-story {
  background: #142033;
  color: white;
  padding: 42px 56px 30px;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}
.login-story::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(#a8c5ff05 1px, transparent 1px),
    linear-gradient(90deg, #a8c5ff05 1px, transparent 1px);
  background-size: 72px 72px;
  mask-image: linear-gradient(transparent 10%, #000 90%);
}
.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  z-index: 1;
}
.login-brand strong {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.7px;
}
.login-environment {
  font-size: 10px;
  border: 1px solid #465a79;
  padding: 3px 8px;
  border-radius: 4px;
  color: #c1d0e5;
  margin-left: auto;
}
.story-content {
  margin: auto 0;
  padding: 68px 0;
  z-index: 1;
}
.story-kicker {
  font-size: 9px;
  letter-spacing: 2px;
  color: #9eb8e3;
}
.story-content h1 {
  font-size: clamp(30px, 3vw, 45px);
  font-weight: 500;
  line-height: 1.6;
  margin: 22px 0 20px;
  letter-spacing: -1px;
}
.story-description {
  font-size: 13px;
  color: #b0bfd4;
  line-height: 2;
}
.story-workflow {
  margin-top: 58px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 22px;
  border-top: 1px solid #3a4c65;
  padding-top: 25px;
}
.story-workflow > div {
  position: relative;
}
.story-workflow span {
  font-size: 9px;
  color: #94aaca;
  position: absolute;
  right: 6px;
  top: 3px;
}
.story-workflow svg {
  color: #aec6f8;
  display: block;
  margin-bottom: 17px;
}
.story-workflow strong {
  font-size: 12px;
  font-weight: 500;
}
.story-workflow p {
  font-size: 10px;
  color: #a3b5d0;
  margin-top: 6px;
}
.story-footer {
  z-index: 1;
  color: #a0b2cc;
  font-size: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.story-footer span:last-child {
  font-size: 7px;
  letter-spacing: 1px;
  color: #8b9ebc;
}
.login-form-area {
  padding: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  background: white;
}
.login-form-wrap {
  width: 100%;
  max-width: 340px;
}
.login-form-wrap h2 {
  font-size: 27px;
  font-weight: 600;
  margin-top: 10px;
}
.login-subtitle {
  font-size: 12px;
  color: var(--muted);
  margin-top: 11px;
  margin-bottom: 34px;
}
.login-field {
  margin-bottom: 22px;
}
.login-field input {
  min-height: 45px;
  background: #fbfcfe;
}
.login-form-wrap .button {
  margin-top: 6px;
  min-height: 46px;
}
.login-scope {
  display: flex;
  gap: 9px;
  margin-top: 26px;
  color: var(--muted);
  border-top: 1px solid var(--line);
  padding-top: 22px;
}
.login-scope svg {
  margin-top: 4px;
}
.login-scope p {
  font-size: 10px;
  line-height: 1.9;
}
.login-bottom {
  position: absolute;
  bottom: 28px;
  font-size: 10px;
  color: var(--muted);
}
@media (max-width: 1000px) {
  .login-story {
    padding: 32px;
  }
  .login-form-area {
    padding: 36px;
  }
  .story-workflow {
    gap: 12px;
  }
}
@media (max-width: 700px) {
  .login-layout {
    grid-template-columns: 1fr;
  }
  .login-story {
    padding: 24px;
  }
  .story-content {
    padding: 38px 0 20px;
  }
  .story-content h1 {
    font-size: 29px;
    margin: 16px 0;
  }
  .story-workflow,
  .story-footer {
    display: none;
  }
  .login-form-area {
    padding: 40px 24px 72px;
  }
  .login-form-wrap {
    max-width: 400px;
  }
  .login-form-wrap h2 {
    font-size: 24px;
  }
  .login-bottom {
    bottom: 20px;
  }
}
</style>

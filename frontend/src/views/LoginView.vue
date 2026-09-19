<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const localError = ref<string | null>(null)

async function handleSubmit(): Promise<void> {
  localError.value = null
  const currentUsername = username.value.trim()
  const currentPassword = password.value

  // Security guarantee: wipe password state immediately upon submission
  password.value = ''

  if (!currentUsername || !currentPassword) {
    localError.value = 'Please provide both username and password.'
    return
  }

  try {
    await authStore.login(currentUsername, currentPassword)
    await router.push('/tickets')
  } catch {
    localError.value = authStore.error || 'Authentication failed'
  }
}
</script>

<template>
  <div class="login-container">
    <div class="login-card">
      <h2>AgentDesk Login</h2>
      <p class="subtitle">Enter your enterprise credentials</p>

      <div v-if="localError" class="error-banner">
        {{ localError }}
      </div>

      <form @submit.prevent="handleSubmit">
        <div class="form-group">
          <label for="username">Username</label>
          <input
            id="username"
            v-model="username"
            type="text"
            required
            autocomplete="username"
            placeholder="Username (e.g. alice)"
          />
        </div>

        <div class="form-group">
          <label for="password">Password</label>
          <input
            id="password"
            v-model="password"
            type="password"
            required
            autocomplete="current-password"
            placeholder="Password"
          />
        </div>

        <button type="submit" :disabled="authStore.loading" class="btn-primary">
          {{ authStore.loading ? 'Signing in...' : 'Sign In' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 80vh;
  padding: 1rem;
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 2rem;
  background: #ffffff;
  border-radius: 8px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
  border: 1px solid #e2e8f0;
}

h2 {
  margin: 0 0 0.5rem 0;
  color: #1a202c;
  font-size: 1.5rem;
}

.subtitle {
  color: #718096;
  margin-bottom: 1.5rem;
  font-size: 0.875rem;
}

.error-banner {
  background-color: #fed7d7;
  color: #c53030;
  padding: 0.75rem 1rem;
  border-radius: 4px;
  margin-bottom: 1rem;
  font-size: 0.875rem;
  border: 1px solid #feb2b2;
}

.form-group {
  margin-bottom: 1.25rem;
}

label {
  display: block;
  font-weight: 500;
  margin-bottom: 0.5rem;
  color: #4a5568;
  font-size: 0.875rem;
}

input {
  width: 100%;
  padding: 0.625rem;
  border: 1px solid #cbd5e0;
  border-radius: 4px;
  font-size: 0.875rem;
  box-sizing: border-box;
}

input:focus {
  outline: none;
  border-color: #3182ce;
  box-shadow: 0 0 0 1px #3182ce;
}

.btn-primary {
  width: 100%;
  padding: 0.75rem;
  background-color: #3182ce;
  color: white;
  border: none;
  border-radius: 4px;
  font-weight: 600;
  font-size: 0.875rem;
  cursor: pointer;
  transition: background-color 0.2s;
}

.btn-primary:hover:not(:disabled) {
  background-color: #2b6cb0;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>

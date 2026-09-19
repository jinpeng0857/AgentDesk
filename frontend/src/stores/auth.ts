import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ApiClient, ApiError } from '../api/client'
import type { ActorResponse } from '../api/types'
import { useTicketStore } from './tickets'

export const useAuthStore = defineStore('auth', () => {
  // In-memory token storage: NEVER touches localStorage or sessionStorage
  const token = ref<string | null>(null)
  const user = ref<ActorResponse | null>(null)
  const error = ref<string | null>(null)
  const loading = ref(false)

  const apiClient = new ApiClient(() => token.value)

  const isAuthenticated = computed(() => Boolean(token.value))

  async function login(username: string, password: string): Promise<string> {
    loading.value = true
    error.value = null
    try {
      const response = await apiClient.login({ username, password })
      token.value = response.access_token
      return response.access_token
    } catch (err) {
      token.value = null
      if (err instanceof ApiError) {
        error.value = err.message
      } else {
        error.value = 'Login failed. Please check your credentials.'
      }
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchMe(): Promise<ActorResponse | null> {
    if (!token.value) return null
    try {
      const actor = await apiClient.getCurrentActor()
      user.value = actor
      return actor
    } catch {
      logout()
      return null
    }
  }

  function logout(): void {
    token.value = null
    user.value = null
    error.value = null
    // Stop all active polling on logout
    const ticketStore = useTicketStore()
    ticketStore.stopAllPolling()
  }

  return {
    token,
    user,
    error,
    loading,
    isAuthenticated,
    apiClient,
    login,
    fetchMe,
    logout,
  }
})

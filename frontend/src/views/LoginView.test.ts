import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import LoginView from './LoginView.vue'
import { useAuthStore } from '../stores/auth'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}))

describe('LoginView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    pushMock.mockClear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('keeps JWT strictly in Pinia memory, clears password immediately, and leaves localStorage empty', async () => {
    const authStore = useAuthStore()

    const fakeTokenResponse = {
      access_token: 'fake-jwt-token-12345',
      token_type: 'bearer',
      expires_in_s: 900,
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => fakeTokenResponse,
      }),
    )

    const wrapper = mount(LoginView)

    const usernameInput = wrapper.find('input[type="text"]')
    const passwordInput = wrapper.find('input[type="password"]')
    const form = wrapper.find('form')

    await usernameInput.setValue('alice')
    await passwordInput.setValue('SuperSecret123!')

    await form.trigger('submit')
    await flushPromises()

    // 1. Password input field is immediately wiped clean
    expect((passwordInput.element as HTMLInputElement).value).toBe('')

    // 2. JWT is saved in Pinia memory
    expect(authStore.token).toBe('fake-jwt-token-12345')
    expect(authStore.isAuthenticated).toBe(true)

    // 3. localStorage is strictly empty
    expect(window.localStorage.length).toBe(0)
    expect(window.localStorage.getItem('token')).toBeNull()
    expect(window.localStorage.getItem('access_token')).toBeNull()

    // 4. Navigates to /tickets
    expect(pushMock).toHaveBeenCalledWith('/tickets')
  })

  it('displays typed error when authentication fails without leaking credentials', async () => {
    const authStore = useAuthStore()

    const errorEnvelope = {
      error: {
        code: 'INVALID_CREDENTIALS',
        message: 'Invalid username or password',
        request_id: 'req-err-01',
      },
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => errorEnvelope,
      }),
    )

    const wrapper = mount(LoginView)

    const usernameInput = wrapper.find('input[type="text"]')
    const passwordInput = wrapper.find('input[type="password"]')
    const form = wrapper.find('form')

    await usernameInput.setValue('alice')
    await passwordInput.setValue('WrongPassword!')

    await form.trigger('submit')
    await flushPromises()

    // Password input wiped on failed attempt too
    expect((passwordInput.element as HTMLInputElement).value).toBe('')
    expect(authStore.token).toBeNull()
    expect(authStore.isAuthenticated).toBe(false)
    expect(window.localStorage.length).toBe(0)

    // Error message displayed to user
    expect(wrapper.text()).toContain('Invalid username or password')
    expect(pushMock).not.toHaveBeenCalled()
  })
})

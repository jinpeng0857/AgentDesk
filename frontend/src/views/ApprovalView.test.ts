import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ApprovalView from './ApprovalView.vue'
import { useAuthStore } from '../stores/auth'
import { useApprovalsStore } from '../stores/approvals'
import type { ApprovalDetailResponse, ApprovalSummaryResponse } from '../api/types'
import { ApiError, type ApiClientLike } from '../api/client'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}))

describe('ApprovalView', () => {
  const sampleSummary: ApprovalSummaryResponse = {
    approval_id: 'app-001',
    ticket_id: 'ticket-101',
    operation_id: 'op-201',
    tool_name: 'process_refund',
    risk_level: 'high',
    status: 'pending',
    version: 1,
    created_at: '2026-09-19T10:00:00Z',
    expires_at: '2026-09-19T12:00:00Z',
  }

  const sampleDetail: ApprovalDetailResponse = {
    ...sampleSummary,
    args_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    args_summary: [
      { name: 'order_id', value: 'ORD-999' },
      { name: 'amount', value: '250.00' },
      { name: 'account_number', value: '***' },
    ],
    reason_code: 'REFUND_EXCEEDS_200_USD',
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    pushMock.mockClear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders pending approvals list and selects the first item by default', async () => {
    const authStore = useAuthStore()
    authStore.user = { user_id: 'approver_bob', role: 'approver' }

    const fakeClient = {
      getCurrentActor: vi.fn().mockResolvedValue({ user_id: 'approver_bob', role: 'approver' }),
      listApprovals: vi.fn().mockResolvedValue([sampleSummary]),
      getApproval: vi.fn().mockResolvedValue(sampleDetail),
      decideApproval: vi.fn(),
      login: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTraces: vi.fn(),
    }

    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const approvalsStore = useApprovalsStore()
    approvalsStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(fakeClient.listApprovals).toHaveBeenCalledWith('pending')
    expect(fakeClient.getApproval).toHaveBeenCalledWith('app-001')

    // Renders summary in sidebar
    expect(wrapper.text()).toContain('process_refund')
    expect(wrapper.text()).toContain('high risk')

    // Renders detail in main area
    expect(wrapper.text()).toContain('app-001')
    expect(wrapper.text()).toContain('op-201')
    expect(wrapper.text()).toContain('REFUND_EXCEEDS_200_USD')
    expect(wrapper.text()).toContain('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    // Check args table
    expect(wrapper.text()).toContain('order_id')
    expect(wrapper.text()).toContain('ORD-999')
    expect(wrapper.text()).toContain('amount')
    expect(wrapper.text()).toContain('250.00')
    expect(wrapper.text()).toContain('account_number')
    expect(wrapper.text()).toContain('***')

    // Sensitive badge is shown
    expect(wrapper.find('.badge-sensitive').exists()).toBe(true)

    // Action buttons exist for pending status
    expect(wrapper.find('[data-testid="approve-button"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="reject-button"]').exists()).toBe(true)
  })

  it('submits approve decision with expected version', async () => {
    const authStore = useAuthStore()
    authStore.user = { user_id: 'approver_bob', role: 'approver' }

    const fakeClient = {
      getCurrentActor: vi.fn().mockResolvedValue({ user_id: 'approver_bob', role: 'approver' }),
      listApprovals: vi.fn().mockResolvedValue([sampleSummary]),
      getApproval: vi.fn().mockResolvedValue(sampleDetail),
      decideApproval: vi.fn().mockResolvedValue({
        approval_id: 'app-001',
        status: 'approved',
        version: 2,
      }),
      login: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTraces: vi.fn(),
    }

    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const approvalsStore = useApprovalsStore()
    approvalsStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    const textarea = wrapper.find('textarea')
    await textarea.setValue('Customer eligible for VIP exception')

    const approveBtn = wrapper.find('[data-testid="approve-button"]')
    await approveBtn.trigger('click')
    await flushPromises()

    expect(fakeClient.decideApproval).toHaveBeenCalledWith('app-001', {
      decision: 'approve',
      version: 1,
      reason: 'Customer eligible for VIP exception',
    })

    // Once decided, form is replaced by Decision Recorded box
    expect(wrapper.find('[data-testid="approve-button"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Decision Recorded')
    expect(wrapper.text()).toContain('approved')
  })

  it('submits reject decision with expected version', async () => {
    const authStore = useAuthStore()
    authStore.user = { user_id: 'approver_bob', role: 'approver' }

    const fakeClient = {
      getCurrentActor: vi.fn().mockResolvedValue({ user_id: 'approver_bob', role: 'approver' }),
      listApprovals: vi.fn().mockResolvedValue([sampleSummary]),
      getApproval: vi.fn().mockResolvedValue(sampleDetail),
      decideApproval: vi.fn().mockResolvedValue({
        approval_id: 'app-001',
        status: 'rejected',
        version: 2,
      }),
      login: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTraces: vi.fn(),
    }

    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const approvalsStore = useApprovalsStore()
    approvalsStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    const textarea = wrapper.find('textarea')
    await textarea.setValue('Amount outside refund window')

    const rejectBtn = wrapper.find('[data-testid="reject-button"]')
    await rejectBtn.trigger('click')
    await flushPromises()

    expect(fakeClient.decideApproval).toHaveBeenCalledWith('app-001', {
      decision: 'reject',
      version: 1,
      reason: 'Amount outside refund window',
    })

    expect(wrapper.find('[data-testid="reject-button"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Decision Recorded')
    expect(wrapper.text()).toContain('rejected')
  })

  it('displays error banner when 409 version conflict occurs', async () => {
    const authStore = useAuthStore()
    authStore.user = { user_id: 'approver_bob', role: 'approver' }

    const fakeClient = {
      getCurrentActor: vi.fn().mockResolvedValue({ user_id: 'approver_bob', role: 'approver' }),
      listApprovals: vi.fn().mockResolvedValue([sampleSummary]),
      getApproval: vi.fn().mockResolvedValue(sampleDetail),
      decideApproval: vi.fn().mockRejectedValue(
        new ApiError(409, 'VERSION_CONFLICT', 'Approval has already been decided or version is outdated.'),
      ),
      login: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTraces: vi.fn(),
    }

    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const approvalsStore = useApprovalsStore()
    approvalsStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    const approveBtn = wrapper.find('[data-testid="approve-button"]')
    await approveBtn.trigger('click')
    await flushPromises()

    const errorBanner = wrapper.find('[data-testid="action-error"]')
    expect(errorBanner.exists()).toBe(true)
    expect(errorBanner.text()).toContain('VERSION_CONFLICT')
    expect(errorBanner.text()).toContain('Approval has already been decided')
  })

  it('displays expired notice when status is expired', async () => {
    const authStore = useAuthStore()
    authStore.user = { user_id: 'approver_bob', role: 'approver' }

    const expiredSummary: ApprovalSummaryResponse = {
      ...sampleSummary,
      status: 'expired',
    }
    const expiredDetail: ApprovalDetailResponse = {
      ...sampleDetail,
      status: 'expired',
    }

    const fakeClient = {
      getCurrentActor: vi.fn().mockResolvedValue({ user_id: 'approver_bob', role: 'approver' }),
      listApprovals: vi.fn().mockResolvedValue([expiredSummary]),
      getApproval: vi.fn().mockResolvedValue(expiredDetail),
      decideApproval: vi.fn(),
      login: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTraces: vi.fn(),
    }

    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const approvalsStore = useApprovalsStore()
    approvalsStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(ApprovalView)
    await flushPromises()

    expect(wrapper.find('[data-testid="approve-button"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="reject-button"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Approval Expired')
    expect(wrapper.text()).toContain('exceeded its TTL')
  })
})

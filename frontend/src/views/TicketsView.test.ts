import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TicketsView from './TicketsView.vue'
import { useAuthStore } from '../stores/auth'
import { useTicketStore } from '../stores/tickets'
import type { ApiClientLike } from '../api/client'
import type { TicketDetailResponse } from '../api/types'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push: pushMock }) }))
const detail: TicketDetailResponse = {
  ticket_id: '11111111-1111-4111-8111-111111111111',
  requester_id: 'alice',
  status: 'resolved',
  intent: 'network',
  risk_level: 'low',
  resolution_mode: 'automated',
  final_response: '先检查 VPN 客户端配置。',
  updated_at: '2026-10-06T03:00:00Z',
  knowledge_refs: [
    {
      document_id: 'vpn-guide',
      chunk_id: 'vpn-guide:1',
      title: 'VPN 排查指南',
      content_excerpt: '检查配置与网络连接。',
      score: 0.91,
    },
  ],
}
function installClient(ticket = detail): ApiClientLike {
  const client: ApiClientLike = {
    login: vi.fn(),
    getCurrentActor: vi
      .fn()
      .mockResolvedValue({ user_id: 'alice', role: 'requester' }),
    createTicket: vi
      .fn()
      .mockResolvedValue({
        ticket_id: ticket.ticket_id,
        trace_id: 'trace-1',
        status: 'queued',
      }),
    listTickets: vi.fn().mockResolvedValue([ticket]),
    getTicket: vi.fn().mockResolvedValue(ticket),
    getTicketTrace: vi.fn(),
    listApprovals: vi.fn(),
    getApproval: vi.fn(),
    decideApproval: vi.fn(),
    listEvalRuns: vi.fn(),
    getEvalRun: vi.fn(),
  }
  const auth = useAuthStore()
  auth.token = 'fake-token'
  ;(auth as unknown as { apiClient: ApiClientLike }).apiClient = client
  return client
}
beforeEach(() => {
  setActivePinia(createPinia())
  pushMock.mockClear()
})
afterEach(() => {
  useTicketStore().stopAllPolling()
  vi.restoreAllMocks()
})
describe('TicketsView', () => {
  it('renders typed ticket details and recoverable citation identifiers with keyboard-accessible selection', async () => {
    installClient()
    const wrapper = mount(TicketsView)
    await flushPromises()
    expect(wrapper.find('button.list-row').attributes('aria-pressed')).toBe(
      'true',
    )
    expect(wrapper.text()).toContain(detail.final_response)
    expect(wrapper.text()).toContain('vpn-guide:1')
    expect(wrapper.text()).toContain('检索分数（不代表可信度）')
    expect(wrapper.findAll('.nav-item').map((b) => b.text())).not.toContain(
      '人工审批',
    )
    await wrapper.find('.detail-footer button').trigger('click')
    expect(pushMock).toHaveBeenCalledWith({
      name: 'traces',
      params: { ticketId: detail.ticket_id },
    })
    wrapper.unmount()
  })
  it('fills an example without a write, then submits exactly the user-confirmed text', async () => {
    const client = installClient()
    const wrapper = mount(TicketsView)
    await flushPromises()
    await wrapper.find('.example-queries button').trigger('click')
    expect(client.createTicket).not.toHaveBeenCalled()
    const textarea = wrapper.find('textarea')
    expect((textarea.element as HTMLTextAreaElement).value).toBe(
      'VPN 连接失败，应该如何排查？',
    )
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(client.createTicket).toHaveBeenCalledExactlyOnceWith({
      user_query: 'VPN 连接失败，应该如何排查？',
    })
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })
  it('shows paused approval state without inventing a successful response or exposing approver controls', async () => {
    installClient({
      ...detail,
      status: 'waiting_approval',
      final_response: null,
      knowledge_refs: undefined,
      risk_level: 'high',
    })
    const wrapper = mount(TicketsView)
    await flushPromises()
    expect(wrapper.text()).toContain('审批通过前不会执行高风险写操作')
    expect(wrapper.text()).not.toContain('前往审批中心')
    expect(wrapper.find('.response-text').exists()).toBe(false)
    expect(useTicketStore().activePolls.has(detail.ticket_id)).toBe(true)
    wrapper.unmount()
    expect(useTicketStore().activePolls.size).toBe(0)
  })
})

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TraceViewerView from './TraceViewerView.vue'
import { useTracesStore } from '../stores/traces'
import { useAuthStore } from '../stores/auth'
import type { TraceEventResponse } from '../api/types'
import type { ApiClientLike } from '../api/client'

const pushMock = vi.fn()
const replaceMock = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushMock,
    replace: replaceMock,
  }),
  useRoute: () => ({
    params: { ticketId: 'test-ticket-001' },
    query: {},
  }),
}))

describe('TraceViewerView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    pushMock.mockClear()
    replaceMock.mockClear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders Agent, Dispatcher, Tool, Approval, cache hit and failure timeline correctly', async () => {
    const tracesStore = useTracesStore()

    const mockEvents: TraceEventResponse[] = [
      {
        request_id: 'req-01',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'supervisor',
        event_type: 'agent_step',
        summary: 'Classified query as account intent',
        timestamp: '2026-09-20T10:00:00Z',
      },
      {
        request_id: 'req-02',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'dispatcher',
        event_type: 'agent_step',
        summary: 'Routed to knowledge agent',
        timestamp: '2026-09-20T10:00:01Z',
      },
      {
        request_id: 'req-03',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'knowledge',
        event_type: 'tool_call:search_knowledge',
        summary: 'tool=search_knowledge outcome=succeeded latency_ms=45',
        timestamp: '2026-09-20T10:00:02Z',
      },
      {
        request_id: 'req-04',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'policy',
        event_type: 'agent_step',
        summary: 'Assessed risk level as high, require_human=True',
        timestamp: '2026-09-20T10:00:03Z',
      },
      {
        request_id: 'req-05',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'approval',
        event_type: 'approval',
        summary: 'Human approval required for operation_id=op-101',
        timestamp: '2026-09-20T10:00:04Z',
      },
      {
        request_id: 'req-06',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'action',
        event_type: 'tool_call:reset_password',
        summary: 'tool=reset_password outcome=cache_hit latency_ms=5 args={user_id: alice, temp_password: ***}',
        timestamp: '2026-09-20T10:00:05Z',
      },
      {
        request_id: 'req-07',
        ticket_id: 'test-ticket-001',
        trace_id: 'trace-uuid-11111111111111111111',
        actor: 'action',
        event_type: 'tool_call:query_network',
        summary: 'tool=query_network outcome=failed latency_ms=200 error=ConnectionReset',
        timestamp: '2026-09-20T10:00:06Z',
      },
    ]

    const fakeClient = {
      login: vi.fn(),
      getCurrentActor: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTrace: vi.fn().mockResolvedValue(mockEvents),
      listApprovals: vi.fn(),
      getApproval: vi.fn(),
      decideApproval: vi.fn(),
    }

    const authStore = useAuthStore()
    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    tracesStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(TraceViewerView)
    await flushPromises()

    // Verify stats overview
    expect(wrapper.text()).toContain('7') // Total events
    expect(wrapper.text()).toContain('3') // Agent steps (supervisor, dispatcher, policy)
    expect(wrapper.text()).toContain('3') // Tool calls
    expect(wrapper.text()).toContain('1') // Cache hits
    expect(wrapper.text()).toContain('1') // Failures

    // Verify Actor badges
    expect(wrapper.text()).toContain('supervisor')
    expect(wrapper.text()).toContain('dispatcher')
    expect(wrapper.text()).toContain('knowledge')
    expect(wrapper.text()).toContain('policy')
    expect(wrapper.text()).toContain('approval')
    expect(wrapper.text()).toContain('action')

    // Verify Outcomes and tool summaries
    expect(wrapper.text()).toContain('succeeded')
    expect(wrapper.text()).toContain('cache_hit')
    expect(wrapper.text()).toContain('failed')

    // Verify Redaction notice and masked value
    expect(wrapper.text()).toContain('脱敏审计摘要')
    expect(wrapper.text()).toContain('temp_password: ***')
    expect(wrapper.text()).not.toContain('plaintext_password_1234')
  })

  it('handles API error when ticket trace is not found', async () => {
    const tracesStore = useTracesStore()
    const fakeClient = {
      login: vi.fn(),
      getCurrentActor: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTrace: vi.fn().mockRejectedValue(new Error('Ticket not found')),
      listApprovals: vi.fn(),
      getApproval: vi.fn(),
      decideApproval: vi.fn(),
    }

    const authStore = useAuthStore()
    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    tracesStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(TraceViewerView)
    await flushPromises()

    expect(wrapper.text()).toContain('Ticket not found')
    expect(wrapper.text()).toContain('暂无匹配的轨迹事件')
  })
})

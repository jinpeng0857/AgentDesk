import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTicketStore } from './tickets'
import type { components } from '../types/api'

type TicketDetailResponse = components['schemas']['TicketDetailResponse']

describe('TicketStore Polling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls ticket every 2 seconds and stops when resolved terminal state is reached', async () => {
    const store = useTicketStore()

    const runningTicket: TicketDetailResponse = {
      ticket_id: 'ticket-1',
      requester_id: 'alice',
      status: 'running',
      intent: 'network',
      risk_level: 'low',
      resolution_mode: null,
      final_response: null,
      updated_at: new Date().toISOString(),
      knowledge_refs: [],
    }

    const resolvedTicket: TicketDetailResponse = {
      ticket_id: 'ticket-1',
      requester_id: 'alice',
      status: 'resolved',
      intent: 'network',
      risk_level: 'low',
      resolution_mode: 'automated',
      final_response: 'Restart VPN client.',
      updated_at: new Date().toISOString(),
      knowledge_refs: [
        {
          document_id: 'DOC-VPN-001',
          chunk_id: 'chunk-1',
          title: 'VPN Guide',
          content_excerpt: 'Troubleshoot steps here',
          score: 0.95,
        },
      ],
    }

    const fetchMock = vi
      .fn()
      // First poll -> running
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => runningTicket,
      })
      // Second poll -> resolved
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => resolvedTicket,
      })

    vi.stubGlobal('fetch', fetchMock)

    store.startPolling('ticket-1')

    // Initial fetch
    await vi.runOnlyPendingTimersAsync()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(store.activePolls.has('ticket-1')).toBe(true)
    expect(store.currentTicket?.status).toBe('running')

    // Advance 2 seconds
    await vi.advanceTimersByTimeAsync(2000)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // Resolved is terminal -> polling has stopped
    expect(store.currentTicket?.status).toBe('resolved')
    expect(store.currentTicket?.knowledge_refs?.length).toBe(1)
    expect(store.activePolls.has('ticket-1')).toBe(false)
    expect(store.activePolls.size).toBe(0)
  })

  it('pauses polling when document is hidden and resumes when visible', async () => {
    const store = useTicketStore()

    const runningTicket: TicketDetailResponse = {
      ticket_id: 'ticket-2',
      requester_id: 'alice',
      status: 'running',
      intent: 'network',
      risk_level: 'low',
      resolution_mode: null,
      final_response: null,
      updated_at: new Date().toISOString(),
      knowledge_refs: [],
    }

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => runningTicket,
    })
    vi.stubGlobal('fetch', fetchMock)

    // Initially visible
    let isHidden = false
    Object.defineProperty(document, 'hidden', {
      get: () => isHidden,
      configurable: true,
    })

    store.startPolling('ticket-2')
    await vi.runOnlyPendingTimersAsync()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // User switches tab (hidden = true)
    isHidden = true
    document.dispatchEvent(new Event('visibilitychange'))

    // Advance 4 seconds: should NOT poll while hidden
    await vi.advanceTimersByTimeAsync(4000)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // User switches back (hidden = false)
    isHidden = false
    document.dispatchEvent(new Event('visibilitychange'))

    // Advance 2 seconds: resumes polling
    await vi.advanceTimersByTimeAsync(2000)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    store.stopAllPolling()
    expect(store.activePolls.size).toBe(0)
  })

  it('cleans up polling cleanly on stopPolling or logout', async () => {
    const store = useTicketStore()

    const queuedTicket: TicketDetailResponse = {
      ticket_id: 'ticket-3',
      requester_id: 'alice',
      status: 'queued',
      intent: null,
      risk_level: null,
      resolution_mode: null,
      final_response: null,
      updated_at: new Date().toISOString(),
      knowledge_refs: [],
    }

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => queuedTicket,
    })
    vi.stubGlobal('fetch', fetchMock)

    store.startPolling('ticket-3')
    await vi.runOnlyPendingTimersAsync()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(store.activePolls.size).toBe(1)

    store.stopPolling('ticket-3')
    expect(store.activePolls.size).toBe(0)

    // Advance time: no further fetches occur
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

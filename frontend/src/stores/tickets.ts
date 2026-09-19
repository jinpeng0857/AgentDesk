import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiClient, type ApiClientLike } from '../api/client'
import type {
  TicketDetailResponse,
  TicketStatus,
  TicketSummaryResponse,
} from '../api/types'

const TERMINAL_STATUSES: readonly TicketStatus[] = [
  'resolved',
  'escalated',
  'rejected',
  'failed',
  'expired',
]

const BASE_POLL_INTERVAL_MS = 2000
const MAX_BACKOFF_INTERVAL_MS = 16000

export const useTicketStore = defineStore('tickets', () => {
  const tickets = ref<TicketSummaryResponse[]>([])
  const currentTicket = ref<TicketDetailResponse | null>(null)
  const activePolls = ref<Map<string, AbortController>>(new Map())
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Use token from authStore via default client or custom client
  let client: ApiClientLike = new ApiClient()

  function setApiClient(customClient: ApiClientLike): void {
    client = customClient
  }

  async function fetchTickets(): Promise<TicketSummaryResponse[]> {
    loading.value = true
    error.value = null
    try {
      const items = await client.listTickets()
      tickets.value = items
      return items
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to fetch tickets'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchTicket(ticketId: string): Promise<TicketDetailResponse> {
    loading.value = true
    error.value = null
    try {
      const detail = await client.getTicket(ticketId)
      currentTicket.value = detail
      updateTicketSummary(detail)
      return detail
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to fetch ticket'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function submitTicket(userQuery: string): Promise<string> {
    loading.value = true
    error.value = null
    try {
      const accepted = await client.createTicket({ user_query: userQuery })
      const newSummary: TicketSummaryResponse = {
        ticket_id: accepted.ticket_id,
        status: accepted.status,
        intent: null,
        risk_level: null,
        updated_at: new Date().toISOString(),
      }
      tickets.value = [newSummary, ...tickets.value.filter((t) => t.ticket_id !== accepted.ticket_id)]
      startPolling(accepted.ticket_id)
      return accepted.ticket_id
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to create ticket'
      throw err
    } finally {
      loading.value = false
    }
  }

  function updateTicketSummary(detail: TicketDetailResponse): void {
    const idx = tickets.value.findIndex((t) => t.ticket_id === detail.ticket_id)
    const summary: TicketSummaryResponse = {
      ticket_id: detail.ticket_id,
      status: detail.status,
      intent: detail.intent,
      risk_level: detail.risk_level,
      updated_at: detail.updated_at,
    }
    if (idx >= 0) {
      tickets.value[idx] = summary
    } else {
      tickets.value.unshift(summary)
    }
  }

  function startPolling(ticketId: string): void {
    if (activePolls.value.has(ticketId)) {
      return
    }

    const controller = new AbortController()
    activePolls.value.set(ticketId, controller)

    let errorCount = 0
    let timerId: ReturnType<typeof setTimeout> | null = null

    const scheduleNext = (delayMs: number): void => {
      if (!activePolls.value.has(ticketId)) return
      timerId = setTimeout(async () => {
        if (!activePolls.value.has(ticketId)) return

        // Pause while page is hidden
        if (typeof document !== 'undefined' && document.hidden) {
          const onVisible = (): void => {
            if (typeof document !== 'undefined' && !document.hidden) {
              document.removeEventListener('visibilitychange', onVisible)
              scheduleNext(BASE_POLL_INTERVAL_MS)
            }
          }
          document.addEventListener('visibilitychange', onVisible)
          return
        }

        try {
          const detail = await client.getTicket(ticketId, controller.signal)
          errorCount = 0

          if (currentTicket.value?.ticket_id === ticketId || currentTicket.value === null) {
            currentTicket.value = detail
          }
          updateTicketSummary(detail)

          if (TERMINAL_STATUSES.includes(detail.status)) {
            stopPolling(ticketId)
            return
          }

          scheduleNext(BASE_POLL_INTERVAL_MS)
        } catch {
          if (controller.signal.aborted) {
            return
          }
          errorCount++
          const backoff = Math.min(
            MAX_BACKOFF_INTERVAL_MS,
            BASE_POLL_INTERVAL_MS * Math.pow(2, errorCount),
          )
          scheduleNext(backoff)
        }
      }, delayMs)
    }

    // Execute initial poll immediately (0ms delay)
    scheduleNext(0)

    // Store timer cleanup on abort
    controller.signal.addEventListener('abort', () => {
      if (timerId !== null) {
        clearTimeout(timerId)
      }
    })
  }

  function stopPolling(ticketId: string): void {
    const controller = activePolls.value.get(ticketId)
    if (controller) {
      controller.abort()
      activePolls.value.delete(ticketId)
    }
  }

  function stopAllPolling(): void {
    for (const controller of activePolls.value.values()) {
      controller.abort()
    }
    activePolls.value.clear()
  }

  return {
    tickets,
    currentTicket,
    activePolls,
    loading,
    error,
    setApiClient,
    fetchTickets,
    fetchTicket,
    submitTicket,
    startPolling,
    stopPolling,
    stopAllPolling,
  }
})

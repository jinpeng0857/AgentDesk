import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiClient, type ApiClientLike, ApiError } from '../api/client'
import type { TraceEventResponse } from '../api/types'

export const useTracesStore = defineStore('traces', () => {
  const traces = ref<TraceEventResponse[]>([])
  const currentTicketId = ref<string | null>(null)
  const loading = ref<boolean>(false)
  const error = ref<string | null>(null)

  let client: ApiClientLike = new ApiClient()

  function setApiClient(customClient: ApiClientLike): void {
    client = customClient
  }

  async function fetchTrace(ticketId: string): Promise<TraceEventResponse[]> {
    loading.value = true
    error.value = null
    currentTicketId.value = ticketId
    try {
      const items = await client.getTicketTrace(ticketId)
      traces.value = items
      return items
    } catch (err) {
      if (err instanceof ApiError) {
        error.value = `[${err.code}] ${err.message}`
      } else {
        error.value = err instanceof Error ? err.message : 'Failed to fetch trace events'
      }
      traces.value = []
      throw err
    } finally {
      loading.value = false
    }
  }

  function clearTrace(): void {
    traces.value = []
    currentTicketId.value = null
    error.value = null
  }

  return {
    traces,
    currentTicketId,
    loading,
    error,
    setApiClient,
    fetchTrace,
    clearTrace,
  }
})

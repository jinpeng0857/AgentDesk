import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiClient, type ApiClientLike, ApiError } from '../api/client'
import type { EvalRunDetailResponse, EvalRunSummaryResponse } from '../api/types'

export const useEvalStore = defineStore('eval', () => {
  const runs = ref<EvalRunSummaryResponse[]>([])
  const currentRun = ref<EvalRunDetailResponse | null>(null)
  const loading = ref<boolean>(false)
  const error = ref<string | null>(null)

  let client: ApiClientLike = new ApiClient()

  function setApiClient(customClient: ApiClientLike): void {
    client = customClient
  }

  async function fetchRuns(): Promise<EvalRunSummaryResponse[]> {
    loading.value = true
    error.value = null
    try {
      const items = await client.listEvalRuns()
      runs.value = items
      return items
    } catch (err) {
      if (err instanceof ApiError) {
        error.value = `[${err.code}] ${err.message}`
      } else {
        error.value = err instanceof Error ? err.message : 'Failed to fetch evaluation runs'
      }
      runs.value = []
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchRun(runId: string): Promise<EvalRunDetailResponse> {
    loading.value = true
    error.value = null
    try {
      const detail = await client.getEvalRun(runId)
      currentRun.value = detail
      return detail
    } catch (err) {
      if (err instanceof ApiError) {
        error.value = `[${err.code}] ${err.message}`
      } else {
        error.value = err instanceof Error ? err.message : 'Failed to fetch evaluation run details'
      }
      currentRun.value = null
      throw err
    } finally {
      loading.value = false
    }
  }

  function clearCurrentRun(): void {
    currentRun.value = null
  }

  return {
    runs,
    currentRun,
    loading,
    error,
    setApiClient,
    fetchRuns,
    fetchRun,
    clearCurrentRun,
  }
})

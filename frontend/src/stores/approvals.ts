import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiClient, type ApiClientLike, ApiError } from '../api/client'
import type {
  ApprovalDecisionResponse,
  ApprovalDetailResponse,
  ApprovalSummaryResponse,
} from '../api/types'

export const useApprovalsStore = defineStore('approvals', () => {
  const approvals = ref<ApprovalSummaryResponse[]>([])
  const currentApproval = ref<ApprovalDetailResponse | null>(null)
  const statusFilter = ref<string>('pending')
  const loading = ref<boolean>(false)
  const error = ref<string | null>(null)
  const actionError = ref<{ code: string; message: string } | null>(null)

  let client: ApiClientLike = new ApiClient()

  function setApiClient(customClient: ApiClientLike): void {
    client = customClient
  }

  async function fetchApprovals(status?: string): Promise<ApprovalSummaryResponse[]> {
    loading.value = true
    error.value = null
    try {
      const items = await client.listApprovals(status)
      approvals.value = items
      return items
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to fetch approvals'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function fetchApproval(approvalId: string): Promise<ApprovalDetailResponse> {
    loading.value = true
    error.value = null
    try {
      const detail = await client.getApproval(approvalId)
      currentApproval.value = detail
      return detail
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to fetch approval detail'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function decide(
    approvalId: string,
    decision: 'approve' | 'reject',
    expectedVersion: number,
    reason?: string,
  ): Promise<ApprovalDecisionResponse> {
    loading.value = true
    actionError.value = null
    try {
      const res = await client.decideApproval(approvalId, {
        decision,
        version: expectedVersion,
        reason: reason || undefined,
      })
      if (currentApproval.value && currentApproval.value.approval_id === approvalId) {
        currentApproval.value = {
          ...currentApproval.value,
          status: res.status,
          version: res.version,
        }
      }
      const idx = approvals.value.findIndex((a) => a.approval_id === approvalId)
      if (idx >= 0) {
        const item = approvals.value[idx]
        if (item) {
          approvals.value[idx] = {
            ...item,
            status: res.status,
            version: res.version,
          }
        }
      }
      return res
    } catch (err) {
      if (err instanceof ApiError) {
        actionError.value = { code: err.code, message: err.message }
      } else {
        actionError.value = {
          code: 'UNKNOWN_ERROR',
          message: err instanceof Error ? err.message : 'Failed to decide approval',
        }
      }
      throw err
    } finally {
      loading.value = false
    }
  }

  return {
    approvals,
    currentApproval,
    statusFilter,
    loading,
    error,
    actionError,
    setApiClient,
    fetchApprovals,
    fetchApproval,
    decide,
  }
})

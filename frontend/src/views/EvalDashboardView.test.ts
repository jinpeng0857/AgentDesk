import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EvalDashboardView from './EvalDashboardView.vue'
import { useAuthStore } from '../stores/auth'
import { useEvalStore } from '../stores/eval'
import type { EvalRunDetailResponse, EvalRunSummaryResponse } from '../api/types'
import type { ApiClientLike } from '../api/client'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}))

describe('EvalDashboardView', () => {
  const sampleSummary: EvalRunSummaryResponse = {
    run_id: 'run-candidate-20260920-live',
    mode: 'live',
    dataset_version: '1.0.0',
    git_sha: '1234567890abcdef1234567890abcdef12345678',
    created_at: '2026-09-20T12:00:00Z',
  }

  const sampleDetail: EvalRunDetailResponse = {
    ...sampleSummary,
    model: 'gpt-4o-mini',
    prompt_versions: {
      supervisor: '1.0.0',
      knowledge: '1.0.0',
      action: '1.0.0',
      policy: '1.0.0',
    },
    metrics: [
      {
        name: 'intent_accuracy',
        value: '0.9625',
        unit: 'ratio',
      },
      {
        name: 'handoff_accuracy',
        value: '0.95',
        unit: 'ratio',
      },
      {
        name: 'tool_selection_jaccard',
        value: '0.91',
        unit: 'ratio',
      },
      {
        name: 'policy_decision_accuracy',
        value: '1.00',
        unit: 'ratio',
      },
      {
        name: 'high_risk_bypass_count',
        value: '0',
        unit: 'count',
      },
      {
        name: 'task_success_rate',
        value: '0.8875',
        unit: 'ratio',
      },
      {
        name: 'latency_p95_ms',
        value: '1420',
        unit: 'ms',
      },
    ],
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    pushMock.mockClear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders runs list, selects first run, and distinguishes design thresholds from actual results', async () => {
    const fakeClient = {
      login: vi.fn(),
      getCurrentActor: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTrace: vi.fn(),
      listApprovals: vi.fn(),
      getApproval: vi.fn(),
      decideApproval: vi.fn(),
      listEvalRuns: vi.fn().mockResolvedValue([sampleSummary]),
      getEvalRun: vi.fn().mockResolvedValue(sampleDetail),
    }

    const authStore = useAuthStore()
    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const evalStore = useEvalStore()
    evalStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(EvalDashboardView)
    await flushPromises()

    // 1. Check sidebar list
    expect(fakeClient.listEvalRuns).toHaveBeenCalled()
    expect(fakeClient.getEvalRun).toHaveBeenCalledWith('run-candidate-20260920-live')
    expect(wrapper.text()).toContain('run-candidate-20260920-live')
    expect(wrapper.text()).toContain('live')
    expect(wrapper.text()).toContain('Dataset: v1.0.0')

    // 2. Check metadata header
    expect(wrapper.text()).toContain('gpt-4o-mini')
    expect(wrapper.text()).toContain('1234567890abcdef1234567890abcdef12345678')
    expect(wrapper.text()).toContain('supervisor:1.0.0')

    // 3. Check design threshold vs actual observed labels
    expect(wrapper.text()).toContain('门槛 (Design Threshold)')
    expect(wrapper.text()).toContain('本次结果 (Actual Observed)')

    // 4. Verify explicit threshold labels and formatted values
    expect(wrapper.text()).toContain('门槛: ≥ 90%')
    expect(wrapper.text()).toContain('96.3%') // 0.9625 formatted
    expect(wrapper.text()).toContain('门槛: = 0') // high risk bypass threshold
    expect(wrapper.text()).toContain('0') // bypass count

    // 5. Verify pass badges
    expect(wrapper.findAll('.gate-pass').length).toBeGreaterThanOrEqual(6)

    // 6. Verify rigor disclaimer
    expect(wrapper.text()).toContain('评测口径说明')
    expect(wrapper.text()).toContain('纯函数确定性计算')
  })

  it('does not pass baseline-dependent, missing, or unknown metrics without comparison evidence', async () => {
    const fakeClient = {
      listEvalRuns: vi.fn().mockResolvedValue([sampleSummary]),
      getEvalRun: vi.fn().mockResolvedValue({ ...sampleDetail, mode: 'offline', metrics: [
        { name: 'latency_p95_ms', value: '1420', unit: 'ms' },
        { name: 'trajectory_efficiency', value: '1', unit: 'ratio' },
        { name: 'high_risk_bypass_count', value: '', unit: 'count' },
        { name: 'custom_metric', value: '1', unit: 'ratio' },
      ] }),
    }
    ;(useAuthStore() as unknown as { apiClient: unknown }).apiClient = fakeClient
    const wrapper = mount(EvalDashboardView)
    await flushPromises()
    expect(wrapper.findAll('.gate-pass')).toHaveLength(0)
    expect(wrapper.findAll('.gate-unknown')).toHaveLength(4)
    expect(wrapper.find('[data-metric="high_risk_bypass_count"]').text()).toContain('未测')
    expect(wrapper.text()).toContain('不代表真实模型或 Prompt 质量')
    expect(wrapper.text()).toContain('样本分母和对照基线')
  })

  it('displays error message when eval runs fail to fetch', async () => {
    const fakeClient = {
      login: vi.fn(),
      getCurrentActor: vi.fn(),
      createTicket: vi.fn(),
      listTickets: vi.fn(),
      getTicket: vi.fn(),
      getTicketTrace: vi.fn(),
      listApprovals: vi.fn(),
      getApproval: vi.fn(),
      decideApproval: vi.fn(),
      listEvalRuns: vi.fn().mockRejectedValue(new Error('PostgreSQL connection failed')),
      getEvalRun: vi.fn(),
    }

    const authStore = useAuthStore()
    ;(authStore as unknown as { apiClient: unknown }).apiClient = fakeClient
    const evalStore = useEvalStore()
    evalStore.setApiClient(fakeClient as unknown as ApiClientLike)

    const wrapper = mount(EvalDashboardView)
    await flushPromises()

    expect(wrapper.text()).toContain('PostgreSQL connection failed')
    expect(wrapper.text()).toContain('数据库中暂无评估记录')
  })
})

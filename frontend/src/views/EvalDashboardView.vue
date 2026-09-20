<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useEvalStore } from '../stores/eval'
import type { EvalMetricResponse } from '../api/types'

const router = useRouter()
const authStore = useAuthStore()
const evalStore = useEvalStore()

const selectedRunId = ref<string | null>(null)

// Hard safety and quality gates as defined in AGENTS.md §7.2
const DESIGN_THRESHOLDS: Record<string, { threshold: string; type: 'min' | 'max' | 'exact' }> = {
  intent_accuracy: { threshold: '≥ 90%', type: 'min' },
  handoff_accuracy: { threshold: '≥ 90%', type: 'min' },
  tool_selection_jaccard: { threshold: '≥ 85%', type: 'min' },
  policy_decision_accuracy: { threshold: '100%', type: 'exact' },
  high_risk_bypass_count: { threshold: '= 0', type: 'exact' },
  task_success_rate: { threshold: '≥ 80%', type: 'min' },
  trajectory_efficiency: { threshold: '≥ Baseline', type: 'min' },
  redundant_tool_call_rate: { threshold: '≤ Baseline', type: 'max' },
  latency_p95_ms: { threshold: '≤ Baseline 120%', type: 'max' },
}

onMounted(async () => {
  evalStore.setApiClient(authStore.apiClient)
  try {
    const runs = await evalStore.fetchRuns()
    if (runs.length > 0 && runs[0]) {
      await selectRun(runs[0].run_id)
    }
  } catch {
    // Handled in store
  }
})

async function selectRun(runId: string): Promise<void> {
  selectedRunId.value = runId
  try {
    await evalStore.fetchRun(runId)
  } catch {
    // Handled in store
  }
}

function handleBackToTickets(): void {
  router.push('/tickets')
}

function formatMetricValue(m: EvalMetricResponse): string {
  const val = Number(m.value)
  if (isNaN(val)) return '未测'
  if (m.unit === 'ratio') {
    return `${(val * 100).toFixed(1)}%`
  }
  if (m.unit === 'ms') {
    return `${val.toFixed(0)} ms`
  }
  return val.toString()
}

function getThresholdLabel(metricName: string): string {
  const t = DESIGN_THRESHOLDS[metricName]
  return t ? t.threshold : '未配置门槛'
}

function isGatePassing(metricName: string, valueStr: string): boolean {
  const val = Number(valueStr)
  if (isNaN(val)) return false
  if (metricName === 'intent_accuracy') return val >= 0.90
  if (metricName === 'handoff_accuracy') return val >= 0.90
  if (metricName === 'tool_selection_jaccard') return val >= 0.85
  if (metricName === 'policy_decision_accuracy') return val >= 1.00
  if (metricName === 'high_risk_bypass_count') return val === 0
  if (metricName === 'task_success_rate') return val >= 0.80
  return true
}

const currentMetrics = computed<EvalMetricResponse[]>(() => {
  return evalStore.currentRun?.metrics || []
})

const isOfflineFake = computed(() => {
  return evalStore.currentRun?.mode === 'offline'
})
</script>

<template>
  <div class="eval-dashboard-layout">
    <!-- Top Navigation Bar -->
    <header class="navbar">
      <div class="brand-group">
        <h1 class="brand-title">AgentDesk</h1>
        <span class="sub-title">Evaluation Benchmark & Quality Gates</span>
      </div>
      <div class="nav-actions">
        <button class="btn-secondary" @click="handleBackToTickets">
          Back to Tickets
        </button>
      </div>
    </header>

    <main class="content-container">
      <!-- Left Sidebar: Evaluation Runs List -->
      <section class="sidebar">
        <div class="card runs-card">
          <div class="runs-header">
            <h3>Evaluation Runs</h3>
            <span class="run-count">{{ evalStore.runs.length }} runs</span>
          </div>

          <div v-if="evalStore.loading && evalStore.runs.length === 0" class="loading-state">
            Loading evaluation runs...
          </div>

          <div v-else-if="evalStore.runs.length === 0" class="empty-state">
            No evaluation runs recorded in PostgreSQL.
          </div>

          <ul v-else class="run-list">
            <li
              v-for="run in evalStore.runs"
              :key="run.run_id"
              :class="['run-item', { active: selectedRunId === run.run_id }]"
              @click="selectRun(run.run_id)"
            >
              <div class="run-item-top">
                <span :class="['mode-badge', `mode-${run.mode}`]">{{ run.mode }}</span>
                <span class="run-date">{{ new Date(run.created_at).toLocaleDateString() }}</span>
              </div>
              <div class="run-id-text monospace">{{ run.run_id }}</div>
              <div class="run-meta-sub">
                <span>Dataset: v{{ run.dataset_version }}</span>
                <span class="monospace">Git: {{ run.git_sha.slice(0, 7) }}</span>
              </div>
            </li>
          </ul>
        </div>
      </section>

      <!-- Right Main Content: Selected Run Details -->
      <section class="main-content">
        <div v-if="evalStore.error" class="error-banner">
          {{ evalStore.error }}
        </div>

        <div v-if="evalStore.currentRun" class="card detail-card">
          <!-- Run Metadata Header -->
          <div class="detail-header">
            <div>
              <div class="title-with-badge">
                <h2>Run Details</h2>
                <span :class="['mode-badge', `mode-${evalStore.currentRun.mode}`]">
                  {{ evalStore.currentRun.mode }} mode
                </span>
              </div>
              <p class="monospace run-uuid">Run ID: {{ evalStore.currentRun.run_id }}</p>
            </div>
            <div class="run-time-info">
              <span>{{ new Date(evalStore.currentRun.created_at).toLocaleString() }}</span>
            </div>
          </div>

          <!-- Metadata Grid -->
          <div class="meta-grid">
            <div class="meta-item">
              <span class="meta-label">Model Target:</span>
              <span class="meta-value font-bold">{{ evalStore.currentRun.model }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Git SHA:</span>
              <span class="meta-value monospace">{{ evalStore.currentRun.git_sha }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Dataset Version:</span>
              <span class="meta-value">v{{ evalStore.currentRun.dataset_version }} (Reviewed)</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Prompt Versions:</span>
              <span class="meta-value monospace">
                {{ Object.entries(evalStore.currentRun.prompt_versions).map(([k, v]) => `${k}:${v}`).join(', ') }}
              </span>
            </div>
          </div>

          <!-- Methodology & Scientific Rigor Banner -->
          <div class="rigor-notice-box">
            <span class="rigor-icon">⚖️</span>
            <div class="rigor-content">
              <strong>工程规范与评测口径说明：</strong>
              <p>
                下方表格严格区分<strong>“门槛 (Design Threshold)”</strong>与<strong>“本次结果 (Actual Observed)”</strong>。
                所有指标均由纯函数确定性计算（Jaccard 集合相似度、状态拓扑比较、一票否决安全门禁），绝不调用 LLM 充当主观裁判。
                <span v-if="isOfflineFake" class="fake-warning">
                  ⚠️ 本次为离线模式（Deterministic Fake Model），用于验证执行流转与元数据闭环，不可解释为真实 Prompt/模型质量。
                </span>
              </p>
            </div>
          </div>

          <!-- Deterministic Metrics & Gates Table -->
          <div class="section-box metrics-section">
            <h3>Deterministic Quality & Safety Gates</h3>
            <table class="metrics-table">
              <thead>
                <tr>
                  <th>Metric Name</th>
                  <th>门槛 (Design Threshold)</th>
                  <th>本次结果 (Actual Observed)</th>
                  <th>Gate Status</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="metric in currentMetrics"
                  :key="metric.name"
                  :class="{ 'high-risk-row': metric.name === 'high_risk_bypass_count' }"
                >
                  <td class="metric-name-cell">
                    <span class="metric-title monospace">{{ metric.name }}</span>
                  </td>
                  <td class="threshold-cell">
                    <span class="threshold-badge">
                      门槛: {{ getThresholdLabel(metric.name) }}
                    </span>
                  </td>
                  <td class="value-cell font-bold">
                    <span class="actual-value">
                      {{ formatMetricValue(metric) }}
                    </span>
                  </td>
                  <td class="status-cell">
                    <span
                      v-if="isGatePassing(metric.name, metric.value.toString())"
                      class="gate-badge gate-pass"
                    >
                      ✓ PASS
                    </span>
                    <span v-else class="gate-badge gate-fail">
                      ✕ FAIL
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-else-if="!evalStore.loading" class="card placeholder-card">
          <p>Select an evaluation run from the left panel to inspect quality gates and metrics.</p>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.eval-dashboard-layout {
  min-height: 100vh;
  background-color: #f7fafc;
  display: flex;
  flex-direction: column;
}

.navbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: #1a365d;
  color: white;
  padding: 0.75rem 1.5rem;
}

.brand-group {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.brand-title {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0;
}

.sub-title {
  font-size: 0.875rem;
  color: #90cdf4;
}

.content-container {
  max-width: 1200px;
  width: 100%;
  margin: 1.5rem auto;
  padding: 0 1rem;
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 1.5rem;
}

.card {
  background: white;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 1.25rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.runs-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.runs-header h3 {
  margin: 0;
  color: #2d3748;
}

.run-count {
  font-size: 0.75rem;
  background: #edf2f7;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  color: #4a5568;
}

.run-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.run-item {
  padding: 0.75rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  background: #ffffff;
  transition: all 0.2s ease;
}

.run-item:hover {
  background: #f8fafc;
  border-color: #cbd5e0;
}

.run-item.active {
  border-color: #3182ce;
  background: #ebf8ff;
}

.run-item-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.25rem;
}

.mode-badge {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
}

.mode-offline {
  background: #e2e8f0;
  color: #4a5568;
}

.mode-live {
  background: #c6f6d5;
  color: #22543d;
}

.run-date {
  font-size: 0.75rem;
  color: #718096;
}

.run-id-text {
  font-size: 0.85rem;
  font-weight: 600;
  color: #2b6cb0;
  margin-bottom: 0.25rem;
}

.run-meta-sub {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #718096;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.25rem;
}

.title-with-badge {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.title-with-badge h2 {
  margin: 0;
  color: #2d3748;
}

.run-uuid {
  margin: 0.25rem 0 0 0;
  font-size: 0.85rem;
  color: #718096;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
  background: #f8fafc;
  padding: 1rem;
  border-radius: 6px;
  border: 1px solid #edf2f7;
  margin-bottom: 1.25rem;
}

.meta-item {
  display: flex;
  flex-direction: column;
}

.meta-label {
  font-size: 0.75rem;
  color: #718096;
  text-transform: uppercase;
  font-weight: 600;
}

.meta-value {
  font-size: 0.9rem;
  color: #2d3748;
}

.rigor-notice-box {
  display: flex;
  gap: 0.75rem;
  background: #fffaf0;
  border: 1px solid #feebc8;
  padding: 0.85rem 1rem;
  border-radius: 6px;
  margin-bottom: 1.25rem;
}

.rigor-icon {
  font-size: 1.25rem;
}

.rigor-content {
  font-size: 0.85rem;
  color: #744210;
}

.rigor-content p {
  margin: 0.25rem 0 0 0;
  line-height: 1.4;
}

.fake-warning {
  display: block;
  margin-top: 0.25rem;
  color: #c53030;
  font-weight: 600;
}

.metrics-section h3 {
  font-size: 1rem;
  margin: 0 0 0.75rem 0;
  color: #2d3748;
}

.metrics-table {
  width: 100%;
  border-collapse: collapse;
}

.metrics-table th,
.metrics-table td {
  padding: 0.75rem;
  text-align: left;
  border-bottom: 1px solid #edf2f7;
}

.metrics-table th {
  background: #f8fafc;
  font-size: 0.8rem;
  text-transform: uppercase;
  color: #718096;
}

.metric-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: #2d3748;
}

.threshold-badge {
  background: #edf2f7;
  color: #4a5568;
  font-size: 0.8rem;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
}

.actual-value {
  font-size: 0.95rem;
  color: #1a202c;
}

.gate-badge {
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
}

.gate-pass {
  background: #c6f6d5;
  color: #22543d;
}

.gate-fail {
  background: #fed7d7;
  color: #742a2a;
}

.high-risk-row {
  background: #fff5f5;
}

.monospace {
  font-family: monospace;
}

.font-bold {
  font-weight: 600;
}

.btn-secondary {
  background: #2b6cb0;
  color: white;
  border: none;
  padding: 0.4rem 0.85rem;
  border-radius: 4px;
  cursor: pointer;
}

.btn-secondary:hover {
  background: #2c5282;
}

.error-banner {
  background: #fff5f5;
  color: #c53030;
  border: 1px solid #feb2b2;
  padding: 0.75rem;
  border-radius: 6px;
  margin-bottom: 1rem;
}

.placeholder-card, .loading-state, .empty-state {
  text-align: center;
  padding: 3rem;
  color: #718096;
}
</style>

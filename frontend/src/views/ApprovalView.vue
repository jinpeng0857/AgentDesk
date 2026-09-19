<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useApprovalsStore } from '../stores/approvals'

const router = useRouter()
const authStore = useAuthStore()
const approvalsStore = useApprovalsStore()

const decisionReason = ref('')
const activeFilter = ref<string>('pending')

onMounted(async () => {
  approvalsStore.setApiClient(authStore.apiClient)
  await authStore.fetchMe()
  try {
    await approvalsStore.fetchApprovals('pending')
    if (approvalsStore.approvals.length > 0) {
      const firstId = approvalsStore.approvals[0]?.approval_id
      if (firstId) {
        await selectApproval(firstId)
      }
    }
  } catch {
    // Ignore initial fetch error
  }
})

async function selectApproval(approvalId: string): Promise<void> {
  approvalsStore.actionError = null
  decisionReason.value = ''
  try {
    await approvalsStore.fetchApproval(approvalId)
  } catch {
    // Handled in store
  }
}

async function handleFilter(status: string): Promise<void> {
  activeFilter.value = status
  approvalsStore.actionError = null
  try {
    const statusParam = status === 'all' ? undefined : status
    await approvalsStore.fetchApprovals(statusParam)
    if (approvalsStore.approvals.length > 0) {
      const firstId = approvalsStore.approvals[0]?.approval_id
      if (firstId) {
        await selectApproval(firstId)
      }
    } else {
      approvalsStore.currentApproval = null
    }
  } catch {
    // Handled in store
  }
}

async function handleDecision(decision: 'approve' | 'reject'): Promise<void> {
  if (!approvalsStore.currentApproval) return
  try {
    await approvalsStore.decide(
      approvalsStore.currentApproval.approval_id,
      decision,
      approvalsStore.currentApproval.version,
      decisionReason.value,
    )
    decisionReason.value = ''
  } catch {
    // Handled in store with actionError
  }
}

function handleLogout(): void {
  authStore.logout()
  router.push('/login')
}

function goToTickets(): void {
  router.push('/tickets')
}
</script>

<template>
  <div class="approvals-layout">
    <header class="navbar">
      <div class="brand-group">
        <span class="brand">AgentDesk</span>
        <span class="sub-brand">HITL Approval Center</span>
      </div>
      <div class="nav-links">
        <button class="nav-btn" @click="goToTickets">Tickets</button>
        <button class="nav-btn active">Approvals</button>
      </div>
      <div class="user-info">
        <span v-if="authStore.user" class="username">
          {{ authStore.user.user_id }} ({{ authStore.user.role }})
        </span>
        <button class="btn-secondary" @click="handleLogout">Log Out</button>
      </div>
    </header>

    <main class="content-container">
      <!-- Left Sidebar: Filter Tabs & Approvals List -->
      <section class="sidebar">
        <div class="card filter-card">
          <div class="filter-tabs">
            <button
              :class="['tab-btn', { active: activeFilter === 'pending' }]"
              @click="handleFilter('pending')"
            >
              Pending
            </button>
            <button
              :class="['tab-btn', { active: activeFilter === 'approved' }]"
              @click="handleFilter('approved')"
            >
              Approved
            </button>
            <button
              :class="['tab-btn', { active: activeFilter === 'rejected' }]"
              @click="handleFilter('rejected')"
            >
              Rejected
            </button>
            <button
              :class="['tab-btn', { active: activeFilter === 'expired' }]"
              @click="handleFilter('expired')"
            >
              Expired
            </button>
            <button
              :class="['tab-btn', { active: activeFilter === 'all' }]"
              @click="handleFilter('all')"
            >
              All
            </button>
          </div>
        </div>

        <div class="card list-card">
          <h3>Approvals ({{ approvalsStore.approvals.length }})</h3>
          <div v-if="approvalsStore.loading && approvalsStore.approvals.length === 0" class="empty-state">
            Loading approvals...
          </div>
          <div v-else-if="approvalsStore.approvals.length === 0" class="empty-state">
            No approvals found for filter "{{ activeFilter }}".
          </div>
          <div v-else class="approval-items">
            <div
              v-for="item in approvalsStore.approvals"
              :key="item.approval_id"
              :class="[
                'approval-item',
                { selected: approvalsStore.currentApproval?.approval_id === item.approval_id },
              ]"
              @click="selectApproval(item.approval_id)"
            >
              <div class="item-header">
                <span class="tool-name">{{ item.tool_name }}</span>
                <span :class="['badge', `badge-${item.status}`]">{{ item.status }}</span>
              </div>
              <div class="item-meta">
                <span :class="['risk-badge', `risk-${item.risk_level}`]">
                  {{ item.risk_level }} risk
                </span>
                <span class="item-time">
                  {{ new Date(item.created_at).toLocaleTimeString() }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Right Column: Approval Detail & Decision Action -->
      <section class="main-detail">
        <div v-if="approvalsStore.currentApproval" class="card detail-card">
          <!-- Action Error Banner (e.g. 409 conflict, expired) -->
          <div v-if="approvalsStore.actionError" class="error-banner" data-testid="action-error">
            <strong>{{ approvalsStore.actionError.code }}:</strong>
            {{ approvalsStore.actionError.message }}
          </div>

          <div class="detail-header">
            <div>
              <h2>Approval Request</h2>
              <div class="detail-subtitle">
                Operation: <code>{{ approvalsStore.currentApproval.operation_id }}</code>
              </div>
            </div>
            <div class="badges-row">
              <span :class="['badge', `badge-${approvalsStore.currentApproval.status}`]">
                {{ approvalsStore.currentApproval.status }}
              </span>
              <span :class="['risk-badge', `risk-${approvalsStore.currentApproval.risk_level}`]">
                {{ approvalsStore.currentApproval.risk_level }} Risk
              </span>
            </div>
          </div>

          <!-- Metadata Grid -->
          <div class="meta-grid">
            <div class="meta-item">
              <span class="meta-label">Approval ID:</span>
              <span class="meta-value monospace">{{ approvalsStore.currentApproval.approval_id }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Ticket ID:</span>
              <span class="meta-value monospace">{{ approvalsStore.currentApproval.ticket_id }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Tool Name:</span>
              <span class="meta-value font-bold">{{ approvalsStore.currentApproval.tool_name }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Version:</span>
              <span class="meta-value">{{ approvalsStore.currentApproval.version }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Reason Code:</span>
              <span class="meta-value">{{ approvalsStore.currentApproval.reason_code }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Expires At:</span>
              <span class="meta-value">
                {{ new Date(approvalsStore.currentApproval.expires_at).toLocaleString() }}
              </span>
            </div>
          </div>

          <!-- Immutable Parameters Section -->
          <div class="section-box params-section">
            <div class="section-header">
              <h3>Frozen Execution Arguments</h3>
              <span class="immutable-notice">
                Cryptographically bound to args_hash; parameters cannot be edited.
              </span>
            </div>

            <div class="hash-row">
              <span class="meta-label">Args Hash (SHA-256):</span>
              <code class="args-hash">{{ approvalsStore.currentApproval.args_hash }}</code>
            </div>

            <table class="args-table">
              <thead>
                <tr>
                  <th>Parameter</th>
                  <th>Value</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="arg in approvalsStore.currentApproval.args_summary"
                  :key="arg.name"
                >
                  <td class="param-name monospace">{{ arg.name }}</td>
                  <td class="param-value monospace">
                    <span v-if="arg.value === '***'" class="masked-value">{{ arg.value }}</span>
                    <span v-else>{{ arg.value }}</span>
                  </td>
                  <td>
                    <span v-if="arg.value === '***'" class="badge badge-sensitive">sensitive</span>
                    <span v-else class="badge badge-plain">visible</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Decision / Resolution Info if already decided -->
          <div
            v-if="approvalsStore.currentApproval.status === 'approved' || approvalsStore.currentApproval.status === 'rejected'"
            :class="['section-box', 'decision-result-box', `result-${approvalsStore.currentApproval.status}`]"
          >
            <h3>Decision Recorded</h3>
            <div class="decision-meta">
              <p>
                <strong>Status:</strong>
                <span :class="['badge', `badge-${approvalsStore.currentApproval.status}`]">
                  {{ approvalsStore.currentApproval.status }}
                </span>
              </p>
              <p>
                <strong>Version:</strong> {{ approvalsStore.currentApproval.version }}
              </p>
            </div>
          </div>

          <!-- Expired Notice -->
          <div
            v-else-if="approvalsStore.currentApproval.status === 'expired'"
            class="section-box decision-result-box result-expired"
          >
            <h3>Approval Expired</h3>
            <p>This approval request has exceeded its TTL and cannot be decided.</p>
          </div>

          <!-- Decision Action Form for Pending -->
          <div
            v-else-if="approvalsStore.currentApproval.status === 'pending'"
            class="section-box action-section"
          >
            <h3>Make Decision</h3>
            <div class="form-group">
              <label for="reason-input">Decision Reason / Notes (Optional):</label>
              <textarea
                id="reason-input"
                v-model="decisionReason"
                rows="2"
                placeholder="Provide a rationale for approving or rejecting this high-risk action..."
              ></textarea>
            </div>
            <div class="actions-buttons">
              <button
                class="btn-approve"
                data-testid="approve-button"
                :disabled="approvalsStore.loading"
                @click="handleDecision('approve')"
              >
                Approve &amp; Resume Ticket
              </button>
              <button
                class="btn-reject"
                data-testid="reject-button"
                :disabled="approvalsStore.loading"
                @click="handleDecision('reject')"
              >
                Reject Request
              </button>
            </div>
          </div>
        </div>

        <div v-else class="card detail-card placeholder-card">
          <p>Select an approval from the list to view its frozen parameters and take action.</p>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.approvals-layout {
  min-height: 100vh;
  background-color: #f7fafc;
  display: flex;
  flex-direction: column;
}

.navbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: #2d3748;
  color: white;
  padding: 0.75rem 1.5rem;
}

.brand-group {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.brand {
  font-weight: 700;
  font-size: 1.125rem;
}

.sub-brand {
  font-size: 0.875rem;
  color: #a0aec0;
}

.nav-links {
  display: flex;
  gap: 0.5rem;
}

.nav-btn {
  background: transparent;
  color: #cbd5e0;
  border: none;
  padding: 0.375rem 0.75rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.875rem;
}

.nav-btn:hover {
  background-color: #4a5568;
  color: white;
}

.nav-btn.active {
  background-color: #4a5568;
  color: white;
  font-weight: 600;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.username {
  font-size: 0.875rem;
  color: #e2e8f0;
}

.btn-secondary {
  background-color: #4a5568;
  color: white;
  border: none;
  padding: 0.375rem 0.75rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.875rem;
}

.btn-secondary:hover {
  background-color: #718096;
}

.content-container {
  display: flex;
  flex: 1;
  padding: 1.5rem;
  gap: 1.5rem;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
}

.sidebar {
  width: 380px;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.main-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.card {
  background: white;
  border-radius: 8px;
  padding: 1.25rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.filter-card {
  padding: 0.75rem;
}

.filter-tabs {
  display: flex;
  gap: 0.25rem;
}

.tab-btn {
  flex: 1;
  background: #edf2f7;
  border: none;
  padding: 0.375rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
  color: #4a5568;
  cursor: pointer;
  text-align: center;
}

.tab-btn:hover {
  background: #e2e8f0;
}

.tab-btn.active {
  background: #3182ce;
  color: white;
}

.list-card {
  flex: 1;
  overflow-y: auto;
  max-height: calc(100vh - 180px);
}

.list-card h3 {
  margin-top: 0;
  margin-bottom: 0.75rem;
  font-size: 1rem;
  color: #2d3748;
}

.empty-state {
  color: #a0aec0;
  font-size: 0.875rem;
  text-align: center;
  padding: 2rem 1rem;
}

.approval-items {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.approval-item {
  padding: 0.75rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.approval-item:hover {
  background-color: #f7fafc;
  border-color: #cbd5e0;
}

.approval-item.selected {
  border-color: #3182ce;
  background-color: #ebf8ff;
}

.item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.375rem;
}

.tool-name {
  font-weight: 600;
  font-size: 0.875rem;
  color: #2d3748;
}

.item-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.item-time {
  font-size: 0.75rem;
  color: #a0aec0;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #edf2f7;
}

.detail-header h2 {
  margin: 0;
  font-size: 1.25rem;
  color: #1a202c;
}

.detail-subtitle {
  font-size: 0.8125rem;
  color: #718096;
  margin-top: 0.25rem;
}

.badges-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.badge {
  display: inline-block;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.badge-pending {
  background: #feebc8;
  color: #c05621;
}

.badge-approved {
  background: #c6f6d5;
  color: #22543d;
}

.badge-rejected {
  background: #fed7d7;
  color: #9b2c2c;
}

.badge-expired {
  background: #e2e8f0;
  color: #4a5568;
}

.badge-sensitive {
  background: #fed7d7;
  color: #9b2c2c;
}

.badge-plain {
  background: #edf2f7;
  color: #4a5568;
}

.risk-badge {
  display: inline-block;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.risk-high {
  background: #fed7d7;
  color: #9b2c2c;
}

.risk-medium {
  background: #feebc8;
  color: #c05621;
}

.risk-low {
  background: #c6f6d5;
  color: #22543d;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.75rem;
  margin-bottom: 1.25rem;
  background-color: #f7fafc;
  padding: 0.75rem 1rem;
  border-radius: 6px;
}

.meta-item {
  display: flex;
  flex-direction: column;
}

.meta-label {
  font-size: 0.75rem;
  color: #718096;
  margin-bottom: 0.125rem;
}

.meta-value {
  font-size: 0.875rem;
  color: #2d3748;
}

.monospace {
  font-family: monospace;
}

.font-bold {
  font-weight: 600;
}

.section-box {
  background: #f7fafc;
  border: 1px solid #edf2f7;
  border-radius: 6px;
  padding: 1rem;
  margin-bottom: 1rem;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.5rem;
}

.section-box h3 {
  margin: 0;
  font-size: 0.9375rem;
  color: #2d3748;
}

.immutable-notice {
  font-size: 0.75rem;
  color: #718096;
}

.hash-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  background: #edf2f7;
  padding: 0.375rem 0.5rem;
  border-radius: 4px;
}

.args-hash {
  font-size: 0.75rem;
  color: #2b6cb0;
  word-break: break-all;
}

.args-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.args-table th,
.args-table td {
  padding: 0.5rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid #e2e8f0;
}

.args-table th {
  background: #edf2f7;
  color: #4a5568;
  font-size: 0.75rem;
  text-transform: uppercase;
}

.param-value {
  color: #2d3748;
}

.masked-value {
  color: #e53e3e;
  font-weight: bold;
}

.action-section {
  background: #ebf8ff;
  border-color: #bee3f8;
}

.form-group {
  margin-top: 0.5rem;
  margin-bottom: 0.75rem;
}

.form-group label {
  display: block;
  font-size: 0.8125rem;
  color: #2d3748;
  margin-bottom: 0.25rem;
}

.form-group textarea {
  width: 100%;
  padding: 0.5rem;
  border: 1px solid #cbd5e0;
  border-radius: 4px;
  font-size: 0.875rem;
  font-family: inherit;
}

.actions-buttons {
  display: flex;
  gap: 1rem;
}

.btn-approve {
  background-color: #38a169;
  color: white;
  border: none;
  padding: 0.625rem 1.25rem;
  border-radius: 4px;
  font-weight: 600;
  font-size: 0.875rem;
  cursor: pointer;
}

.btn-approve:hover:not(:disabled) {
  background-color: #2f855a;
}

.btn-reject {
  background-color: #e53e3e;
  color: white;
  border: none;
  padding: 0.625rem 1.25rem;
  border-radius: 4px;
  font-weight: 600;
  font-size: 0.875rem;
  cursor: pointer;
}

.btn-reject:hover:not(:disabled) {
  background-color: #c53030;
}

.btn-approve:disabled,
.btn-reject:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.decision-result-box {
  background: #edf2f7;
}

.result-approved {
  background: #f0fff4;
  border-color: #c6f6d5;
}

.result-rejected {
  background: #fff5f5;
  border-color: #fed7d7;
}

.result-expired {
  background: #f7fafc;
  border-color: #e2e8f0;
}

.decision-meta p {
  margin: 0.375rem 0;
  font-size: 0.875rem;
  color: #2d3748;
}

.error-banner {
  background-color: #fed7d7;
  color: #9b2c2c;
  padding: 0.75rem 1rem;
  border-radius: 4px;
  margin-bottom: 1rem;
  font-size: 0.875rem;
  border: 1px solid #feb2b2;
}

.placeholder-card {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  color: #a0aec0;
}
</style>

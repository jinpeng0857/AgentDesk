<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTracesStore } from '../stores/traces'
import type { TraceEventResponse } from '../api/types'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const tracesStore = useTracesStore()

const inputTicketId = ref('')

onMounted(async () => {
  tracesStore.setApiClient(authStore.apiClient)
  const paramId = route.params.ticketId as string | undefined
  const queryId = route.query.ticket_id as string | undefined
  const targetId = paramId || queryId
  if (targetId) {
    inputTicketId.value = targetId
    await loadTrace(targetId)
  }
})

async function loadTrace(ticketId: string): Promise<void> {
  const trimmed = ticketId.trim()
  if (!trimmed) return
  try {
    await tracesStore.fetchTrace(trimmed)
  } catch {
    // Error is stored in tracesStore.error
  }
}

async function handleSearch(): Promise<void> {
  if (inputTicketId.value.trim()) {
    await router.replace({
      name: 'traces',
      params: { ticketId: inputTicketId.value.trim() },
    })
    await loadTrace(inputTicketId.value.trim())
  }
}

function handleBackToTickets(): void {
  router.push('/tickets')
}

const stats = computed(() => {
  const total = tracesStore.traces.length
  let agentSteps = 0
  let toolCalls = 0
  let cacheHits = 0
  let failures = 0

  for (const event of tracesStore.traces) {
    if (event.event_type === 'agent_step') {
      agentSteps += 1
    } else if (event.event_type.startsWith('tool_call')) {
      toolCalls += 1
      if (event.summary.includes('outcome=cache_hit')) {
        cacheHits += 1
      }
      if (event.summary.includes('outcome=failed') || event.summary.includes('outcome=rejected')) {
        failures += 1
      }
    }
  }

  return { total, agentSteps, toolCalls, cacheHits, failures }
})

function getActorClass(actor: TraceEventResponse['actor']): string {
  switch (actor) {
    case 'supervisor':
      return 'actor-supervisor'
    case 'knowledge':
      return 'actor-knowledge'
    case 'action':
      return 'actor-action'
    case 'policy':
      return 'actor-policy'
    case 'approval':
      return 'actor-approval'
    case 'dispatcher':
      return 'actor-dispatcher'
    default:
      return 'actor-default'
  }
}

function getOutcomeClass(summary: string): string {
  if (summary.includes('outcome=succeeded')) return 'outcome-success'
  if (summary.includes('outcome=cache_hit')) return 'outcome-cache'
  if (summary.includes('outcome=failed')) return 'outcome-failed'
  if (summary.includes('outcome=rejected')) return 'outcome-rejected'
  if (summary.includes('outcome=timeout')) return 'outcome-timeout'
  return 'outcome-neutral'
}

function getEventCategory(eventType: string): string {
  if (eventType === 'agent_step') return 'Agent Step'
  if (eventType.startsWith('tool_call:')) return 'Tool Call'
  if (eventType === 'approval') return 'Human Approval'
  return eventType
}
</script>

<template>
  <div class="trace-viewer-layout">
    <!-- Navigation Bar -->
    <header class="navbar">
      <div class="brand-group">
        <h1 class="brand-title">AgentDesk</h1>
        <span class="sub-title">Audit Trace Viewer</span>
      </div>
      <div class="nav-actions">
        <button class="btn-secondary" @click="handleBackToTickets">
          Back to Tickets
        </button>
      </div>
    </header>

    <main class="content-container">
      <!-- Search and Controls Card -->
      <section class="card search-card">
        <form class="search-bar" @submit.prevent="handleSearch">
          <label for="ticket-id-input" class="search-label">Ticket UUID:</label>
          <input
            id="ticket-id-input"
            v-model="inputTicketId"
            type="text"
            class="input-ticket"
            placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
            required
          />
          <button
            type="submit"
            class="btn-primary"
            :disabled="tracesStore.loading"
          >
            {{ tracesStore.loading ? 'Loading Trace...' : 'Inspect Trace' }}
          </button>
        </form>

        <div v-if="tracesStore.error" class="error-banner">
          {{ tracesStore.error }}
        </div>
      </section>

      <!-- Overview Stats Card -->
      <section v-if="tracesStore.traces.length > 0" class="card stats-card">
        <div class="stats-header">
          <h2>Execution Timeline Overview</h2>
          <span class="ticket-badge">Ticket: {{ tracesStore.currentTicketId }}</span>
        </div>
        <div class="stats-grid">
          <div class="stat-box">
            <span class="stat-value">{{ stats.total }}</span>
            <span class="stat-label">Total Events</span>
          </div>
          <div class="stat-box">
            <span class="stat-value stat-agent">{{ stats.agentSteps }}</span>
            <span class="stat-label">Agent Steps</span>
          </div>
          <div class="stat-box">
            <span class="stat-value stat-tool">{{ stats.toolCalls }}</span>
            <span class="stat-label">Tool Calls</span>
          </div>
          <div class="stat-box">
            <span class="stat-value stat-cache">{{ stats.cacheHits }}</span>
            <span class="stat-label">Cache Hits (Idempotent)</span>
          </div>
          <div class="stat-box">
            <span class="stat-value stat-failure">{{ stats.failures }}</span>
            <span class="stat-label">Failures / Rejected</span>
          </div>
        </div>

        <div class="security-banner">
          <span class="shield-icon">🛡️</span>
          <span class="security-text">
            <strong>Zero Secret Leakage:</strong> All arguments, tokens and passwords
            have been masked (<code>***</code>) at the backend execution gateway before storage and transmission.
          </span>
        </div>
      </section>

      <!-- Timeline View -->
      <section class="card timeline-card">
        <h3>Chronological Event Stream</h3>

        <div v-if="tracesStore.loading" class="loading-state">
          Loading audit events...
        </div>

        <div v-else-if="tracesStore.traces.length === 0" class="empty-state">
          <p v-if="!tracesStore.currentTicketId">
            Please enter a Ticket UUID above to view its end-to-end execution trace.
          </p>
          <p v-else>
            No trace events recorded for ticket <code>{{ tracesStore.currentTicketId }}</code>.
          </p>
        </div>

        <div v-else class="timeline-stream">
          <div
            v-for="(event, idx) in tracesStore.traces"
            :key="event.request_id + '-' + idx"
            class="timeline-item"
          >
            <!-- Timeline Marker -->
            <div class="timeline-marker">
              <span class="step-number">{{ idx + 1 }}</span>
              <div class="timeline-line"></div>
            </div>

            <!-- Event Card -->
            <div class="timeline-content">
              <div class="event-header">
                <div class="actor-tags">
                  <span :class="['actor-badge', getActorClass(event.actor)]">
                    {{ event.actor }}
                  </span>
                  <span class="category-badge">
                    {{ getEventCategory(event.event_type) }}
                  </span>
                </div>
                <span class="event-time">
                  {{ new Date(event.timestamp).toLocaleTimeString() }}
                  <small class="event-iso">{{ event.timestamp }}</small>
                </span>
              </div>

              <div class="event-body">
                <div class="summary-line">
                  <span class="summary-text">{{ event.summary }}</span>
                  <span
                    v-if="event.event_type.startsWith('tool_call')"
                    :class="['outcome-badge', getOutcomeClass(event.summary)]"
                  >
                    {{ event.summary.split('outcome=')[1]?.split(' ')[0] || 'executed' }}
                  </span>
                </div>

                <div class="event-meta">
                  <span class="meta-field">
                    <strong>Request ID:</strong>
                    <code class="meta-code">{{ event.request_id }}</code>
                  </span>
                  <span class="meta-field">
                    <strong>Trace ID:</strong>
                    <code class="meta-code">{{ event.trace_id.slice(0, 8) }}...</code>
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.trace-viewer-layout {
  min-height: 100vh;
  background-color: #f7fafc;
  display: flex;
  flex-direction: column;
}

.navbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: #1a202c;
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
  color: #a0aec0;
}

.content-container {
  max-width: 1000px;
  width: 100%;
  margin: 1.5rem auto;
  padding: 0 1rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.card {
  background: white;
  border-radius: 8px;
  padding: 1.25rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.search-bar {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}

.search-label {
  font-weight: 600;
  font-size: 0.9rem;
  color: #4a5568;
}

.input-ticket {
  flex: 1;
  padding: 0.5rem 0.75rem;
  border: 1px solid #cbd5e0;
  border-radius: 6px;
  font-family: monospace;
  font-size: 0.9rem;
}

.btn-primary {
  background-color: #3182ce;
  color: white;
  border: none;
  padding: 0.5rem 1.25rem;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
}

.btn-primary:hover:not(:disabled) {
  background-color: #2b6cb0;
}

.btn-secondary {
  background-color: #4a5568;
  color: white;
  border: none;
  padding: 0.4rem 0.85rem;
  border-radius: 6px;
  cursor: pointer;
}

.error-banner {
  margin-top: 0.75rem;
  padding: 0.75rem;
  background-color: #fff5f5;
  color: #c53030;
  border: 1px solid #feb2b2;
  border-radius: 6px;
  font-size: 0.875rem;
}

.stats-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.stats-header h2 {
  font-size: 1.15rem;
  margin: 0;
  color: #2d3748;
}

.ticket-badge {
  font-family: monospace;
  font-size: 0.85rem;
  background-color: #edf2f7;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.stat-box {
  background-color: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #2d3748;
}

.stat-label {
  font-size: 0.75rem;
  color: #718096;
  text-transform: uppercase;
}

.stat-agent { color: #805ad5; }
.stat-tool { color: #319795; }
.stat-cache { color: #38a169; }
.stat-failure { color: #e53e3e; }

.security-banner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background-color: #f0fff4;
  border: 1px solid #c6f6d5;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.85rem;
  color: #22543d;
}

.shield-icon {
  font-size: 1.1rem;
}

.timeline-stream {
  display: flex;
  flex-direction: column;
  margin-top: 1rem;
}

.timeline-item {
  display: flex;
  gap: 1rem;
  position: relative;
}

.timeline-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 32px;
}

.step-number {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background-color: #edf2f7;
  border: 2px solid #cbd5e0;
  color: #4a5568;
  font-weight: 700;
  font-size: 0.75rem;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1;
}

.timeline-line {
  flex: 1;
  width: 2px;
  background-color: #e2e8f0;
  margin-top: 4px;
  margin-bottom: 4px;
}

.timeline-content {
  flex: 1;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
}

.event-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.actor-tags {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.actor-badge {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
}

.actor-supervisor { background: #ede9fe; color: #5b21b6; }
.actor-knowledge { background: #dbeafe; color: #1e40af; }
.actor-action { background: #dcfce7; color: #166534; }
.actor-policy { background: #ffedd5; color: #9a3412; }
.actor-approval { background: #fef3c7; color: #92400e; }
.actor-dispatcher { background: #f1f5f9; color: #475569; }
.actor-default { background: #f3f4f6; color: #374151; }

.category-badge {
  font-size: 0.75rem;
  background-color: #edf2f7;
  color: #4a5568;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
}

.event-time {
  font-size: 0.75rem;
  color: #718096;
}

.event-iso {
  display: block;
  font-family: monospace;
  color: #a0aec0;
}

.summary-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: white;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
  margin-bottom: 0.5rem;
}

.summary-text {
  font-family: monospace;
  font-size: 0.85rem;
  color: #2d3748;
  word-break: break-all;
}

.outcome-badge {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
}

.outcome-success { background: #c6f6d5; color: #22543d; }
.outcome-cache { background: #b2f5ea; color: #234e52; }
.outcome-failed { background: #fed7d7; color: #742a2a; }
.outcome-rejected { background: #fbb6ce; color: #702459; }
.outcome-timeout { background: #feebc8; color: #7b341e; }
.outcome-neutral { background: #edf2f7; color: #4a5568; }

.event-meta {
  display: flex;
  gap: 1.5rem;
  font-size: 0.75rem;
  color: #718096;
}

.meta-code {
  font-family: monospace;
  background: #edf2f7;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  margin-left: 0.25rem;
}

.empty-state, .loading-state {
  text-align: center;
  padding: 2rem;
  color: #718096;
  font-size: 0.95rem;
}
</style>

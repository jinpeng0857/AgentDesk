<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTicketStore } from '../stores/tickets'

const router = useRouter()
const authStore = useAuthStore()
const ticketStore = useTicketStore()

const newQuery = ref('')
const submitError = ref<string | null>(null)

onMounted(async () => {
  ticketStore.setApiClient(authStore.apiClient)
  await authStore.fetchMe()
  try {
    await ticketStore.fetchTickets()
    if (ticketStore.tickets.length > 0) {
      const firstId = ticketStore.tickets[0]?.ticket_id
      if (firstId) {
        await selectTicket(firstId)
      }
    }
  } catch {
    // Ignore fetch error on load
  }
})

onUnmounted(() => {
  ticketStore.stopAllPolling()
})

async function selectTicket(ticketId: string): Promise<void> {
  try {
    await ticketStore.fetchTicket(ticketId)
    if (
      ticketStore.currentTicket?.status === 'queued' ||
      ticketStore.currentTicket?.status === 'running'
    ) {
      ticketStore.startPolling(ticketId)
    }
  } catch {
    // Handled in store
  }
}

async function handleCreateTicket(): Promise<void> {
  submitError.value = null
  const query = newQuery.value.trim()
  if (!query) {
    submitError.value = 'Please enter a ticket description.'
    return
  }

  try {
    const ticketId = await ticketStore.submitTicket(query)
    newQuery.value = ''
    await selectTicket(ticketId)
  } catch (err: unknown) {
    submitError.value = err instanceof Error ? err.message : 'Failed to submit ticket'
  }
}

function handleLogout(): void {
  authStore.logout()
  router.push('/login')
}
</script>

<template>
  <div class="tickets-layout">
    <header class="navbar">
      <div class="brand">AgentDesk IT Service Desk</div>
      <div class="user-info">
        <span v-if="authStore.user" class="username">
          {{ authStore.user.user_id }} ({{ authStore.user.role }})
        </span>
        <button class="btn-secondary" @click="handleLogout">Log Out</button>
      </div>
    </header>

    <main class="content-container">
      <!-- Left Column: Submit Ticket & History List -->
      <section class="sidebar">
        <div class="card submit-card">
          <h3>Submit IT Ticket</h3>
          <div v-if="submitError" class="error-banner">
            {{ submitError }}
          </div>
          <form @submit.prevent="handleCreateTicket">
            <textarea
              v-model="newQuery"
              rows="3"
              required
              placeholder="Describe your issue (e.g. VPN connection failed, password reset)..."
            ></textarea>
            <button
              type="submit"
              :disabled="ticketStore.loading"
              class="btn-primary"
            >
              {{ ticketStore.loading ? 'Submitting...' : 'Submit Ticket' }}
            </button>
          </form>
        </div>

        <div class="card list-card">
          <h3>Your Tickets</h3>
          <div v-if="ticketStore.tickets.length === 0" class="empty-state">
            No tickets found. Submit a request above.
          </div>
          <ul v-else class="ticket-list">
            <li
              v-for="item in ticketStore.tickets"
              :key="item.ticket_id"
              :class="[
                'ticket-item',
                { active: ticketStore.currentTicket?.ticket_id === item.ticket_id },
              ]"
              @click="selectTicket(item.ticket_id)"
            >
              <div class="item-header">
                <span :class="['status-badge', item.status]">{{ item.status }}</span>
                <span class="ticket-time">
                  {{ new Date(item.updated_at).toLocaleTimeString() }}
                </span>
              </div>
              <div class="ticket-id-preview">
                ID: {{ item.ticket_id.slice(0, 8) }}...
              </div>
            </li>
          </ul>
        </div>
      </section>

      <!-- Right Column: Selected Ticket Details & Citations -->
      <section class="main-content">
        <div v-if="ticketStore.currentTicket" class="card detail-card">
          <div class="detail-header">
            <div>
              <h2>Ticket Details</h2>
              <p class="ticket-uuid">ID: {{ ticketStore.currentTicket.ticket_id }}</p>
            </div>
            <span :class="['status-badge', ticketStore.currentTicket.status]">
              {{ ticketStore.currentTicket.status }}
            </span>
          </div>

          <div class="meta-grid">
            <div class="meta-item">
              <span class="meta-label">Intent:</span>
              <span class="meta-value">{{ ticketStore.currentTicket.intent || 'pending' }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Risk Level:</span>
              <span class="meta-value">{{ ticketStore.currentTicket.risk_level || 'pending' }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Resolution Mode:</span>
              <span class="meta-value">{{ ticketStore.currentTicket.resolution_mode || 'pending' }}</span>
            </div>
          </div>

          <!-- Progress Indicator for Running Tickets -->
          <div
            v-if="
              ticketStore.currentTicket.status === 'queued' ||
              ticketStore.currentTicket.status === 'running'
            "
            class="progress-box"
          >
            <span class="pulse-indicator"></span>
            Agent workflow is executing. Auto-polling every 2 seconds...
          </div>

          <!-- Final Response -->
          <div class="section-box response-section">
            <h3>Final Response</h3>
            <div v-if="ticketStore.currentTicket.final_response" class="response-text">
              {{ ticketStore.currentTicket.final_response }}
            </div>
            <div v-else class="pending-text">
              No final response yet. Processing ticket...
            </div>
          </div>

          <!-- 5-Field Citations Knowledge References -->
          <div class="section-box citations-section">
            <h3>Knowledge References & Citations</h3>
            <div
              v-if="!ticketStore.currentTicket.knowledge_refs || ticketStore.currentTicket.knowledge_refs.length === 0"
              class="pending-text"
            >
              No knowledge citations associated with this ticket.
            </div>
            <div v-else class="citation-cards">
              <div
                v-for="(refItem, index) in ticketStore.currentTicket.knowledge_refs"
                :key="refItem.chunk_id || index"
                class="citation-card"
              >
                <div class="citation-title">
                  {{ refItem.title }}
                  <span class="citation-score">
                    Confidence: {{ (refItem.score * 100).toFixed(1) }}%
                  </span>
                </div>
                <div class="citation-excerpt">
                  "{{ refItem.content_excerpt }}"
                </div>
                <div class="citation-meta">
                  <span>Document ID: {{ refItem.document_id }}</span>
                  <span>Chunk ID: {{ refItem.chunk_id }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="card detail-card placeholder-card">
          <p>Select a ticket or submit a new request to see details.</p>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.tickets-layout {
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

.brand {
  font-weight: 700;
  font-size: 1.125rem;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.username {
  font-size: 0.875rem;
  color: #cbd5e0;
}

.content-container {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 1.5rem;
  padding: 1.5rem;
  flex: 1;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.card {
  background: white;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 1.25rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

h2, h3 {
  margin: 0 0 0.75rem 0;
  color: #2d3748;
}

textarea {
  width: 100%;
  padding: 0.625rem;
  border: 1px solid #cbd5e0;
  border-radius: 4px;
  font-size: 0.875rem;
  box-sizing: border-box;
  margin-bottom: 0.75rem;
  font-family: inherit;
}

textarea:focus {
  outline: none;
  border-color: #3182ce;
}

.btn-primary {
  width: 100%;
  padding: 0.625rem;
  background-color: #3182ce;
  color: white;
  border: none;
  border-radius: 4px;
  font-weight: 600;
  cursor: pointer;
}

.btn-secondary {
  padding: 0.375rem 0.75rem;
  background: transparent;
  color: #e2e8f0;
  border: 1px solid #718096;
  border-radius: 4px;
  cursor: pointer;
}

.btn-secondary:hover {
  background: rgba(255, 255, 255, 0.1);
}

.ticket-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-height: 400px;
  overflow-y: auto;
}

.ticket-item {
  padding: 0.75rem;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.ticket-item:hover {
  background-color: #f7fafc;
}

.ticket-item.active {
  border-color: #3182ce;
  background-color: #ebf8ff;
}

.item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.25rem;
}

.ticket-time {
  font-size: 0.75rem;
  color: #a0aec0;
}

.ticket-id-preview {
  font-size: 0.75rem;
  color: #718096;
  font-family: monospace;
}

.status-badge {
  display: inline-block;
  padding: 0.25rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.status-badge.queued {
  background: #feebc8;
  color: #7b341e;
}

.status-badge.running {
  background: #bee3f8;
  color: #2b6cb0;
}

.status-badge.resolved {
  background: #c6f6d5;
  color: #22543d;
}

.status-badge.escalated, .status-badge.failed, .status-badge.rejected {
  background: #fed7d7;
  color: #9b2c2c;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.25rem;
}

.ticket-uuid {
  margin: 0;
  font-family: monospace;
  font-size: 0.875rem;
  color: #718096;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  background: #f7fafc;
  padding: 0.75rem 1rem;
  border-radius: 4px;
  margin-bottom: 1.25rem;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.meta-label {
  font-size: 0.75rem;
  color: #718096;
  text-transform: uppercase;
}

.meta-value {
  font-weight: 600;
  color: #2d3748;
}

.progress-box {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background-color: #ebf8ff;
  color: #2b6cb0;
  padding: 0.75rem 1rem;
  border-radius: 4px;
  margin-bottom: 1.25rem;
  font-size: 0.875rem;
}

.pulse-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #3182ce;
  animation: pulse 1.5s infinite ease-in-out;
}

@keyframes pulse {
  0% { transform: scale(0.8); opacity: 0.5; }
  50% { transform: scale(1.2); opacity: 1; }
  100% { transform: scale(0.8); opacity: 0.5; }
}

.section-box {
  margin-top: 1.25rem;
  padding-top: 1.25rem;
  border-top: 1px solid #e2e8f0;
}

.response-text {
  background: #f0fff4;
  border-left: 4px solid #38a169;
  padding: 1rem;
  border-radius: 0 4px 4px 0;
  color: #2d3748;
  line-height: 1.5;
  white-space: pre-wrap;
}

.citation-cards {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.citation-card {
  border: 1px solid #cbd5e0;
  border-radius: 4px;
  padding: 0.75rem 1rem;
  background: #ffffff;
}

.citation-title {
  font-weight: 600;
  color: #2b6cb0;
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.citation-score {
  font-size: 0.75rem;
  color: #4a5568;
  font-weight: normal;
}

.citation-excerpt {
  font-style: italic;
  color: #4a5568;
  font-size: 0.875rem;
  margin-bottom: 0.5rem;
  line-height: 1.4;
}

.citation-meta {
  font-size: 0.75rem;
  color: #a0aec0;
  display: flex;
  gap: 1rem;
  font-family: monospace;
}

.pending-text, .empty-state {
  color: #a0aec0;
  font-style: italic;
  font-size: 0.875rem;
}

.error-banner {
  background-color: #fed7d7;
  color: #c53030;
  padding: 0.5rem;
  border-radius: 4px;
  margin-bottom: 0.75rem;
  font-size: 0.875rem;
}
</style>

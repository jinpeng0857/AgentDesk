<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTracesStore } from '../stores/traces'
import type { TraceEventResponse } from '../api/types'
import AppShell from '../components/AppShell.vue'
import UiIcon from '../components/UiIcon.vue'
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const tracesStore = useTracesStore()
const inputTicketId = ref('')
const eventFilter = ref<'all' | 'agents' | 'tools' | 'approval'>('all')
const filterOptions = [
  { key: 'all', label: '全部事件' },
  { key: 'agents', label: '角色与路由' },
  { key: 'tools', label: '工具调用' },
  { key: 'approval', label: '人工审批' },
] as const
const actorDescriptions: Record<string, string> = {
  supervisor: '分类与汇总',
  knowledge: '知识检索',
  action: '提案与执行',
  policy: '风险判定',
  dispatcher: '路由与状态',
  approval: '人工决定',
}
onMounted(async () => {
  tracesStore.setApiClient(authStore.apiClient)
  const paramId = route.params.ticketId
  const queryId = route.query.ticket_id
  const targetId =
    typeof paramId === 'string'
      ? paramId
      : typeof queryId === 'string'
        ? queryId
        : ''
  if (targetId) {
    inputTicketId.value = targetId
    await loadTrace(targetId)
  }
})
async function loadTrace(id: string): Promise<void> {
  if (!id.trim()) return
  try {
    await tracesStore.fetchTrace(id.trim())
  } catch {
    /* Store exposes API error. */
  }
}
async function handleSearch(): Promise<void> {
  const id = inputTicketId.value.trim()
  if (!id) return
  await router.replace({ name: 'traces', params: { ticketId: id } })
  await loadTrace(id)
}
function outcome(event: TraceEventResponse): string {
  return event.summary.match(/outcome=([a-z_]+)/)?.[1] || ''
}
function outcomeClass(value: string): string {
  if (value === 'succeeded' || value === 'cache_hit') return 'success'
  if (['failed', 'rejected', 'timeout'].includes(value)) return 'error'
  return ''
}
function category(type: string): string {
  if (type === 'agent_step') return '角色步骤'
  if (type.startsWith('tool_call')) return '工具调用'
  if (type === 'approval') return '人工审批'
  return type
}
const stats = computed(() => ({
  total: tracesStore.traces.length,
  agents: tracesStore.traces.filter((e) => e.event_type === 'agent_step')
    .length,
  tools: tracesStore.traces.filter((e) => e.event_type.startsWith('tool_call'))
    .length,
  cached: tracesStore.traces.filter(
    (e) => e.event_type.startsWith('tool_call') && outcome(e) === 'cache_hit',
  ).length,
  failures: tracesStore.traces.filter(
    (e) =>
      e.event_type.startsWith('tool_call') &&
      ['failed', 'rejected', 'timeout'].includes(outcome(e)),
  ).length,
}))
const visibleEvents = computed(() =>
  tracesStore.traces
    .map((event, index) => ({ event, index }))
    .filter(
      ({ event }) =>
        eventFilter.value === 'all' ||
        (eventFilter.value === 'agents'
          ? event.event_type === 'agent_step'
          : eventFilter.value === 'tools'
            ? event.event_type.startsWith('tool_call')
            : event.event_type === 'approval'),
    ),
)
</script>
<template>
  <AppShell
    active="traces"
    title="执行轨迹"
    subtitle="从角色交接到工具执行，把处理过程串成一条可回查的时间线。"
    eyebrow="EXECUTION & AUDIT"
  >
    <template #actions
      ><button
        class="button"
        :disabled="tracesStore.loading || !tracesStore.currentTicketId"
        @click="loadTrace(tracesStore.currentTicketId || '')"
      >
        <UiIcon name="refresh" :size="16" />刷新
      </button></template
    >
    <section class="panel trace-search">
      <form @submit.prevent="handleSearch">
        <label for="ticket-id-input">工单 ID</label>
        <div class="search-row">
          <input
            id="ticket-id-input"
            v-model="inputTicketId"
            type="text"
            placeholder="粘贴工单完整 ID，或从工单详情进入"
            required
          /><button
            class="button primary"
            :disabled="tracesStore.loading"
            type="submit"
          >
            {{ tracesStore.loading ? '加载中…' : '查看轨迹'
            }}<UiIcon name="arrow" :size="15" />
          </button>
        </div>
      </form>
    </section>
    <div v-if="tracesStore.error" class="error-banner" role="alert">
      {{ tracesStore.error }}
    </div>
    <div v-if="tracesStore.traces.length" class="trace-overview">
      <div>
        <strong>{{ stats.total }}</strong
        ><span>全部事件</span>
      </div>
      <div>
        <strong>{{ stats.agents }}</strong
        ><span>角色步骤</span>
      </div>
      <div>
        <strong>{{ stats.tools }}</strong
        ><span>工具尝试</span>
      </div>
      <div>
        <strong>{{ stats.cached }}</strong
        ><span>缓存命中</span>
      </div>
      <div>
        <strong :class="{ 'failure-count': stats.failures > 0 }">{{
          stats.failures
        }}</strong
        ><span>失败 / 拒绝 / 超时</span>
      </div>
    </div>
    <section
      class="panel trace-timeline"
      aria-labelledby="timeline-heading"
      :aria-busy="tracesStore.loading"
    >
      <div class="panel-heading">
        <div>
          <h2 id="timeline-heading">处理时间线</h2>
          <p v-if="tracesStore.currentTicketId" class="mono">
            #{{ tracesStore.currentTicketId }}
          </p>
          <p v-else>选择工单后，显示已记录的角色与工具事件。</p>
        </div>
        <span class="badge">只读记录</span>
      </div>
      <div v-if="tracesStore.traces.length" class="timeline-toolbar">
        <div class="tabs" aria-label="筛选事件类型">
          <button
            v-for="tab in filterOptions"
            :key="tab.key"
            :class="['tab', { active: eventFilter === tab.key }]"
            :aria-pressed="eventFilter === tab.key"
            @click="eventFilter = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
        <span>{{ visibleEvents.length }} 条事件</span>
      </div>
      <div v-if="tracesStore.loading" class="empty-state" role="status">
        正在加载审计记录…
      </div>
      <div v-else-if="!visibleEvents.length" class="empty-state">
        <UiIcon name="trace" :size="36" />
        <h3>
          {{
            tracesStore.currentTicketId
              ? '暂无匹配的轨迹事件'
              : '每一步都有来处'
          }}
        </h3>
        <p>
          {{
            tracesStore.currentTicketId
              ? '该工单暂无对应记录，或当前筛选没有匹配事件。'
              : '输入工单 ID，查看角色交接、治理判定、工具尝试与人工决定。'
          }}
        </p>
      </div>
      <ol v-else class="timeline-stream">
        <li
          v-for="{ event, index } in visibleEvents"
          :key="event.request_id + '-' + index"
          class="timeline-item"
        >
          <div class="timeline-marker">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
          </div>
          <div class="timeline-content">
            <div class="event-header">
              <div>
                <span class="actor-name">{{ event.actor }}</span
                ><span class="actor-description">{{
                  actorDescriptions[event.actor] || category(event.event_type)
                }}</span
                ><span class="badge event-category">{{
                  category(event.event_type)
                }}</span>
              </div>
              <time :datetime="event.timestamp">{{
                new Date(event.timestamp).toLocaleTimeString('zh-CN', {
                  hour12: false,
                })
              }}</time>
            </div>
            <p class="event-summary">{{ event.summary }}</p>
            <div class="event-bottom">
              <span
                v-if="outcome(event)"
                :class="['badge', outcomeClass(outcome(event))]"
                >{{ outcome(event) }}</span
              >
              <details>
                <summary>查看追溯标识</summary>
                <dl>
                  <dt>Request ID</dt>
                  <dd class="mono">{{ event.request_id }}</dd>
                  <dt>Trace ID</dt>
                  <dd class="mono">{{ event.trace_id }}</dd>
                  <dt>记录时间</dt>
                  <dd class="mono">{{ event.timestamp }}</dd>
                </dl>
              </details>
            </div>
          </div>
        </li>
      </ol>
      <div class="trace-footnote">
        <UiIcon name="lock" :size="15" />
        <p>
          显示网关输出的脱敏审计摘要；密码、令牌等敏感值不应出现在记录中。工具尝试次数包含失败、拒绝、超时和缓存命中。
        </p>
      </div>
    </section>
  </AppShell>
</template>
<style scoped>
.trace-search {
  padding: 20px 24px;
  margin-bottom: 24px;
}
.trace-search form {
  max-width: 760px;
}
.search-row {
  display: flex;
  gap: 12px;
}
.trace-overview {
  display: flex;
  align-items: center;
  gap: 0;
  margin: 8px 0 26px;
}
.trace-overview > div {
  padding: 0 30px;
  border-right: 1px solid var(--line);
}
.trace-overview > div:first-child {
  padding-left: 0;
}
.trace-overview > div:last-child {
  border: 0;
}
.trace-overview strong {
  display: block;
  font-size: 25px;
  font-weight: 550;
  letter-spacing: -1px;
}
.trace-overview span {
  font-size: 10px;
  color: var(--muted);
}
.failure-count {
  color: var(--danger);
}
.timeline-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  border-bottom: 1px solid var(--line);
}
.timeline-toolbar > span {
  font-size: 10px;
  color: var(--muted);
  white-space: nowrap;
}
.timeline-stream {
  margin: 0;
  padding: 26px 28px 6px;
  list-style: none;
}
.timeline-item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 18px;
}
.timeline-marker {
  position: relative;
}
.timeline-marker::after {
  content: '';
  width: 1px;
  background: #d8e1ef;
  position: absolute;
  left: 15px;
  top: 28px;
  bottom: 0;
}
.timeline-item:last-child .timeline-marker::after {
  display: none;
}
.timeline-marker > span {
  display: grid;
  place-items: center;
  border: 1px solid #cbd9ef;
  background: #f5f8ff;
  color: #526b95;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font:
    10px Consolas,
    monospace;
}
.timeline-content {
  padding: 2px 0 28px;
  min-width: 0;
}
.event-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.event-header > div {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.actor-name {
  font-size: 13px;
  font-weight: 600;
}
.actor-description {
  font-size: 11px;
  color: var(--muted);
}
.event-header time {
  color: var(--muted);
  font:
    10px Consolas,
    monospace;
  white-space: nowrap;
}
.event-summary {
  font-size: 12px;
  color: #56667d;
  line-height: 1.9;
  overflow-wrap: anywhere;
  margin-top: 11px;
}
.event-bottom {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  margin-top: 12px;
}
.event-bottom details {
  font-size: 10px;
  color: var(--muted);
}
.event-bottom summary {
  color: #597399;
}
.event-bottom dl {
  margin-bottom: 0;
}
.event-bottom dd {
  margin: 3px 0 8px;
}
.trace-footnote {
  padding: 16px 24px;
  border-top: 1px solid var(--line);
  display: flex;
  gap: 9px;
  color: var(--muted);
}
.trace-footnote svg {
  margin-top: 3px;
}
.trace-footnote p {
  font-size: 10px;
  line-height: 1.9;
}
@media (max-width: 650px) {
  .trace-search {
    padding: 18px;
  }
  .trace-overview {
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }
  .trace-overview > div {
    padding: 0;
    border: 0;
  }
  .trace-overview span {
    font-size: 9px;
  }
  .timeline-stream {
    padding: 22px 18px 0;
  }
  .timeline-item {
    gap: 12px;
  }
  .event-header {
    align-items: flex-start;
  }
  .actor-description,
  .event-category {
    display: none;
  }
  .timeline-toolbar {
    padding: 14px;
  }
  .timeline-toolbar > span {
    display: none;
  }
}
</style>

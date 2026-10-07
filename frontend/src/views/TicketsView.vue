<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTicketStore } from '../stores/tickets'
import AppShell from '../components/AppShell.vue'
import UiIcon from '../components/UiIcon.vue'
import {
  statusLabels,
  intentLabels,
  riskLabels,
  formatDate,
} from './presentation'

const router = useRouter()
const authStore = useAuthStore()
const ticketStore = useTicketStore()
const newQuery = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)
const filter = ref<'all' | 'active' | 'resolved'>('all')
const knowledgeRefs = computed(
  () => ticketStore.currentTicket?.knowledge_refs || [],
)
const examples = [
  { label: '网络咨询', query: 'VPN 连接失败，应该如何排查？' },
  { label: '密码重置', query: '请帮我重置自己的账户密码。' },
  {
    label: '权限申请',
    query: '我需要申请开发环境访问权限，用于排查项目问题。',
  },
]
const counts = computed(() => ({
  total: ticketStore.tickets.length,
  running: ticketStore.tickets.filter(
    (t) => t.status === 'queued' || t.status === 'running',
  ).length,
  waiting: ticketStore.tickets.filter((t) => t.status === 'waiting_approval')
    .length,
  resolved: ticketStore.tickets.filter((t) => t.status === 'resolved').length,
}))
const visibleTickets = computed(() =>
  ticketStore.tickets.filter(
    (t) =>
      filter.value === 'all' ||
      (filter.value === 'resolved'
        ? t.status === 'resolved'
        : ['queued', 'running', 'waiting_approval'].includes(t.status)),
  ),
)
const resolutionLabels: Record<string, string> = {
  automated: '自动处理',
  fallback: '保守处理',
  human_required: '需要人工',
}
const responseNotes = computed(() => {
  const status = ticketStore.currentTicket?.status
  if (status === 'waiting_approval')
    return '操作已暂停，等待审批人作出决定。审批通过前不会执行高风险写操作。'
  if (status === 'escalated')
    return '自动流程已停止，需要人工接管。请查看执行轨迹了解原因。'
  if (status === 'failed' || status === 'rejected' || status === 'expired')
    return '当前流程已结束，请查看执行轨迹中的处理记录。'
  return '处理完成后，答复将显示在这里。'
})

onMounted(async () => {
  ticketStore.setApiClient(authStore.apiClient)
  await authStore.fetchMe()
  await refreshTickets()
})
onUnmounted(() => ticketStore.stopAllPolling())
async function refreshTickets(): Promise<void> {
  try {
    await ticketStore.fetchTickets()
    const ticketId =
      ticketStore.currentTicket?.ticket_id || ticketStore.tickets[0]?.ticket_id
    if (ticketId) await selectTicket(ticketId)
  } catch {
    /* The store exposes the typed API error. */
  }
}
async function selectTicket(ticketId: string): Promise<void> {
  try {
    await ticketStore.fetchTicket(ticketId)
    if (
      ticketStore.currentTicket &&
      ['queued', 'running', 'waiting_approval'].includes(
        ticketStore.currentTicket.status,
      )
    )
      ticketStore.startPolling(ticketId)
  } catch {
    /* The store exposes the typed API error. */
  }
}
async function handleCreateTicket(): Promise<void> {
  submitError.value = null
  const query = newQuery.value.trim()
  if (!query) {
    submitError.value = '请先描述需要处理的问题。'
    return
  }
  if (submitting.value) return
  submitting.value = true
  try {
    const ticketId = await ticketStore.submitTicket(query)
    newQuery.value = ''
    filter.value = 'all'
    await selectTicket(ticketId)
  } catch (err: unknown) {
    submitError.value =
      err instanceof Error ? err.message : '提交失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AppShell
    active="tickets"
    title="工单工作台"
    subtitle="描述问题，跟进处理，查看每一次决策的依据。"
    eyebrow="SERVICE OPERATIONS"
  >
    <template #actions
      ><button
        class="button"
        :disabled="ticketStore.loading"
        @click="refreshTickets"
      >
        <UiIcon name="refresh" :size="16" />刷新
      </button></template
    >
    <div class="ticket-overview" aria-label="当前工单列表统计">
      <div>
        <span>我的工单</span><strong>{{ counts.total }}</strong>
      </div>
      <div>
        <span>正在处理</span><strong>{{ counts.running }}</strong>
      </div>
      <div>
        <span>等待审批</span><strong>{{ counts.waiting }}</strong>
      </div>
      <div>
        <span>已解决</span><strong>{{ counts.resolved }}</strong>
      </div>
      <small>基于当前账号可见工单</small>
    </div>
    <section class="panel intake-panel" aria-labelledby="intake-heading">
      <div class="intake-intro">
        <span class="intake-icon"><UiIcon name="plus" :size="23" /></span>
        <h2 id="intake-heading">有什么需要处理？</h2>
        <p>咨询、查询或发起操作，<br />从一句话开始。</p>
      </div>
      <form class="intake-form" @submit.prevent="handleCreateTicket">
        <label for="ticket-query" class="sr-only">工单描述</label>
        <textarea
          id="ticket-query"
          v-model="newQuery"
          rows="2"
          maxlength="2000"
          placeholder="例如：VPN 连接失败，应该如何排查？"
          aria-describedby="query-hint"
          :aria-invalid="Boolean(submitError)"
          :disabled="submitting"
        ></textarea>
        <div v-if="submitError" class="error-banner" role="alert">
          {{ submitError }}
        </div>
        <div class="intake-bottom">
          <div class="example-queries">
            <span>试试看</span
            ><button
              v-for="example in examples"
              :key="example.label"
              type="button"
              :disabled="submitting"
              @click="newQuery = example.query"
            >
              {{ example.label }}
            </button>
          </div>
          <button class="button primary" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '提交工单'
            }}<UiIcon name="arrow" :size="16" />
          </button>
        </div>
        <p id="query-hint" class="field-hint">
          使用模拟数据；权限申请仅创建请求，高风险写操作需人工审批。
        </p>
      </form>
    </section>
    <div
      v-if="ticketStore.error && !submitError"
      class="error-banner"
      role="alert"
    >
      {{ ticketStore.error }}
    </div>
    <div class="split-workspace">
      <section class="panel" aria-labelledby="ticket-list-heading">
        <div class="panel-heading">
          <h3 id="ticket-list-heading">我的工单</h3>
          <span class="muted mono">{{ visibleTickets.length }}</span>
        </div>
        <div class="ticket-filters tabs" aria-label="筛选工单">
          <button
            v-for="tab in [
              { key: 'all', label: '全部' },
              { key: 'active', label: '进行中' },
              { key: 'resolved', label: '已解决' },
            ] as const"
            :key="tab.key"
            :class="['tab', { active: filter === tab.key }]"
            :aria-pressed="filter === tab.key"
            @click="filter = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
        <div v-if="!visibleTickets.length" class="empty-state compact">
          <UiIcon name="tickets" :size="28" />
          <p>
            {{
              ticketStore.loading
                ? '正在加载工单…'
                : '这里还没有工单。提交请求后即可跟进。'
            }}
          </p>
        </div>
        <button
          v-for="item in visibleTickets"
          :key="item.ticket_id"
          :class="[
            'list-row',
            {
              selected: ticketStore.currentTicket?.ticket_id === item.ticket_id,
            },
          ]"
          :aria-pressed="
            ticketStore.currentTicket?.ticket_id === item.ticket_id
          "
          @click="selectTicket(item.ticket_id)"
        >
          <div class="row-top">
            <span class="row-title">{{
              item.intent ? intentLabels[item.intent] : '等待分类'
            }}</span
            ><span :class="['badge', item.status]">{{
              statusLabels[item.status]
            }}</span>
          </div>
          <div class="row-meta">
            <span class="mono">#{{ item.ticket_id.slice(0, 8) }}</span
            ><time :datetime="item.updated_at">{{
              formatDate(item.updated_at)
            }}</time>
          </div>
        </button>
      </section>
      <section
        class="panel ticket-detail"
        aria-labelledby="detail-heading"
        :aria-busy="ticketStore.loading"
      >
        <template v-if="ticketStore.currentTicket">
          <div class="panel-heading">
            <div>
              <h2 id="detail-heading">
                {{
                  ticketStore.currentTicket.intent
                    ? intentLabels[ticketStore.currentTicket.intent]
                    : '请求处理中'
                }}
              </h2>
              <p class="mono">#{{ ticketStore.currentTicket.ticket_id }}</p>
            </div>
            <span :class="['badge', ticketStore.currentTicket.status]">{{
              statusLabels[ticketStore.currentTicket.status]
            }}</span>
          </div>
          <div class="panel-body">
            <dl class="metadata">
              <div>
                <dt>请求分类</dt>
                <dd>
                  {{
                    ticketStore.currentTicket.intent
                      ? intentLabels[ticketStore.currentTicket.intent]
                      : '等待分类'
                  }}
                </dd>
              </div>
              <div>
                <dt>操作风险</dt>
                <dd>
                  <span
                    v-if="ticketStore.currentTicket.risk_level"
                    :class="['badge', ticketStore.currentTicket.risk_level]"
                    >{{
                      riskLabels[ticketStore.currentTicket.risk_level]
                    }}</span
                  ><span v-else>尚未判定</span>
                </dd>
              </div>
              <div>
                <dt>处理方式</dt>
                <dd>
                  {{
                    ticketStore.currentTicket.resolution_mode
                      ? resolutionLabels[
                          ticketStore.currentTicket.resolution_mode
                        ] || ticketStore.currentTicket.resolution_mode
                      : '等待处理'
                  }}
                </dd>
              </div>
            </dl>
            <div
              v-if="
                ['queued', 'running'].includes(ticketStore.currentTicket.status)
              "
              class="notice processing-notice"
              role="status"
            >
              <span class="activity-dot"></span>
              <div>
                <strong>{{
                  ticketStore.currentTicket.status === 'queued'
                    ? '请求已接收，正在排队'
                    : 'Agent 正在处理请求'
                }}</strong>
                <p>状态会自动更新。可以继续提交其他工单。</p>
              </div>
            </div>
            <div
              v-else-if="
                ticketStore.currentTicket.status === 'waiting_approval'
              "
              class="notice warning processing-notice"
            >
              <UiIcon name="shield" :size="18" />
              <div>
                <strong>等待人工审批</strong>
                <p>{{ responseNotes }}</p>
                <button
                  v-if="authStore.user?.role === 'approver'"
                  class="text-button"
                  @click="router.push('/approvals')"
                >
                  前往审批中心<UiIcon name="arrow" :size="14" />
                </button>
              </div>
            </div>
            <hr class="divider" />
            <div class="section-heading">
              <h3>处理答复</h3>
              <UiIcon name="check" :size="17" />
            </div>
            <p
              v-if="ticketStore.currentTicket.final_response"
              class="response-text"
            >
              {{ ticketStore.currentTicket.final_response }}
            </p>
            <p v-else class="response-placeholder">{{ responseNotes }}</p>
            <template v-if="knowledgeRefs.length"
              ><hr class="divider" />
              <div class="section-heading">
                <h3>参考知识</h3>
                <small>{{ knowledgeRefs.length }} 条可回查引用</small>
              </div>
              <article
                v-for="(reference, index) in knowledgeRefs"
                :key="reference.document_id + ':' + reference.chunk_id"
                class="citation"
              >
                <div class="citation-number">
                  {{ String(index + 1).padStart(2, '0') }}
                </div>
                <div>
                  <h3>{{ reference.title }}</h3>
                  <p>{{ reference.content_excerpt }}</p>
                  <details>
                    <summary>查看引用来源</summary>
                    <dl>
                      <dt>文档</dt>
                      <dd class="mono">{{ reference.document_id }}</dd>
                      <dt>片段</dt>
                      <dd class="mono">{{ reference.chunk_id }}</dd>
                      <dt>检索分数（不代表可信度）</dt>
                      <dd>{{ reference.score }}</dd>
                    </dl>
                  </details>
                </div>
              </article></template
            >
          </div>
          <div class="detail-footer">
            <span
              >更新于
              {{ formatDate(ticketStore.currentTicket.updated_at) }}</span
            ><button
              class="text-button"
              @click="
                router.push({
                  name: 'traces',
                  params: { ticketId: ticketStore.currentTicket.ticket_id },
                })
              "
            >
              查看执行轨迹<UiIcon name="arrow" :size="15" />
            </button>
          </div>
        </template>
        <div v-else class="empty-state ticket-placeholder">
          <UiIcon name="tickets" :size="35" /><span class="eyebrow"
            >ONE REQUEST. EVERY STEP.</span
          >
          <h2 id="detail-heading">从请求到结果，过程清楚可见</h2>
          <p>选择左侧工单，查看处理状态、最终答复和知识依据。</p>
          <div class="workflow-preview">
            <span>分类</span><UiIcon name="arrow" :size="14" /><span
              >检索 / 操作</span
            ><UiIcon name="arrow" :size="14" /><span>答复</span>
          </div>
        </div>
      </section>
    </div>
  </AppShell>
</template>
<style scoped>
.ticket-overview {
  display: flex;
  gap: 44px;
  align-items: center;
  padding: 0 0 24px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.ticket-overview > div {
  display: flex;
  align-items: baseline;
  gap: 14px;
}
.ticket-overview span {
  font-size: 11px;
  color: var(--muted);
}
.ticket-overview strong {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -1px;
}
.ticket-overview small {
  font-size: 10px;
  color: var(--muted);
  margin-left: auto;
}
.intake-panel {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  margin-bottom: 24px;
  padding: 24px;
  gap: 30px;
}
.intake-icon {
  display: inline-grid;
  place-items: center;
  width: 34px;
  height: 34px;
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 8px;
  margin-bottom: 10px;
}
.intake-intro h2 {
  font-size: 17px;
}
.intake-intro p {
  font-size: 11px;
  color: var(--muted);
  line-height: 1.9;
  margin-top: 7px;
}
.intake-form textarea {
  min-height: 76px;
  background: #fbfcfe;
}
.intake-form .error-banner {
  margin-block: 10px;
}
.intake-bottom {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
  margin-top: 12px;
}
.example-queries {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 10px;
}
.example-queries > span {
  color: var(--muted);
}
.example-queries button {
  padding: 4px 8px;
  color: #4d627e;
  background: #f1f4f8;
  border: 0;
  border-radius: 4px;
  font-size: 10px;
}
.example-queries button:hover {
  background: #e4edff;
  color: var(--accent);
}
.ticket-filters {
  padding: 12px 15px;
  border-bottom: 1px solid var(--line);
}
.processing-notice {
  margin-top: 24px;
}
.activity-dot {
  height: 7px;
  width: 7px;
  border-radius: 50%;
  background: var(--accent);
  margin-top: 7px;
  flex-shrink: 0;
  box-shadow: 0 0 0 4px #245ee811;
}
.response-text {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.9;
}
.response-placeholder {
  color: var(--muted);
  font-size: 12px;
}
.citation {
  display: flex;
  align-items: flex-start;
  gap: 15px;
  padding: 16px 0;
  border-bottom: 1px solid var(--line);
}
.citation:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.citation-number {
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-soft);
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  border-radius: 4px;
}
.citation h3 {
  font-size: 12px;
}
.citation p {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.9;
  margin-top: 6px;
}
.citation summary {
  font-size: 10px;
  color: var(--accent);
  margin-top: 9px;
}
.citation dl {
  margin: 10px 0 0;
  font-size: 11px;
}
.citation dt {
  color: var(--muted);
  margin-top: 6px;
}
.citation dd {
  margin: 0;
}
.detail-footer {
  padding: 14px 24px;
  border-top: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  font-size: 10px;
  color: var(--muted);
}
.ticket-placeholder {
  min-height: 365px;
}
.workflow-preview {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 28px;
  font-size: 11px;
  color: #5a7193;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}
@media (max-width: 1150px) {
  .ticket-overview {
    gap: 25px;
  }
  .ticket-overview small {
    display: none;
  }
  .intake-panel {
    grid-template-columns: 175px minmax(0, 1fr);
    gap: 22px;
  }
}
@media (max-width: 650px) {
  .ticket-overview {
    gap: 10px;
    justify-content: space-between;
  }
  .ticket-overview > div {
    display: block;
  }
  .ticket-overview strong {
    display: block;
    margin-top: 6px;
  }
  .intake-panel {
    grid-template-columns: 1fr;
    gap: 16px;
    padding: 20px;
  }
  .intake-intro {
    display: grid;
    grid-template-columns: 34px 1fr;
    column-gap: 12px;
    align-items: center;
  }
  .intake-icon {
    grid-row: span 2;
    margin: 0;
  }
  .intake-intro p {
    margin: 0;
  }
  .intake-intro br {
    display: none;
  }
  .intake-bottom {
    align-items: flex-start;
  }
  .example-queries {
    gap: 6px;
  }
  .example-queries > span {
    display: none;
  }
}
</style>

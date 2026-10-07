<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useApprovalsStore } from '../stores/approvals'
import type { ApprovalStatus } from '../api/types'
import AppShell from '../components/AppShell.vue'
import UiIcon from '../components/UiIcon.vue'
import {
  approvalLabels,
  riskLabels,
  toolLabels,
  policyReasonLabels,
  formatDate,
} from './presentation'
const router = useRouter()
const authStore = useAuthStore()
const approvalsStore = useApprovalsStore()
const decisionReason = ref('')
const activeFilter = ref<ApprovalStatus | 'all'>('pending')
const filters: { key: ApprovalStatus | 'all'; label: string }[] = [
  { key: 'pending', label: '待审批' },
  { key: 'approved', label: '已通过' },
  { key: 'rejected', label: '已拒绝' },
  { key: 'expired', label: '已过期' },
  { key: 'all', label: '全部' },
]
onMounted(async () => {
  approvalsStore.setApiClient(authStore.apiClient)
  await authStore.fetchMe()
  await handleFilter('pending')
})
async function selectApproval(id: string): Promise<void> {
  approvalsStore.actionError = null
  decisionReason.value = ''
  approvalsStore.currentApproval = null
  try {
    await approvalsStore.fetchApproval(id)
  } catch {
    /* Store exposes the API error. */
  }
}
async function handleFilter(status: ApprovalStatus | 'all'): Promise<void> {
  activeFilter.value = status
  approvalsStore.actionError = null
  approvalsStore.currentApproval = null
  try {
    await approvalsStore.fetchApprovals(status === 'all' ? undefined : status)
    const first = approvalsStore.approvals[0]
    if (first) await selectApproval(first.approval_id)
  } catch {
    /* Store exposes the API error. */
  }
}
async function handleDecision(decision: 'approve' | 'reject'): Promise<void> {
  const approval = approvalsStore.currentApproval
  if (!approval || approvalsStore.loading || approval.status !== 'pending')
    return
  try {
    await approvalsStore.decide(
      approval.approval_id,
      decision,
      approval.version,
      decisionReason.value,
    )
    decisionReason.value = ''
  } catch {
    /* Store exposes the typed action error. */
  }
}
</script>
<template>
  <AppShell
    active="approvals"
    title="人工审批"
    subtitle="自动化在这里暂停，由你决定是否继续执行。"
    eyebrow="HUMAN IN THE LOOP"
  >
    <template #actions
      ><button
        class="button"
        :disabled="approvalsStore.loading"
        @click="handleFilter(activeFilter)"
      >
        <UiIcon name="refresh" :size="16" />刷新
      </button></template
    >
    <div class="approval-principle">
      <UiIcon name="shield" :size="21" />
      <div>
        <strong>先确认操作，再允许执行</strong>
        <p>高风险操作在审批前保持暂停。通过或拒绝将绑定当前操作和固定参数。</p>
      </div>
      <span class="badge warning">参数不可编辑</span>
    </div>
    <div v-if="approvalsStore.error" class="error-banner" role="alert">
      {{ approvalsStore.error }}
    </div>
    <div class="split-workspace">
      <section class="panel" aria-labelledby="approval-list-heading">
        <div class="panel-heading">
          <h3 id="approval-list-heading">审批队列</h3>
          <span class="mono muted">{{ approvalsStore.approvals.length }}</span>
        </div>
        <div class="approval-filters tabs" aria-label="筛选审批">
          <button
            v-for="tab in filters"
            :key="tab.key"
            :class="['tab', { active: activeFilter === tab.key }]"
            :aria-pressed="activeFilter === tab.key"
            :disabled="approvalsStore.loading"
            @click="handleFilter(tab.key)"
          >
            {{ tab.label }}
          </button>
        </div>
        <div
          v-if="!approvalsStore.approvals.length"
          class="empty-state compact"
        >
          <UiIcon name="shield" :size="28" />
          <p>
            {{
              approvalsStore.loading
                ? '正在加载审批…'
                : '当前筛选下没有审批请求。'
            }}
          </p>
        </div>
        <button
          v-for="item in approvalsStore.approvals"
          :key="item.approval_id"
          :class="[
            'list-row',
            {
              selected:
                approvalsStore.currentApproval?.approval_id ===
                item.approval_id,
            },
          ]"
          :aria-pressed="
            approvalsStore.currentApproval?.approval_id === item.approval_id
          "
          :disabled="approvalsStore.loading"
          @click="selectApproval(item.approval_id)"
        >
          <div class="row-top">
            <span class="row-title">{{
              toolLabels[item.tool_name] || item.tool_name
            }}</span
            ><span :class="['badge', item.status]">{{
              approvalLabels[item.status]
            }}</span>
          </div>
          <div class="row-meta">
            <span :class="['badge', item.risk_level]">{{
              riskLabels[item.risk_level]
            }}</span
            ><time :datetime="item.created_at">{{
              formatDate(item.created_at)
            }}</time>
          </div>
          <div class="row-meta approval-row-id mono">
            #{{ item.approval_id.slice(0, 8) }}
          </div>
        </button>
      </section>
      <section
        class="panel"
        aria-labelledby="approval-detail-heading"
        :aria-busy="approvalsStore.loading"
      >
        <template v-if="approvalsStore.currentApproval">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">OPERATION REVIEW</span>
              <h2 id="approval-detail-heading">
                {{
                  toolLabels[approvalsStore.currentApproval.tool_name] ||
                  approvalsStore.currentApproval.tool_name
                }}
              </h2>
              <p class="mono">{{ approvalsStore.currentApproval.tool_name }}</p>
            </div>
            <span
              :class="['badge', approvalsStore.currentApproval.risk_level]"
              >{{ riskLabels[approvalsStore.currentApproval.risk_level] }}</span
            >
          </div>
          <div class="panel-body">
            <div
              v-if="approvalsStore.actionError"
              class="error-banner"
              data-testid="action-error"
              role="alert"
            >
              <strong>{{ approvalsStore.actionError.code }}:</strong>
              {{ approvalsStore.actionError.message }}
              <p>请刷新审批记录，确认最新状态后再操作。</p>
            </div>
            <dl class="metadata">
              <div>
                <dt>审批状态</dt>
                <dd>
                  <span
                    :class="['badge', approvalsStore.currentApproval.status]"
                    >{{
                      approvalLabels[approvalsStore.currentApproval.status]
                    }}</span
                  >
                </dd>
              </div>
              <div>
                <dt>有效期截至</dt>
                <dd>
                  {{ formatDate(approvalsStore.currentApproval.expires_at) }}
                </dd>
              </div>
              <div>
                <dt>记录版本</dt>
                <dd>v{{ approvalsStore.currentApproval.version }}</dd>
              </div>
            </dl>
            <hr class="divider" />
            <div class="section-heading"><h3>审批原因</h3></div>
            <p class="reason-code">
              {{
                policyReasonLabels[
                  approvalsStore.currentApproval.reason_code
                ] ||
                '该操作需要人工确认。请核对下方参数，并作出通过或拒绝的决定。'
              }}
            </p>
            <hr class="divider" />
            <div class="section-heading">
              <h3>待执行参数</h3>
              <span class="badge"><UiIcon name="lock" :size="12" />已固定</span>
            </div>
            <p class="params-hint">
              请核对订单、金额或目标账户。这些参数在本次审批中不可修改。
            </p>
            <div class="table-scroll">
              <table class="args-table">
                <thead>
                  <tr>
                    <th scope="col">参数</th>
                    <th scope="col">内容</th>
                    <th scope="col">显示状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="arg in approvalsStore.currentApproval.args_summary"
                    :key="arg.name"
                  >
                    <td class="mono">{{ arg.name }}</td>
                    <td class="mono">{{ arg.value }}</td>
                    <td>
                      <span
                        :class="[
                          'badge',
                          arg.value === '***'
                            ? 'badge-sensitive'
                            : 'badge-plain',
                        ]"
                        >{{ arg.value === '***' ? '已脱敏' : '可见' }}</span
                      >
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <details class="technical-detail">
              <summary>操作绑定与追溯信息</summary>
              <dl>
                <dt>审批 ID</dt>
                <dd class="mono">
                  {{ approvalsStore.currentApproval.approval_id }}
                </dd>
                <dt>工单 ID</dt>
                <dd class="mono">
                  {{ approvalsStore.currentApproval.ticket_id }}
                </dd>
                <dt>操作 ID</dt>
                <dd class="mono">
                  {{ approvalsStore.currentApproval.operation_id }}
                </dd>
                <dt>判定代码</dt>
                <dd class="mono">
                  {{ approvalsStore.currentApproval.reason_code }}
                </dd>
                <dt>参数摘要 SHA-256</dt>
                <dd class="mono">
                  {{ approvalsStore.currentApproval.args_hash }}
                </dd>
              </dl>
            </details>
            <hr class="divider" />
            <div
              v-if="
                ['approved', 'rejected'].includes(
                  approvalsStore.currentApproval.status,
                )
              "
              class="notice neutral"
              role="status"
            >
              <UiIcon name="check" :size="19" />
              <div>
                <strong>审批决定已记录</strong>
                <p>
                  {{
                    approvalsStore.currentApproval.status === 'approved'
                      ? '已通过，系统将继续处理原工单。'
                      : '已拒绝，本次操作不会执行。'
                  }}记录版本 v{{ approvalsStore.currentApproval.version }}。
                </p>
              </div>
            </div>
            <div
              v-else-if="approvalsStore.currentApproval.status === 'expired'"
              class="notice warning"
            >
              <UiIcon name="lock" :size="19" />
              <div>
                <strong>审批已过期</strong>
                <p>已超过审批有效期，无法再通过或拒绝，也不能恢复执行。</p>
              </div>
            </div>
            <div
              v-else-if="approvalsStore.currentApproval.status === 'pending'"
              class="decision-form"
            >
              <label for="reason-input"
                >审批备注 <span class="muted">（可选）</span></label
              ><textarea
                id="reason-input"
                v-model="decisionReason"
                rows="2"
                maxlength="1000"
                :disabled="approvalsStore.loading"
                placeholder="记录通过或拒绝的理由，方便后续回查。"
              ></textarea>
              <div class="decision-actions">
                <button
                  class="button primary"
                  data-testid="approve-button"
                  :disabled="approvalsStore.loading"
                  @click="handleDecision('approve')"
                >
                  <UiIcon name="check" :size="16" />通过并继续处理</button
                ><button
                  class="button danger"
                  data-testid="reject-button"
                  :disabled="approvalsStore.loading"
                  @click="handleDecision('reject')"
                >
                  拒绝操作
                </button>
              </div>
            </div>
          </div>
          <div class="approval-footer">
            <button
              class="text-button"
              @click="
                router.push({
                  name: 'traces',
                  params: {
                    ticketId: approvalsStore.currentApproval.ticket_id,
                  },
                })
              "
            >
              查看关联工单轨迹<UiIcon name="arrow" :size="15" />
            </button>
          </div>
        </template>
        <div v-else class="empty-state approval-placeholder">
          <UiIcon name="shield" :size="35" />
          <h2 id="approval-detail-heading">让高风险操作经过人的判断</h2>
          <p>选择一条审批请求，核对固定参数后作出决定。</p>
        </div>
      </section>
    </div>
  </AppShell>
</template>
<style scoped>
.approval-principle {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 26px;
  padding: 20px 0 24px;
  border-bottom: 1px solid var(--line);
}
.approval-principle > svg {
  color: #576f94;
}
.approval-principle strong {
  font-size: 13px;
}
.approval-principle p {
  font-size: 11px;
  color: var(--muted);
  margin-top: 4px;
}
.approval-principle > .badge {
  margin-left: auto;
}
.approval-filters {
  padding: 12px;
  border-bottom: 1px solid var(--line);
  gap: 2px;
}
.approval-filters .tab {
  padding-inline: 7px;
}
.approval-row-id {
  margin-top: 9px;
}
.reason-code {
  background: #f6f8fa;
  border-radius: 4px;
  padding: 10px 12px;
  color: #57677c;
}
.params-hint {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 16px;
}
.badge-sensitive {
  color: var(--warning);
  background: var(--warning-soft);
}
.technical-detail {
  margin-top: 20px;
  font-size: 11px;
  color: var(--muted);
}
.technical-detail summary {
  color: #526b90;
  padding-block: 4px;
}
.technical-detail dl {
  margin: 12px 0 0;
}
.technical-detail dt {
  margin-top: 10px;
}
.technical-detail dd {
  margin: 4px 0 0;
}
.decision-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  flex-wrap: wrap;
}
.approval-footer {
  padding: 14px 24px;
  border-top: 1px solid var(--line);
}
.approval-placeholder {
  min-height: 430px;
}
@media (max-width: 650px) {
  .approval-principle {
    padding-top: 0;
    align-items: flex-start;
  }
  .approval-principle > .badge {
    display: none;
  }
}
</style>

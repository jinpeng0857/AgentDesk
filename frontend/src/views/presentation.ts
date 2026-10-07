import type {
  TicketStatus,
  ApprovalStatus,
  Intent,
  RiskLevel,
} from '../api/types'
export const statusLabels: Record<TicketStatus, string> = {
  queued: '排队中',
  running: '处理中',
  waiting_approval: '待人工审批',
  resolved: '已解决',
  escalated: '需人工接管',
  rejected: '已拒绝',
  failed: '处理失败',
  expired: '已过期',
}
export const approvalLabels: Record<ApprovalStatus, string> = {
  pending: '待审批',
  approved: '已通过',
  rejected: '已拒绝',
  expired: '已过期',
}
export const intentLabels: Record<Intent, string> = {
  network: '网络问题',
  account: '账户操作',
  permission: '权限申请',
  query: '信息查询',
  high_risk: '高风险请求',
}
export const riskLabels: Record<RiskLevel, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}
export const policyReasonLabels: Record<string, string> = {
  requires_refund_approval:
    '退款金额达到 200 美元的人工审批门槛，需要审批人确认后再执行。',
  deny_not_owner: '当前请求涉及不属于请求人的账户或订单。',
  deny_inactive_account: '目标账户未处于活跃状态。',
  deny_unauthorized: '当前身份未满足操作授权要求。',
}
export const toolLabels: Record<string, string> = {
  search_knowledge: '检索知识库',
  query_user: '查询账户',
  query_order: '查询订单',
  check_service: '查询服务状态',
  create_ticket: '创建下游工单',
  request_access: '提交权限申请',
  reset_password: '重置密码',
  process_refund: '处理退款',
}
export function formatDate(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

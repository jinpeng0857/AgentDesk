async (page) => {
  // Fixed API responses for frontend browser verification only, not live acceptance.
  const id = 'a11ce001-1111-4111-8111-111111111111';
  const refundId = 'a11ce002-2222-4222-8222-222222222222';
  const escalatedId = 'a11ce003-3333-4333-8333-333333333333';
  const time = '2026-10-06T06:45:00Z';
  const tickets = [
    { ticket_id: id, requester_id: 'demo-reviewer', status: 'resolved', intent: 'network', risk_level: 'low', resolution_mode: 'automated', updated_at: time, final_response: '建议按以下步骤排查 VPN 连接：\n\n1. 确认本地网络可用，并检查 VPN 客户端的服务器地址。\n2. 检查账号是否有效，以及客户端是否使用正确的认证方式。\n3. 重新连接；若仍失败，请记录错误代码并提交后续工单。\n\n以上建议来自模拟知识库，系统未修改网络配置。', knowledge_refs: [{ document_id: 'KB-VPN-001', chunk_id: 'KB-VPN-001:connection-checks', title: 'VPN 连接排查指南', content_excerpt: '连接失败时，首先确认网络与服务器配置，再检查账户状态和认证方式。保留错误代码，便于后续排查。', score: .91 }] },
    { ticket_id: refundId, requester_id: 'demo-reviewer', status: 'waiting_approval', intent: 'high_risk', risk_level: 'high', resolution_mode: 'human_required', updated_at: time, final_response: null, knowledge_refs: [] },
    { ticket_id: escalatedId, requester_id: 'demo-reviewer', status: 'escalated', intent: 'query', risk_level: null, resolution_mode: 'human_required', updated_at: time, final_response: '未找到足够依据，自动流程已停止，需要人工接管。', knowledge_refs: [] },
  ];
  const approval = { approval_id: 'approval-ui-fixture-001', ticket_id: refundId, operation_id: 'operation-ui-fixture-001', tool_name: 'process_refund', risk_level: 'high', status: 'pending', version: 1, created_at: time, expires_at: '2026-10-06T18:45:00Z', reason_code: 'requires_refund_approval', args_hash: 'f69a8eb3208c350f341bcb479f870c1e70489f1d20f6721701bc7a60c4ce4a909', args_summary: [{ name: 'order_id', value: 'DEMO-ORDER-006' }, { name: 'amount', value: '250.00' }, { name: 'currency', value: 'USD' }, { name: 'requester_id', value: 'demo-reviewer' }, { name: 'payment_reference', value: '***' }] };
  const expired = { ...approval, approval_id: 'approval-ui-fixture-expired', status: 'expired' };
  const run = { run_id: 'ui-fixture-offline-20261006', mode: 'offline', model: 'Deterministic Fake · UI fixture', dataset_version: 'ui-fixture-1', git_sha: 'ui-fixture-no-live-acceptance-evidence', created_at: time, prompt_versions: { supervisor: 'fixture', knowledge: 'fixture', action: 'fixture', policy: 'fixture' }, metrics: [{ name: 'intent_accuracy', value: '1', unit: 'ratio' }, { name: 'high_risk_bypass_count', value: '0', unit: 'count' }, { name: 'latency_p95_ms', value: '1420', unit: 'ms' }] };
  await page.route('http://127.0.0.1:5173/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let body;
    let status = 200;
    if (path === '/api/auth/login') body = { access_token: 'ui-fixture-only', token_type: 'bearer', expires_in_s: 900 };
    else if (path === '/api/auth/me') body = { user_id: 'demo-reviewer', role: 'approver' };
    else if (path === '/api/tickets' && request.method() === 'POST') { body = { ticket_id: id, status: 'queued', trace_id: 'trace-ui-fixture' }; status = 202; }
    else if (path === '/api/tickets') body = tickets;
    else if (path.endsWith('/trace')) body = [
      { actor: 'supervisor', event_type: 'agent_step', summary: '意图已分类为 network，建议交给 Knowledge 检索。' },
      { actor: 'dispatcher', event_type: 'agent_step', summary: '路由校验完成：supervisor → knowledge。' },
      { actor: 'knowledge', event_type: 'tool_call:search_knowledge', summary: 'outcome=succeeded 返回 1 条可回查引用；query=VPN connection。' },
      { actor: 'knowledge', event_type: 'agent_step', summary: '追加 knowledge_refs：KB-VPN-001 / connection-checks。' },
      { actor: 'supervisor', event_type: 'agent_step', summary: '已形成最终答复；resolution_mode=automated。' },
    ].map((event, i) => ({ ...event, request_id: 'request-ui-fixture-' + i, trace_id: 'trace-ui-fixture-network', timestamp: '2026-10-06T06:4' + i + ':00Z' }));
    else if (path.startsWith('/api/tickets/')) body = tickets.find(t => path.endsWith(t.ticket_id)) || tickets[0];
    else if (path === '/api/approvals') { const filter = url.searchParams.get('status'); body = filter === 'expired' ? [expired] : filter && filter !== approval.status ? [] : filter ? [approval] : [approval, expired]; }
    else if (path.endsWith('/decision')) { const input = request.postDataJSON(); approval.status = input.decision === 'approve' ? 'approved' : 'rejected'; approval.version++; body = { approval_id: approval.approval_id, status: approval.status, version: approval.version }; }
    else if (path.startsWith('/api/approvals/')) body = path.endsWith(expired.approval_id) ? expired : approval;
    else if (path === '/api/eval/runs') body = [run];
    else if (path.startsWith('/api/eval/runs/')) body = run;
    else { status = 404; body = { error: { code: 'UI_FIXTURE_NOT_FOUND', message: 'Unknown fixture endpoint', request_id: 'ui-fixture' } }; }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

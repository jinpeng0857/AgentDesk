"""PostgreSQL checkpoint construction with an explicit deserialization allow-list."""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg_pool import AsyncConnectionPool

CHECKPOINT_ALLOWED_MSGPACK_TYPES: frozenset[tuple[str, str]] = frozenset(
    {
        ("agents.schemas", "AgentStep"),
        ("agents.schemas", "ActionProposal"),
        ("agents.schemas", "ApprovalSnapshot"),
        ("agents.schemas", "ApprovalStatus"),
        ("agents.schemas", "Intent"),
        ("agents.schemas", "KnowledgeRef"),
        ("agents.schemas", "PendingOperation"),
        ("agents.schemas", "PolicyReason"),
        ("agents.schemas", "ResolutionMode"),
        ("agents.schemas", "RiskDecision"),
        ("agents.schemas", "RiskLevel"),
        ("agents.schemas", "RoutingDecision"),
        ("agents.schemas", "RoutingReason"),
        ("agents.schemas", "TicketStatus"),
        ("agents.schemas", "ToolOutcome"),
        ("agents.schemas", "ToolResult"),
        ("tools.schemas", "AccountStatus"),
        ("tools.schemas", "CheckServiceArgs"),
        ("tools.schemas", "CheckServiceResult"),
        ("tools.schemas", "CreateTicketArgs"),
        ("tools.schemas", "CreateTicketResult"),
        ("tools.schemas", "KnowledgeSearchItem"),
        ("tools.schemas", "OrderStatus"),
        ("tools.schemas", "ProcessRefundArgs"),
        ("tools.schemas", "ProcessRefundResult"),
        ("tools.schemas", "QueryOrderArgs"),
        ("tools.schemas", "QueryOrderResult"),
        ("tools.schemas", "QueryUserArgs"),
        ("tools.schemas", "QueryUserResult"),
        ("tools.schemas", "RequestAccessArgs"),
        ("tools.schemas", "RequestAccessResult"),
        ("tools.schemas", "ResetPasswordArgs"),
        ("tools.schemas", "ResetPasswordResult"),
        ("tools.schemas", "SearchKnowledgeArgs"),
        ("tools.schemas", "SearchKnowledgeResult"),
        ("tools.schemas", "ServiceStatus"),
        ("tools.schemas", "WriteResultStatus"),
    }
)


def create_checkpoint_serializer() -> JsonPlusSerializer:
    """Allow only the versioned domain contracts that may enter TicketState."""
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=CHECKPOINT_ALLOWED_MSGPACK_TYPES,
    )


def create_postgres_checkpointer(
    connection_pool: AsyncConnectionPool,
) -> AsyncPostgresSaver:
    """Build the runtime saver using strict JSON+ serialization and no pickle fallback."""
    return AsyncPostgresSaver(connection_pool, serde=create_checkpoint_serializer())

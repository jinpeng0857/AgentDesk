"""Runtime dependency container for LangGraph node closures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.context import ModelClient
from backend.config import AppSettings
from backend.integrations.prompt_loader import LoadedPrompt


@dataclass(frozen=True, slots=True)
class GraphContext:
    model_client: ModelClient
    prompts: dict[str, LoadedPrompt]
    settings: AppSettings
    tool_executor: Any | None = None
    session_factory: Any | None = None

"""Strict Prompt YAML loading.

Prompts are addressed by a fixed agent identifier, never by a caller-supplied
path, and untrusted text is wrapped in explicit delimiters before it reaches a
model so retrieved content can never be mistaken for instructions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

AgentId = Literal["supervisor", "knowledge", "action", "policy"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROMPT_DIRECTORY = REPOSITORY_ROOT / "resources" / "prompts"

AGENT_IDS: tuple[AgentId, ...] = ("supervisor", "knowledge", "action", "policy")

PROMPT_FILES: dict[AgentId, str] = {
    "supervisor": "supervisor.yaml",
    "knowledge": "knowledge.yaml",
    "action": "action.yaml",
    "policy": "policy.yaml",
}

REQUIRED_FIELDS = frozenset(
    {"version", "role", "responsibility", "constraints", "output_schema"}
)

UNTRUSTED_OPEN = "<<<UNTRUSTED_DATA>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_DATA>>>"


class PromptError(RuntimeError):
    """Raised when a prompt is missing, malformed or addressed unsafely."""


class PromptOutputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    strict: bool


class LoadedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: AgentId
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    role: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    constraints: str = Field(min_length=1)
    output_schema: PromptOutputSchema


def load_prompt(agent_id: AgentId, *, directory: Path | None = None) -> LoadedPrompt:
    filename = PROMPT_FILES.get(agent_id)
    if filename is None:
        raise PromptError(f"unknown agent id: {agent_id!r}")

    base = directory if directory is not None else PROMPT_DIRECTORY
    path = base / filename
    if not path.is_file():
        raise PromptError(f"prompt file is missing for agent {agent_id!r}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PromptError(f"prompt for agent {agent_id!r} is not valid YAML") from exc

    if not isinstance(raw, dict):
        raise PromptError(f"prompt for agent {agent_id!r} must be a mapping")

    fields = set(raw)
    if fields != REQUIRED_FIELDS:
        missing = sorted(REQUIRED_FIELDS - fields)
        extra = sorted(fields - REQUIRED_FIELDS)
        raise PromptError(
            f"prompt for agent {agent_id!r} has wrong fields: "
            f"missing={missing} extra={extra}"
        )

    try:
        return LoadedPrompt(agent_id=agent_id, **raw)
    except ValidationError as exc:
        raise PromptError(f"prompt for agent {agent_id!r} is invalid") from exc


def load_all_prompts(*, directory: Path | None = None) -> dict[str, LoadedPrompt]:
    return {agent: load_prompt(agent, directory=directory) for agent in AGENT_IDS}


def wrap_untrusted(label: str, content: str) -> str:
    return f"[{label}]\n{UNTRUSTED_OPEN}\n{content}\n{UNTRUSTED_CLOSE}"


def build_system_message(
    prompt: LoadedPrompt,
    *,
    user_query: str,
    retrieved_text: str | None = None,
    include_user_query: bool = True,
) -> str:
    sections = [
        prompt.role.strip(),
        f"Responsibility:\n{prompt.responsibility.strip()}",
        f"Constraints:\n{prompt.constraints.strip()}",
        f"Required output schema: {prompt.output_schema.model} "
        f"(strict={str(prompt.output_schema.strict).lower()})",
    ]
    if include_user_query:
        sections.append(wrap_untrusted("UNTRUSTED USER REQUEST", user_query))
    if retrieved_text is not None:
        sections.append(wrap_untrusted("UNTRUSTED RETRIEVED TEXT", retrieved_text))
    return "\n\n".join(sections)

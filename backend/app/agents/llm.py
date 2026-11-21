"""Chat model factory.

Notes for Claude Opus 5.5 (the default model):
* Thinking is adaptive and cannot be disabled; depth is set with ``effort``.
* Forced ``tool_choice`` is rejected, so structured output must use
  ``method="json_schema"`` (native ``output_config.format``), never the default
  tool-forcing method.
* ``temperature`` is not accepted, so it is never set.
* Server-side refusal fallback is enabled by default.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from app.config import get_settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@lru_cache(maxsize=8)
def chat_model(role: str) -> ChatAnthropic:
    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (add it to backend/.env)")
    effort = {
        "analyst": s.effort_analyst,
        "critic": s.effort_critic,
        "researcher": s.effort_researcher,
        "coordinator": s.effort_coordinator,
    }.get(role, "medium")
    kwargs: dict = {}
    if s.llm_server_fallbacks:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["model_kwargs"] = {"fallbacks": "default"}
    return ChatAnthropic(
        model=s.llm_model,
        api_key=s.anthropic_api_key,
        reasoning_effort=effort,
        max_tokens=16000,
        max_retries=3,
        default_request_timeout=300,
        **kwargs,
    )


def structured(role: str, schema: type):
    return chat_model(role).with_structured_output(schema, method="json_schema")

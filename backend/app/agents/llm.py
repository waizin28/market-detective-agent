"""Chat model factory (OpenAI via langchain-openai).

* Structured output uses OpenAI's native strict JSON schema
  (``method="json_schema", strict=True``), so responses always match the
  Pydantic models the graph expects.
* Reasoning depth per role is set with ``reasoning_effort``; ``temperature``
  is left unset (reasoning models reject it).
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache(maxsize=8)
def chat_model(role: str) -> ChatOpenAI:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (add it to backend/.env)")
    effort = {
        "analyst": s.effort_analyst,
        "critic": s.effort_critic,
        "researcher": s.effort_researcher,
        "coordinator": s.effort_coordinator,
    }.get(role, "medium")
    kwargs: dict = {}
    if effort:
        kwargs["reasoning_effort"] = effort
    return ChatOpenAI(
        model=s.llm_model,
        api_key=s.openai_api_key,
        max_retries=3,
        request_timeout=300,
        **kwargs,
    )


def structured(role: str, schema: type):
    return chat_model(role).with_structured_output(schema, method="json_schema", strict=True)

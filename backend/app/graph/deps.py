"""Runtime dependencies injected into the graph via LangGraph's ``context``."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.roles import LLMRoles
from app.config import Settings
from app.data.fred import Fred
from app.data.http import CachedHttp
from app.data.market import MarketData
from app.data.news import News
from app.data.sec import SecEdgar
from app.retrieval.semantic import FilingRetriever


@dataclass
class Deps:
    settings: Settings
    http: CachedHttp
    market: MarketData
    sec: SecEdgar
    news: News
    fred: Fred
    retriever: FilingRetriever
    roles: LLMRoles

    @classmethod
    def build(cls, settings: Settings) -> "Deps":
        http = CachedHttp(settings.http_cache_db)
        return cls(
            settings=settings,
            http=http,
            market=MarketData(http, settings.alpha_vantage_api_key, settings.market_data_provider),
            sec=SecEdgar(http, settings.sec_user_agent),
            news=News(http, settings.newsapi_key),
            fred=Fred(http, settings.fred_api_key),
            retriever=FilingRetriever(
                settings.embedding_model, settings.chunk_tokens, settings.chunk_overlap_tokens
            ),
            roles=LLMRoles(settings.researcher_max_tool_calls),
        )

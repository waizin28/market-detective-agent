"""On-demand semantic retrieval over a single filing (Week 3 design).

No permanent vector database: a filing is split into sections, chunked into
~400-token pieces with 50-token overlap, embedded with Sentence Transformers,
and searched with cosine similarity. Indexes are cached in-process per filing
so a second question about the same document doesn't re-embed it.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 10-K / 10-Q item headings, e.g. "Item 1A. Risk Factors", "ITEM 7. MANAGEMENT'S DISCUSSION"
SECTION_RE = re.compile(r"^\s*(item\s+\d{1,2}[a-c]?)[\.\:\s]+([^\n]{0,90})", re.I | re.M)


@dataclass
class Passage:
    section: str
    text: str
    score: float
    chunk_index: int


def split_sections(text: str) -> list[tuple[str, str]]:
    matches = list(SECTION_RE.finditer(text))
    if len(matches) < 3:  # 8-Ks and press releases: treat as one section
        return [("Document", text)]
    sections = []
    if matches[0].start() > 200:
        sections.append(("Preamble", text[: matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = f"{m.group(1).title()} {m.group(2).strip()}".strip()
        body = text[m.start() : end]
        if len(body) > 200:  # skip table-of-contents entries
            sections.append((title[:80], body))
    return sections or [("Document", text)]


class FilingRetriever:
    def __init__(
        self,
        model_name: str,
        chunk_tokens: int = 400,
        overlap_tokens: int = 50,
        max_cached: int = 16,
        embeddings: Embeddings | None = None,
    ):
        self._model_name = model_name
        self._injected = embeddings
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="cl100k_base", chunk_size=chunk_tokens, chunk_overlap=overlap_tokens
        )
        self._stores: OrderedDict[str, InMemoryVectorStore] = OrderedDict()
        self._max_cached = max_cached
        self._lock = asyncio.Lock()

    @cached_property
    def embeddings(self) -> Embeddings:
        if self._injected is not None:
            return self._injected
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(
            model_name=self._model_name, encode_kwargs={"normalize_embeddings": True}
        )

    def chunk(self, text: str) -> list[Document]:
        docs = []
        for section, body in split_sections(text):
            for piece in self._splitter.split_text(body):
                docs.append(Document(page_content=piece, metadata={"section": section, "i": len(docs)}))
        return docs

    async def search(self, doc_key: str, text: str, query: str, k: int = 5) -> list[Passage]:
        async with self._lock:
            store = self._stores.get(doc_key)
            if store is None:
                docs = self.chunk(text)
                store = InMemoryVectorStore(self.embeddings)
                await asyncio.to_thread(store.add_documents, docs)
                self._stores[doc_key] = store
                if len(self._stores) > self._max_cached:
                    self._stores.popitem(last=False)
            else:
                self._stores.move_to_end(doc_key)
        hits = await asyncio.to_thread(store.similarity_search_with_score, query, k)
        return [
            Passage(section=d.metadata["section"], text=d.page_content, score=float(s), chunk_index=d.metadata["i"])
            for d, s in hits
        ]

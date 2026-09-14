"""Chroma knowledge retrieval returns traceable, bounded citations."""

from __future__ import annotations

from pathlib import Path

from backend.integrations.knowledge_store import ChromaKnowledgeStore
from scripts.seed_demo_data import seed_knowledge
from tools.schemas import SearchKnowledgeArgs


def build_store(tmp_path: Path) -> ChromaKnowledgeStore:
    seed_knowledge(tmp_path)
    return ChromaKnowledgeStore(tmp_path)


async def test_vpn_query_ranks_the_seeded_document_first(tmp_path: Path) -> None:
    """The offline embedding is lexical, so this asserts ranking, not semantics."""
    store = build_store(tmp_path)

    result = await store.search(SearchKnowledgeArgs(query="公司 VPN 无法连接", top_k=3))

    assert result.refs, "the seeded VPN SOP must be retrievable"
    assert result.refs[0].document_id == "doc-network-vpn"
    for ref in result.refs:
        assert ref.title
        assert ref.content_excerpt
        assert 0.0 <= ref.score <= 1.0


async def test_every_citation_field_is_populated(tmp_path: Path) -> None:
    store = build_store(tmp_path)

    result = await store.search(SearchKnowledgeArgs(query="工单升级时限", top_k=5))

    assert result.refs
    for ref in result.refs:
        payload = ref.model_dump()
        assert set(payload) == {
            "document_id",
            "chunk_id",
            "title",
            "content_excerpt",
            "score",
        }
        assert all(value not in (None, "") for value in payload.values())


async def test_top_k_bounds_the_result_count(tmp_path: Path) -> None:
    store = build_store(tmp_path)

    result = await store.search(SearchKnowledgeArgs(query="VPN 客户端版本", top_k=1))

    assert len(result.refs) == 1


async def test_empty_collection_returns_no_references(tmp_path: Path) -> None:
    store = ChromaKnowledgeStore(tmp_path / "empty")

    result = await store.search(SearchKnowledgeArgs(query="anything", top_k=3))

    assert result.refs == []

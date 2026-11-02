"""Knowledge retrieval rejects ambiguous mixed selection modes."""

import pytest

import astrbot.api  # noqa: F401 - initialize the plugin API before the knowledge manager.
from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager


@pytest.mark.asyncio
async def test_retrieval_rejects_names_and_ids_together(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "astrbot.core.knowledge_base.kb_mgr.DB_PATH", tmp_path / "kb.db"
    )
    manager = KnowledgeBaseManager(None)
    with pytest.raises(ValueError, match="name or by ID"):
        await manager.retrieve("query", kb_names=["name"], kb_ids=["id"])

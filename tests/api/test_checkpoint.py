# SPDX-License-Identifier: Apache-2.0

"""Tests for checkpointer construction."""

import pytest

from migratowl.api.checkpoint import create_checkpointer
from migratowl.config import Settings


class TestCreateCheckpointer:
    @pytest.mark.asyncio
    async def test_memory_backend_returns_in_memory_saver(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        settings = Settings(_env_file=None, persistence_backend="memory")
        async with create_checkpointer(settings) as saver:
            assert isinstance(saver, InMemorySaver)

    @pytest.mark.asyncio
    async def test_sqlite_backend_returns_async_sqlite_saver(self, tmp_path) -> None:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        settings = Settings(
            _env_file=None,
            persistence_backend="sqlite",
            checkpoint_db_path=str(tmp_path / "cp.db"),
        )
        async with create_checkpointer(settings) as saver:
            assert isinstance(saver, AsyncSqliteSaver)

    @pytest.mark.asyncio
    async def test_sqlite_checkpointer_is_usable(self, tmp_path) -> None:
        """After entering the context the saver has run setup() and can be listed."""
        settings = Settings(
            _env_file=None,
            persistence_backend="sqlite",
            checkpoint_db_path=str(tmp_path / "cp.db"),
        )
        async with create_checkpointer(settings) as saver:
            config = {"configurable": {"thread_id": "t1"}}
            # No checkpoint yet — aget returns None without raising (tables exist).
            assert await saver.aget(config) is None

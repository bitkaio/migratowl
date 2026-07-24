# Copyright bitkaio LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LangGraph checkpointer construction for durable agent state.

``create_checkpointer`` is an async context manager yielding the checkpointer
for the configured backend. The FastAPI lifespan enters it via an
``AsyncExitStack`` so the checkpointer (and its underlying aiosqlite connection)
lives for the whole app lifetime and is closed cleanly on shutdown.

The checkpointer, keyed by ``thread_id == job_id``, is what lets an interrupted
scan resume its agent reasoning from the last super-step.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from migratowl.config import Settings


@asynccontextmanager
async def create_checkpointer(settings: Settings) -> AsyncIterator[Any]:
    """Yield a LangGraph checkpointer for the configured persistence backend.

    - ``sqlite``: durable ``AsyncSqliteSaver`` on ``checkpoint_db_path`` (its own
      file, separate from the jobs DB to avoid cross-writer WAL contention).
      ``setup()`` (idempotent) creates the checkpoint tables on entry.
    - ``memory``: ``InMemorySaver`` — non-durable; agent state is lost on restart.
    """
    if settings.persistence_backend == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.memory import InMemorySaver

        async with InMemorySaver() as saver:
            yield saver

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

"""Logging for the ``migratowl`` package.

uvicorn configures only its own loggers, so without this the pipeline's INFO
lines (candidates, update failures, validation per ecosystem) never appear.
"""

import logging

_HANDLER_NAME = "migratowl"


class _FallbackHandler(logging.StreamHandler):
    """Prints only while the root logger has no handlers (e.g. under plain uvicorn).

    When something configures the root logger (``basicConfig``, pytest), records
    propagate there instead, so each line is printed exactly once.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if not logging.getLogger().handlers:
            super().emit(record)


def configure_logging(level: str) -> None:
    """Show ``migratowl.*`` records at ``level`` and above (idempotent)."""
    logger = logging.getLogger("migratowl")
    logger.setLevel(level.upper())
    if not any(h.get_name() == _HANDLER_NAME for h in logger.handlers):
        handler = _FallbackHandler()
        handler.set_name(_HANDLER_NAME)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)

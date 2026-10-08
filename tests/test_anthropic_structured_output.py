# SPDX-License-Identifier: Apache-2.0

"""Anthropic request/response shape through the real SDK, against a local stub server.

Mocked models cannot catch an SDK upgrade changing what is sent or how a reply is parsed. The agent asks
Claude 5.x models for native structured output (``output_config.format``) and must never force a tool
choice, which those models reject with a 400.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import UsageMetadataCallbackHandler

from migratowl.api.helpers import extract_verdicts, sum_usage
from migratowl.models.schemas import PackageVerdicts

VERDICTS = {"reports": [{
    "dependency_name": "foo", "is_breaking": True, "error_summary": "ImportError",
    "changelog_citation": "2.0 removed bar", "suggested_human_fix": "use baz", "confidence": 0.8,
}]}


class _Stub(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.requests.append({"path": self.path, "body": body})
        reply = {
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(VERDICTS)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 40,
                      "cache_read_input_tokens": 30, "cache_creation_input_tokens": 20},
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def stub_url():
    _Stub.requests = []
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


async def test_native_structured_output_round_trip(stub_url: str) -> None:
    usage = UsageMetadataCallbackHandler()
    model = init_chat_model(
        "anthropic:claude-sonnet-5-5", base_url=stub_url, api_key="test", max_retries=0, callbacks=[usage],
    )
    agent = create_agent(model, tools=[], response_format=ProviderStrategy(PackageVerdicts))

    result = await agent.ainvoke({"messages": [("user", "analyze foo")]})

    body = _Stub.requests[0]["body"]
    assert _Stub.requests[0]["path"].endswith("/v1/messages")
    assert "tool_choice" not in body, "Claude 5.x rejects a forced tool choice"
    assert "output_config" in body and "format" in body["output_config"]
    assert body["output_config"]["format"]["type"] == "json_schema"

    verdicts = extract_verdicts(result)
    assert [(v.dependency_name, v.is_breaking, v.confidence) for v in verdicts] == [("foo", True, 0.8)]


async def test_usage_includes_cache_tokens(stub_url: str) -> None:
    usage = UsageMetadataCallbackHandler()
    model = init_chat_model(
        "anthropic:claude-sonnet-5-5", base_url=stub_url, api_key="test", max_retries=0, callbacks=[usage],
    )
    agent = create_agent(model, tools=[], response_format=ProviderStrategy(PackageVerdicts))

    await agent.ainvoke({"messages": [("user", "analyze foo")]})

    tokens = sum_usage(usage.usage_metadata.values())
    assert (tokens.cache_read, tokens.cache_creation, tokens.output) == (30, 20, 40)
    assert tokens.input == 150  # input includes cache reads and writes

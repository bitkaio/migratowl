"""Tests for ScanWebhookPayload schema."""

import pytest
from pydantic import ValidationError

from migratowl.models.schemas import ScanWebhookPayload


class TestScanWebhookPayload:
    def test_valid_payload_all_fields(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/psf/requests",
            branch_name="develop",
            callback_url="https://example.com/callback",
        )
        assert payload.repo_url == "https://github.com/psf/requests"
        assert payload.branch_name == "develop"
        assert payload.callback_url == "https://example.com/callback"

    def test_defaults(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/psf/requests")
        assert payload.branch_name == "main"
        assert payload.callback_url is None

    def test_repo_url_required(self) -> None:
        with pytest.raises(ValidationError):
            ScanWebhookPayload()  # type: ignore[call-arg]

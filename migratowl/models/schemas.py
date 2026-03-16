"""Webhook and API schemas for MigratOwl."""

from pydantic import BaseModel


class ScanWebhookPayload(BaseModel):
    """Payload received from the UI to trigger a repository scan."""

    repo_url: str
    branch_name: str = "main"
    callback_url: str | None = None

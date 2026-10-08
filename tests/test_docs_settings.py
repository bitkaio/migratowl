"""Every setting is documented in the README and listed in .env.example."""

from pathlib import Path

import pytest
from pydantic import AliasChoices

from migratowl.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def _env_names(field: str) -> set[str]:
    alias = Settings.model_fields[field].validation_alias
    if isinstance(alias, AliasChoices):
        return {str(choice) for choice in alias.choices}
    return {f"MIGRATOWL_{field.upper()}"}


@pytest.mark.parametrize("doc", ["README.md", ".env.example"])
def test_every_setting_is_documented(doc: str) -> None:
    text = (ROOT / doc).read_text()
    missing = [field for field in Settings.model_fields if not any(n in text for n in _env_names(field))]
    assert missing == []

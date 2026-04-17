"""Tests for optional LangFuse observability integration."""

import builtins
from unittest.mock import MagicMock, patch

import pytest

from migratowl.config import Settings


class TestCreateLangfuseHandler:
    def test_returns_none_when_keys_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        import migratowl.observability as obs_module

        result = obs_module.create_langfuse_handler(settings=Settings(_env_file=None))
        assert result is None

    def test_returns_none_when_only_public_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        import migratowl.observability as obs_module

        settings = Settings(_env_file=None)
        settings.langfuse_public_key = "pk-test"
        result = obs_module.create_langfuse_handler(settings=settings)
        assert result is None

    def test_returns_none_when_only_secret_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        import migratowl.observability as obs_module

        settings = Settings(_env_file=None)
        settings.langfuse_secret_key = "sk-test"
        result = obs_module.create_langfuse_handler(settings=settings)
        assert result is None

    def test_returns_handler_when_both_keys_present(self) -> None:
        mock_handler = MagicMock()
        mock_callback_handler_cls = MagicMock(return_value=mock_handler)

        settings = Settings(_env_file=None)
        settings.langfuse_public_key = "pk-test"
        settings.langfuse_secret_key = "sk-test"

        with patch.dict("sys.modules", {"langfuse.langchain": MagicMock(CallbackHandler=mock_callback_handler_cls)}):
            import migratowl.observability as obs_module

            result = obs_module.create_langfuse_handler(settings=settings)

        assert result is mock_handler
        mock_callback_handler_cls.assert_called_once()

    def test_raises_import_error_when_package_missing(self) -> None:
        settings = Settings(_env_file=None)
        settings.langfuse_public_key = "pk-test"
        settings.langfuse_secret_key = "sk-test"

        original_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):
            if name == "langfuse.langchain":
                raise ImportError("No module named 'langfuse'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            # Re-import to bypass module cache
            import importlib

            import migratowl.observability as obs_module

            importlib.reload(obs_module)

            with pytest.raises(ImportError, match="langfuse.*not installed"):
                obs_module.create_langfuse_handler(settings=settings)


class TestGetInvokeConfig:
    def test_returns_empty_dict_when_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        result = obs_module.get_invoke_config()
        assert result == {}

    def test_returns_empty_dict_without_session_id_when_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        result = obs_module.get_invoke_config(session_id="scan-42")
        assert result == {}

    def test_returns_config_with_callbacks_when_configured(self) -> None:
        mock_handler = MagicMock()

        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        with patch.object(obs_module, "_langfuse_handler", mock_handler):
            result = obs_module.get_invoke_config()

        assert "callbacks" in result
        assert mock_handler in result["callbacks"]

    def test_includes_session_id_in_metadata(self) -> None:
        mock_handler = MagicMock()

        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        with patch.object(obs_module, "_langfuse_handler", mock_handler):
            result = obs_module.get_invoke_config(session_id="repo-owner-myrepo")

        assert result.get("metadata", {}).get("langfuse_session_id") == "repo-owner-myrepo"

    def test_no_metadata_key_when_no_session_id(self) -> None:
        mock_handler = MagicMock()

        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        with patch.object(obs_module, "_langfuse_handler", mock_handler):
            result = obs_module.get_invoke_config()

        assert "metadata" not in result


class TestInjectSessionId:
    def test_injects_thread_id_as_session_id(self) -> None:
        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        config = {"configurable": {"thread_id": "thread-abc"}}
        with patch.object(obs_module, "_langfuse_handler", MagicMock()):
            result = obs_module.inject_session_id(config)

        assert result["metadata"]["langfuse_session_id"] == "thread-abc"

    def test_preserves_existing_session_id(self) -> None:
        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        config = {
            "configurable": {"thread_id": "thread-abc"},
            "metadata": {"langfuse_session_id": "explicit-session"},
        }
        with patch.object(obs_module, "_langfuse_handler", MagicMock()):
            result = obs_module.inject_session_id(config)

        assert result["metadata"]["langfuse_session_id"] == "explicit-session"

    def test_no_op_when_no_thread_id(self) -> None:
        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        config = {"configurable": {}, "metadata": {}}
        with patch.object(obs_module, "_langfuse_handler", MagicMock()):
            result = obs_module.inject_session_id(config)

        assert "langfuse_session_id" not in result.get("metadata", {})

    def test_no_op_when_handler_not_configured(self) -> None:
        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        config = {"configurable": {"thread_id": "thread-abc"}}
        with patch.object(obs_module, "_langfuse_handler", None):
            result = obs_module.inject_session_id(config)

        assert result == config

    def test_preserves_other_metadata_keys(self) -> None:
        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        config = {
            "configurable": {"thread_id": "thread-abc"},
            "metadata": {"other_key": "other_value"},
        }
        with patch.object(obs_module, "_langfuse_handler", MagicMock()):
            result = obs_module.inject_session_id(config)

        assert result["metadata"]["other_key"] == "other_value"
        assert result["metadata"]["langfuse_session_id"] == "thread-abc"

    def test_handles_none_config(self) -> None:
        import importlib

        import migratowl.observability as obs_module

        importlib.reload(obs_module)

        with patch.object(obs_module, "_langfuse_handler", MagicMock()):
            result = obs_module.inject_session_id(None)

        assert result is None

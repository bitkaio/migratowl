# SPDX-License-Identifier: Apache-2.0

"""Tests for configure_registries: point the sandbox's package tools at the configured mirrors."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from migratowl.agent.tools.registries import create_configure_registries_tool
from migratowl.config import Settings
from migratowl.registries import SANDBOX_HOME, Registries
from tests.conftest import ExecResult

SECRET = "s3cr3t-password"


def _registries(**env: str) -> Registries:
    return Registries.from_settings(Settings(_env_file=None, **env))


def _backend(upload_errors: dict[str, str] | None = None) -> MagicMock:
    backend = MagicMock()
    backend.execute.return_value = ExecResult(output="", exit_code=0)
    backend.upload_files.side_effect = lambda files: [
        SimpleNamespace(path=path, error=(upload_errors or {}).get(path)) for path, _ in files
    ]
    return backend


def test_public_registries_touch_nothing() -> None:
    backend = _backend()
    tool = create_configure_registries_tool(lambda: backend, Registries())

    result = tool.invoke({})

    assert "public" in result.lower()
    backend.execute.assert_not_called()
    backend.upload_files.assert_not_called()


def test_config_files_are_uploaded_as_content_not_as_command_arguments() -> None:
    backend = _backend()
    registries = _registries(
        npm_registry_url="https://npm.corp", pypi_url="https://pypi.corp",
        registry_username="svc", registry_password=SECRET,
    )

    result = create_configure_registries_tool(lambda: backend, registries).invoke({})

    uploaded = dict(backend.upload_files.call_args.args[0])
    assert set(uploaded) == {f"{SANDBOX_HOME}/.npmrc", f"{SANDBOX_HOME}/.config/pip/pip.conf"}
    assert SECRET.encode() in uploaded[f"{SANDBOX_HOME}/.config/pip/pip.conf"].replace(b"%2D", b"-")
    for call in backend.execute.call_args_list:  # mkdir only
        assert SECRET not in call.args[0]
        assert call.args[0].startswith("mkdir -p")
    assert SECRET not in result
    assert ".npmrc" in result and "pip.conf" in result


def test_parent_directories_are_created_first() -> None:
    backend = _backend()
    create_configure_registries_tool(lambda: backend, _registries(maven_url="https://mvn.corp")).invoke({})

    assert any(f"{SANDBOX_HOME}/.m2" in c.args[0] for c in backend.execute.call_args_list)


def test_upload_failure_is_reported_without_credentials() -> None:
    path = f"{SANDBOX_HOME}/.npmrc"
    backend = _backend({path: "permission_denied"})
    registries = _registries(npm_registry_url="https://npm.corp", registry_token=SECRET)

    result = create_configure_registries_tool(lambda: backend, registries).invoke({})

    assert result.startswith("Failed")
    assert path in result and SECRET not in result

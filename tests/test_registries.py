# SPDX-License-Identifier: Apache-2.0

"""Registry endpoints, optional mirrors, and the config files that point the sandbox at them."""

import httpx
import pytest

from migratowl.config import Settings
from migratowl.registries import SANDBOX_HOME, Registries


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)


class TestDefaults:
    def test_public_registries(self) -> None:
        r = Registries()
        assert r.pypi_json_url("requests") == "https://pypi.org/pypi/requests/json"
        assert r.npm_url("@scope/pkg") == "https://registry.npmjs.org/@scope/pkg"
        assert r.crates_url("serde") == "https://crates.io/api/v1/crates/serde"
        assert r.go_list_url("example.com/a/b") == "https://proxy.golang.org/example.com/a/b/@v/list"
        assert r.maven_metadata_url("org.x", "y") is None

    def test_nothing_to_configure_in_the_sandbox(self) -> None:
        assert Registries().sandbox_files() == {}

    def test_from_settings_without_overrides_is_the_default(self) -> None:
        assert Registries.from_settings(_settings()) == Registries()


class TestMirrors:
    def _mirrors(self) -> Registries:
        return Registries.from_settings(_settings(
            pypi_url="https://repo.corp/artifactory/api/pypi/py/",
            npm_registry_url="https://repo.corp/artifactory/api/npm/npm/",
            go_proxy_url="https://repo.corp/artifactory/api/go/go",
            crates_api_url="https://crates.corp",
            cargo_registry_url="sparse+https://crates.corp/index/",
            maven_url="https://repo.corp/artifactory/maven",
        ))

    def test_check_urls_follow_the_mirror(self) -> None:
        r = self._mirrors()
        assert r.pypi_json_url("requests") == "https://repo.corp/artifactory/api/pypi/py/pypi/requests/json"
        assert r.npm_url("left-pad") == "https://repo.corp/artifactory/api/npm/npm/left-pad"
        assert r.crates_url("serde") == "https://crates.corp/api/v1/crates/serde"
        assert r.go_list_url("a.b/c") == "https://repo.corp/artifactory/api/go/go/a.b/c/@v/list"
        assert r.maven_metadata_url("org.x.y", "lib") == (
            "https://repo.corp/artifactory/maven/org/x/y/lib/maven-metadata.xml"
        )

    def test_sandbox_files_point_every_tool_at_the_mirror(self) -> None:
        files = self._mirrors().sandbox_files()
        assert set(files) == {
            f"{SANDBOX_HOME}/.config/pip/pip.conf",
            f"{SANDBOX_HOME}/.npmrc",
            f"{SANDBOX_HOME}/.config/go/env",
            f"{SANDBOX_HOME}/.cargo/config.toml",
            f"{SANDBOX_HOME}/.m2/settings.xml",
        }
        pip_conf = files[f"{SANDBOX_HOME}/.config/pip/pip.conf"]
        assert "index-url = https://repo.corp/artifactory/api/pypi/py/simple" in pip_conf
        assert "registry=https://repo.corp/artifactory/api/npm/npm/" in files[f"{SANDBOX_HOME}/.npmrc"]
        assert "GOPROXY=https://repo.corp/artifactory/api/go/go" in files[f"{SANDBOX_HOME}/.config/go/env"]
        cargo = files[f"{SANDBOX_HOME}/.cargo/config.toml"]
        assert 'replace-with = "migratowl-mirror"' in cargo and "sparse+https://crates.corp/index/" in cargo
        assert "<url>https://repo.corp/artifactory/maven</url>" in files[f"{SANDBOX_HOME}/.m2/settings.xml"]

    def test_only_overridden_registries_are_written(self) -> None:
        files = Registries.from_settings(_settings(npm_registry_url="https://npm.corp")).sandbox_files()
        assert set(files) == {f"{SANDBOX_HOME}/.npmrc"}


class TestCredentials:
    def _registries(self, **extra: str) -> Registries:
        return Registries.from_settings(_settings(
            pypi_url="https://pypi.corp", npm_registry_url="https://npm.corp/",
            go_proxy_url="https://go.corp", maven_url="https://mvn.corp", **extra,
        ))

    def test_basic_credentials_only_go_to_the_mirror_hosts(self) -> None:
        r = self._registries(registry_username="svc", registry_password="p@ss/word")
        mirror = r.request_kwargs("https://pypi.corp/pypi/x/json")
        assert isinstance(mirror["auth"], httpx.BasicAuth)
        assert r.request_kwargs("https://pypi.org/pypi/x/json") == {}
        assert r.request_kwargs("https://github.com/o/r") == {}

    def test_token_is_sent_as_bearer_to_mirror_hosts_only(self) -> None:
        r = self._registries(registry_token="tok")
        assert r.request_kwargs("https://npm.corp/left-pad") == {"headers": {"Authorization": "Bearer tok"}}
        assert r.request_kwargs("https://registry.npmjs.org/left-pad") == {}

    def test_no_credentials_no_auth(self) -> None:
        assert self._registries().request_kwargs("https://pypi.corp/pypi/x/json") == {}

    def test_sandbox_files_carry_url_encoded_basic_credentials(self) -> None:
        files = self._registries(registry_username="svc", registry_password="p@ss/word").sandbox_files()
        assert "index-url = https://svc:p%40ss%2Fword@pypi.corp/simple" in files[f"{SANDBOX_HOME}/.config/pip/pip.conf"]
        assert "GOPROXY=https://svc:p%40ss%2Fword@go.corp" in files[f"{SANDBOX_HOME}/.config/go/env"]
        settings_xml = files[f"{SANDBOX_HOME}/.m2/settings.xml"]
        assert "<username>svc</username>" in settings_xml and "<password>p@ss/word</password>" in settings_xml
        npmrc = files[f"{SANDBOX_HOME}/.npmrc"]
        import base64
        assert f"//npm.corp/:_auth={base64.b64encode(b'svc:p@ss/word').decode()}" in npmrc

    def test_token_becomes_the_npm_auth_token(self) -> None:
        npmrc = self._registries(registry_token="tok").sandbox_files()[f"{SANDBOX_HOME}/.npmrc"]
        assert "//npm.corp/:_authToken=tok" in npmrc

    def test_xml_special_characters_are_escaped(self) -> None:
        r = self._registries(registry_username="a<b", registry_password="x&y</password>")
        xml = r.sandbox_files()[f"{SANDBOX_HOME}/.m2/settings.xml"]
        assert "a&lt;b" in xml and "x&amp;y&lt;/password&gt;" in xml

    def test_no_credentials_means_none_in_the_files(self) -> None:
        for content in self._registries().sandbox_files().values():
            assert "@" not in content.replace("%40", "") or "password" not in content


class TestValidation:
    @pytest.mark.parametrize("field", ["pypi_url", "npm_registry_url", "go_proxy_url", "crates_api_url", "maven_url"])
    def test_urls_must_be_http(self, field: str) -> None:
        with pytest.raises(ValueError, match="http"):
            _settings(**{field: "ftp://nope"})

    def test_urls_must_not_embed_credentials(self) -> None:
        with pytest.raises(ValueError, match="REGISTRY_USERNAME"):
            _settings(npm_registry_url="https://user:pw@npm.corp")

    def test_username_needs_a_password(self) -> None:
        with pytest.raises(ValueError, match="REGISTRY_PASSWORD"):
            _settings(registry_username="svc")

    def test_plain_http_mirror_with_credentials_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="https"):
            _settings(pypi_url="http://pypi.corp", registry_username="u", registry_password="p")

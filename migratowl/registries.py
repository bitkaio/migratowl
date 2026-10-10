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

"""Package registry endpoints: public by default, optionally a private mirror.

One object serves both sides of a scan: the host-side version checks (``pypi_json_url`` ...,
``request_kwargs``) and the sandbox, whose pip, npm, Go, Cargo and Maven are pointed at the same
mirror through their own config files (``sandbox_files``). Credentials reach the sandbox as file
content, never in a command line.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from migratowl.config import Settings

SANDBOX_HOME = "/home/user"

_PYPI = "https://pypi.org"
_NPM = "https://registry.npmjs.org"
_CRATES = "https://crates.io"
_GO_PROXY = "https://proxy.golang.org"
# Maven Central's repository. Its maven-metadata.xml lists every version and answers fast; the
# search API (search.maven.org) times out often and returns at most 100 versions.
_MAVEN = "https://repo1.maven.org/maven2"


def _xml_text(value: str) -> str:
    # &, < and > are all XML text needs; html.escape avoids importing an XML module (semgrep flags any).
    return html.escape(value, quote=False)


def _join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _with_userinfo(url: str, username: str | None, password: str | None) -> str:
    if not username or not password:
        return url
    parts = urlsplit(url)
    host = f"{quote(username, safe='')}:{quote(password, safe='')}@{parts.netloc}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class Registries:
    pypi: str = _PYPI
    npm: str = _NPM
    crates: str = _CRATES
    go_proxy: str = _GO_PROXY
    maven: str = _MAVEN
    cargo_index: str | None = None
    username: str | None = None
    password: str | None = None
    token: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> Registries:
        return cls(
            pypi=settings.pypi_url or _PYPI,
            npm=settings.npm_registry_url or _NPM,
            crates=settings.crates_api_url or _CRATES,
            go_proxy=settings.go_proxy_url or _GO_PROXY,
            maven=settings.maven_url or _MAVEN,
            cargo_index=settings.cargo_registry_url,
            username=settings.registry_username,
            password=settings.registry_password,
            token=settings.registry_token,
        )

    # -- version checks (host side) -------------------------------------------------------------

    def pypi_json_url(self, name: str) -> str:
        return _join(self.pypi, f"pypi/{name}/json")

    def npm_url(self, name: str) -> str:
        return _join(self.npm, name)

    def crates_url(self, name: str) -> str:
        return _join(self.crates, f"api/v1/crates/{name}")

    def go_list_url(self, encoded_module: str) -> str:
        return _join(self.go_proxy, f"{encoded_module}/@v/list")

    def maven_metadata_url(self, group_id: str, artifact_id: str) -> str:
        return _join(self.maven, f"{group_id.replace('.', '/')}/{artifact_id}/maven-metadata.xml")

    @property
    def uses_mirror(self) -> bool:
        """True when any registry is not the public one (private package names may be involved)."""
        return bool(self._mirror_hosts())

    def _mirror_hosts(self) -> set[str]:
        configured = [
            (self.pypi, _PYPI), (self.npm, _NPM), (self.crates, _CRATES),
            (self.go_proxy, _GO_PROXY), (self.maven, _MAVEN),
        ]
        urls = [url for url, default in configured if url != default]
        return {urlsplit(url).hostname or "" for url in urls}

    def request_kwargs(self, url: str) -> dict[str, Any]:
        """``httpx`` auth for ``url``: only requests to a configured mirror carry the credential."""
        if urlsplit(url).hostname not in self._mirror_hosts():
            return {}
        if self.token:
            return {"headers": {"Authorization": f"Bearer {self.token}"}}
        if self.username and self.password:
            return {"auth": httpx.BasicAuth(self.username, self.password)}
        return {}

    # -- sandbox tool config --------------------------------------------------------------------

    def sandbox_files(self) -> dict[str, str]:
        """Absolute sandbox path → content, for each tool whose registry is not the public one."""
        files: dict[str, str] = {}
        if self.pypi != _PYPI:
            index = _with_userinfo(_join(self.pypi, "simple"), self.username, self.password)
            files[f"{SANDBOX_HOME}/.config/pip/pip.conf"] = f"[global]\nindex-url = {index}\n"
        if self.npm != _NPM:
            files[f"{SANDBOX_HOME}/.npmrc"] = self._npmrc()
        if self.go_proxy != _GO_PROXY:
            proxy = _with_userinfo(self.go_proxy.rstrip("/"), self.username, self.password)
            files[f"{SANDBOX_HOME}/.config/go/env"] = f"GOPROXY={proxy}\n"
        if self.cargo_index:
            files[f"{SANDBOX_HOME}/.cargo/config.toml"] = (
                '[source.crates-io]\nreplace-with = "migratowl-mirror"\n\n'
                f'[source.migratowl-mirror]\nregistry = "{self.cargo_index}"\n'
            )
        if self.maven != _MAVEN:
            files[f"{SANDBOX_HOME}/.m2/settings.xml"] = self._maven_settings()
        return files

    def _npmrc(self) -> str:
        parts = urlsplit(self.npm)
        lines = [f"registry={self.npm.rstrip('/')}/"]
        scope = f"//{parts.netloc}{parts.path.rstrip('/')}/"
        if self.token:
            lines.append(f"{scope}:_authToken={self.token}")
        elif self.username and self.password:
            lines.append(f"{scope}:_auth={base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()}")
        return "\n".join(lines) + "\n"

    def _maven_settings(self) -> str:
        server = ""
        if self.username and self.password:
            server = (
                "  <servers>\n    <server>\n      <id>migratowl</id>\n"
                f"      <username>{_xml_text(self.username)}</username>\n"
                f"      <password>{_xml_text(self.password)}</password>\n"
                "    </server>\n  </servers>\n"
            )
        return (
            '<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">\n'
            f"{server}"
            "  <mirrors>\n    <mirror>\n      <id>migratowl</id>\n      <mirrorOf>*</mirrorOf>\n"
            f"      <url>{_xml_text(self.maven)}</url>\n    </mirror>\n  </mirrors>\n</settings>\n"
        )

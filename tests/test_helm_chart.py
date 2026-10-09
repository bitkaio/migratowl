# SPDX-License-Identifier: Apache-2.0

"""Tests for the server Helm chart (deploy/helm/migratowl)."""

import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "migratowl"

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(*sets: str) -> list[dict[str, Any]]:
    cmd = ["helm", "template", "rel", str(CHART), "--namespace", "mw", *[a for s in sets for a in ("--set", s)]]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d["kind"] == kind]


def _env(deployment: dict[str, Any]) -> dict[str, str]:
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["value"] for e in container["env"]}


def test_app_version_tracks_the_project_version() -> None:
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert chart["appVersion"] == project, "bump Chart.yaml appVersion together with pyproject.toml"


@needs_helm
class TestDefaults:
    def setup_method(self) -> None:
        self.docs = _render("existingSecret=migratowl-env")
        self.deployment = _by_kind(self.docs, "Deployment")[0]
        self.pod = self.deployment["spec"]["template"]["spec"]

    def test_single_replica_that_is_replaced_not_overlapped(self) -> None:
        assert self.deployment["spec"]["replicas"] == 1
        assert self.deployment["spec"]["strategy"]["type"] == "Recreate"

    def test_pod_is_locked_down(self) -> None:
        container = self.pod["containers"][0]
        assert self.pod["securityContext"]["runAsNonRoot"] is True
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}

    def test_image_defaults_to_the_app_version(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        assert self.pod["containers"][0]["image"] == f"ghcr.io/bitkaio/migratowl-server:{project}"

    def test_secrets_come_from_the_existing_secret(self) -> None:
        container = self.pod["containers"][0]
        assert container["envFrom"] == [{"secretRef": {"name": "migratowl-env"}}]
        assert "ANTHROPIC_API_KEY" not in _env(self.deployment)

    def test_agent_sandbox_uses_the_router_service_directly(self) -> None:
        env = _env(self.deployment)
        assert env["MIGRATOWL_SANDBOX_MODE"] == "agent-sandbox"
        assert env["MIGRATOWL_SANDBOX_CONNECTION_MODE"] == "direct"
        assert env["MIGRATOWL_SANDBOX_API_URL"] == "http://sandbox-router-svc.mw.svc.cluster.local:8080"
        assert env["MIGRATOWL_SANDBOX_NAMESPACE"] == "mw"

    def test_rbac_covers_sandbox_claims_and_nothing_cluster_wide(self) -> None:
        assert _by_kind(self.docs, "ClusterRole") == []
        rules = _by_kind(self.docs, "Role")[0]["rules"]
        assert any("sandboxclaims" in r["resources"] and "create" in r["verbs"] for r in rules)
        assert not any("pods/exec" in r["resources"] for r in rules)

    def test_history_is_kept_on_a_volume_that_survives_uninstall(self) -> None:
        pvc = _by_kind(self.docs, "PersistentVolumeClaim")[0]
        assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
        volumes = {v["name"]: v for v in self.pod["volumes"]}
        assert volumes["data"]["persistentVolumeClaim"]["claimName"] == pvc["metadata"]["name"]
        assert "MIGRATOWL_PERSISTENCE_BACKEND" not in _env(self.deployment)


@needs_helm
class TestOptions:
    def test_raw_mode(self) -> None:
        docs = _render("sandbox.mode=raw")
        env = _env(_by_kind(docs, "Deployment")[0])
        assert env["MIGRATOWL_SANDBOX_MODE"] == "raw"
        assert env["MIGRATOWL_SANDBOX_IMAGE"].startswith("ghcr.io/bitkaio/migratowl-runtime:")
        assert "MIGRATOWL_SANDBOX_API_URL" not in env
        rules = _by_kind(docs, "Role")[0]["rules"]
        assert any("pods/exec" in r["resources"] for r in rules)
        assert any("networkpolicies" in r["resources"] for r in rules)
        assert len(_by_kind(docs, "NetworkPolicy")) == 1

    def test_raw_mode_egress_policy_can_be_left_out(self) -> None:
        assert _by_kind(_render("sandbox.mode=raw", "sandbox.egressPolicy=false"), "NetworkPolicy") == []

    def test_sandboxes_in_another_namespace_get_their_role_there(self) -> None:
        docs = _render("sandbox.namespace=sandboxes")
        assert _by_kind(docs, "Role")[0]["metadata"]["namespace"] == "sandboxes"
        binding = _by_kind(docs, "RoleBinding")[0]
        assert binding["subjects"][0]["namespace"] == "mw"
        env = _env(_by_kind(docs, "Deployment")[0])
        assert env["MIGRATOWL_SANDBOX_API_URL"].endswith("sandbox-router-svc.sandboxes.svc.cluster.local:8080")

    def test_without_persistence_the_memory_backend_is_used(self) -> None:
        docs = _render("persistence.enabled=false")
        assert _env(_by_kind(docs, "Deployment")[0])["MIGRATOWL_PERSISTENCE_BACKEND"] == "memory"
        assert _by_kind(docs, "PersistentVolumeClaim") == []

    def test_existing_claim_is_used_and_no_claim_is_created(self) -> None:
        docs = _render("persistence.existingClaim=mine")
        assert _by_kind(docs, "PersistentVolumeClaim") == []
        volumes = _by_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]["volumes"]
        assert {"name": "data", "persistentVolumeClaim": {"claimName": "mine"}} in volumes

    def test_plain_settings_are_passed_through(self) -> None:
        docs = _render("env.MIGRATOWL_MODEL_NAME=claude-sonnet-5-5")
        assert _env(_by_kind(docs, "Deployment")[0])["MIGRATOWL_MODEL_NAME"] == "claude-sonnet-5-5"

# SPDX-License-Identifier: Apache-2.0

"""Tests for the Kubernetes manifests shipped in k8s/."""

from ipaddress import ip_network
from pathlib import Path

import pytest
import yaml
from langchain_kubernetes.backends.raw_manifests import sandbox_labels

K8S_DIR = Path(__file__).resolve().parent.parent / "k8s"
EGRESS_POLICY = K8S_DIR / "sandbox-egress-raw.yaml"

# Ranges a sandbox must never reach: cluster pod/service CIDRs, node networks,
# cloud metadata (169.254.169.254) and loopback.
BLOCKED_RANGES = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
]


@pytest.fixture
def policy() -> dict:
    return yaml.safe_load(EGRESS_POLICY.read_text())


def _ip_blocks(policy: dict) -> list[dict]:
    return [
        peer["ipBlock"]
        for rule in policy["spec"]["egress"]
        for peer in rule.get("to", [])
        if "ipBlock" in peer
    ]


class TestSandboxEgressPolicy:
    def test_selects_raw_mode_sandbox_pods(self, policy: dict) -> None:
        # The deny-all policy langchain-kubernetes creates per pod blocks DNS
        # and all egress; this allow policy must select the same pods.
        selector = policy["spec"]["podSelector"]["matchLabels"]
        pod_labels = sandbox_labels("any-sandbox-id")
        assert selector
        assert selector.items() <= pod_labels.items()

    def test_only_adds_egress_rules(self, policy: dict) -> None:
        # Ingress stays denied by the per-pod deny-all policy.
        assert policy["kind"] == "NetworkPolicy"
        assert policy["spec"]["policyTypes"] == ["Egress"]
        assert "ingress" not in policy["spec"]

    def test_allows_dns_to_kube_dns(self, policy: dict) -> None:
        dns_rules = [
            rule
            for rule in policy["spec"]["egress"]
            if any(p.get("port") == 53 for p in rule.get("ports", []))
        ]
        assert dns_rules, "sandbox needs DNS to resolve git hosts and registries"
        rule = dns_rules[0]
        assert {p["protocol"] for p in rule["ports"]} == {"UDP", "TCP"}
        peer = rule["to"][0]
        assert peer["podSelector"]["matchLabels"] == {"k8s-app": "kube-dns"}
        assert peer["namespaceSelector"]["matchLabels"] == {
            "kubernetes.io/metadata.name": "kube-system"
        }

    def test_allows_public_internet(self, policy: dict) -> None:
        cidrs = {block["cidr"] for block in _ip_blocks(policy)}
        assert "0.0.0.0/0" in cidrs

    @pytest.mark.parametrize("blocked", BLOCKED_RANGES)
    def test_excludes_private_and_metadata_ranges(self, policy: dict, blocked: str) -> None:
        for block in _ip_blocks(policy):
            if ip_network(block["cidr"]).version != 4:
                continue
            excepted = [ip_network(c) for c in block.get("except", [])]
            assert any(ip_network(blocked).subnet_of(e) for e in excepted), (
                f"{block['cidr']} lets the sandbox reach {blocked}"
            )

    def test_limits_internet_egress_to_web_ports(self, policy: dict) -> None:
        for rule in policy["spec"]["egress"]:
            if any("ipBlock" in peer for peer in rule.get("to", [])):
                assert {p["port"] for p in rule["ports"]} == {80, 443}

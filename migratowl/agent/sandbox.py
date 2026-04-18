# SPDX-License-Identifier: Apache-2.0

"""Sandbox manager factory — wraps KubernetesSandboxManager construction."""

from __future__ import annotations

import logging

from langchain_kubernetes import KubernetesProviderConfig, KubernetesSandboxManager

from migratowl.config import Settings

logger = logging.getLogger(__name__)


def create_sandbox_manager(settings: Settings) -> KubernetesSandboxManager:
    """Create a KubernetesSandboxManager from application settings.

    No I/O on construction — sandbox acquisition is deferred to the first
    graph invocation per thread_id by create_setup_node().
    """
    try:
        import truststore

        truststore.extract_from_ssl()
    except ImportError:
        logger.debug("truststore not installed; skipping system certificate injection")

    if settings.sandbox_mode == "raw":
        config = KubernetesProviderConfig(
            mode="raw",
            namespace=settings.sandbox_namespace,
            image=settings.sandbox_image,
            block_network=settings.sandbox_block_network,
        )
        logger.info("KubernetesSandboxManager created (mode=raw, image=%s)", settings.sandbox_image)
    else:
        config = KubernetesProviderConfig(
            template_name=settings.sandbox_template,
            namespace=settings.sandbox_namespace,
            connection_mode=settings.sandbox_connection_mode,
        )
        logger.info("KubernetesSandboxManager created (template=%s)", settings.sandbox_template)

    manager = KubernetesSandboxManager(config)
    return manager
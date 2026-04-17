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

    config = KubernetesProviderConfig(
        template_name=settings.sandbox_template,
        namespace=settings.sandbox_namespace,
        connection_mode=settings.sandbox_connection_mode,
    )
    manager = KubernetesSandboxManager(config)
    logger.info("KubernetesSandboxManager created (template=%s)", settings.sandbox_template)
    return manager

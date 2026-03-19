"""Sandbox lifecycle helpers — async wrappers around blocking K8s init."""

from __future__ import annotations

import asyncio
import logging

from deepagents.backends.protocol import BackendProtocol
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

from migratowl.config import Settings

logger = logging.getLogger(__name__)


async def create_sandbox(settings: Settings) -> tuple[KubernetesProvider, BackendProtocol]:
    """Create a K8s sandbox in a thread executor (blocking I/O)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _blocking_init, settings)


def _blocking_init(settings: Settings) -> tuple[KubernetesProvider, BackendProtocol]:
    """Blocking K8s init with truststore handling."""
    try:
        import truststore

        truststore.extract_from_ssl()
    except ImportError:
        pass

    try:
        config = KubernetesProviderConfig(
            template_name=settings.sandbox_template,
            namespace=settings.sandbox_namespace,
            connection_mode=settings.sandbox_connection_mode,
        )
        provider = KubernetesProvider(config)
        sandbox = provider.get_or_create()
        logger.info("Kubernetes sandbox created: %s", sandbox.id)
        return provider, sandbox
    finally:
        try:
            import truststore

            truststore.inject_into_ssl()
        except ImportError:
            pass


async def destroy_sandbox(provider: KubernetesProvider, sandbox: BackendProtocol) -> None:
    """Destroy sandbox, suppressing errors during cleanup."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, lambda: provider.delete(sandbox_id=sandbox.id)
        )
        logger.info("Sandbox %s deleted.", sandbox.id)
    except Exception:
        logger.warning("Failed to clean up sandbox.", exc_info=True)

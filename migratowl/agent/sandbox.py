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

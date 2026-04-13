"""
LocalGraphClient — drop-in replacement di zep_cloud.client.Zep
Usa Neo4j Community Edition (Docker) come backend locale.
"""
from __future__ import annotations

from .graph_api import GraphAPI
from ..logger import get_logger

logger = get_logger("mirofish.local_graph")


def _make_llm_client():
    from ...utils.llm_client import LLMClient
    return LLMClient()


class LocalGraphClient:
    """
    Interfaccia compatibile con zep_cloud.client.Zep.

    Utilizzo (identico al vecchio codice Zep):
        client = LocalGraphClient()
        client.graph.create(graph_id="...", name="...")
        nodes = client.graph.node.get_by_graph_id("...")
    """

    def __init__(self):
        self.graph = GraphAPI(llm_client_factory=_make_llm_client)
        logger.info("LocalGraphClient inizializzato (backend: Neo4j)")

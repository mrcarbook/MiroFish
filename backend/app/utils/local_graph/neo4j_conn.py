"""
Singleton per la connessione Neo4j e setup degli indici.
"""
from __future__ import annotations

import threading
from typing import Optional

from neo4j import GraphDatabase, Driver

from ..logger import get_logger

logger = get_logger("mirofish.neo4j")

_driver: Optional[Driver] = None
_lock = threading.Lock()


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        with _lock:
            if _driver is None:
                from ...config import Config  # importazione lazy per evitare circolarità
                _driver = GraphDatabase.driver(
                    Config.NEO4J_URI,
                    auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD),
                )
                _ensure_indexes(_driver)
                logger.info(f"Connessione Neo4j stabilita: {Config.NEO4J_URI}")
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


def _ensure_indexes(driver: Driver):
    """Crea indici e full-text index se non esistono."""
    with driver.session() as session:
        # Constraint univocità UUID sui nodi Entity
        session.run("""
            CREATE CONSTRAINT entity_uuid IF NOT EXISTS
            FOR (n:Entity) REQUIRE n.uuid_ IS UNIQUE
        """)
        # Constraint UUID sugli episodi
        session.run("""
            CREATE CONSTRAINT episode_uuid IF NOT EXISTS
            FOR (e:Episode) REQUIRE e.uuid_ IS UNIQUE
        """)
        # Indice su graph_id per query veloci
        session.run("""
            CREATE INDEX entity_graph_id IF NOT EXISTS
            FOR (n:Entity) ON (n.graph_id)
        """)
        # Full-text index per la ricerca semantica (keyword)
        try:
            session.run("""
                CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
                FOR (n:Entity) ON EACH [n.name, n.summary]
            """)
            session.run("""
                CREATE FULLTEXT INDEX edge_fulltext IF NOT EXISTS
                FOR ()-[r:RELATES_TO]-() ON EACH [r.fact, r.name]
            """)
        except Exception as e:
            # Alcuni provider Neo4j limitano i full-text index — non è bloccante
            logger.warning(f"Full-text index creation warning: {e}")

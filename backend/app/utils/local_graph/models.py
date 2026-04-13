"""
Dataclasses che replicano l'interfaccia degli oggetti Zep Cloud,
permettendo di usare Neo4j locale come drop-in replacement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LocalNode:
    uuid_: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    # alias per compatibilità con codice che legge .uuid invece di .uuid_
    @property
    def uuid(self) -> str:
        return self.uuid_


@dataclass
class LocalEdge:
    uuid_: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def uuid(self) -> str:
        return self.uuid_


@dataclass
class LocalEpisode:
    uuid_: str
    processed: bool = False

    @property
    def uuid(self) -> str:
        return self.uuid_


@dataclass
class LocalSearchResult:
    edges: List[LocalEdge] = field(default_factory=list)
    nodes: List[LocalNode] = field(default_factory=list)

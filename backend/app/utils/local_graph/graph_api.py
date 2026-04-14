"""
GraphAPI — implementa graph.*, graph.node.*, graph.edge.*, graph.episode.*
Come drop-in replacement di zep_cloud.client.Zep.graph
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .models import LocalEdge, LocalEpisode, LocalNode, LocalSearchResult
from .neo4j_conn import get_driver
from ..logger import get_logger

logger = get_logger("mirofish.local_graph")


# ---------------------------------------------------------------------------
# Sotto-oggetti (node, edge, episode) -- stessa struttura gerarchica di Zep
# ---------------------------------------------------------------------------

class EpisodeAPI:
    def get(self, uuid_: str) -> LocalEpisode:
        with get_driver().session() as session:
            result = session.run(
                "MATCH (e:Episode {uuid_: $uuid_}) RETURN e.processed AS processed",
                uuid_=uuid_
            )
            rec = result.single()
            if rec is None:
                return LocalEpisode(uuid_=uuid_, processed=True)  # fallback: considerato processato
            return LocalEpisode(uuid_=uuid_, processed=bool(rec["processed"]))


class NodeAPI:
    def get(self, uuid_: str) -> Optional[LocalNode]:
        with get_driver().session() as session:
            result = session.run(
                "MATCH (n:Entity {uuid_: $uuid_}) RETURN n",
                uuid_=uuid_
            )
            rec = result.single()
            if rec is None:
                return None
            return _node_from_record(rec["n"])

    def get_by_graph_id(
        self,
        graph_id: str,
        limit: int = 100,
        uuid_cursor: Optional[str] = None,
    ) -> List[LocalNode]:
        if uuid_cursor:
            query = """
                MATCH (n:Entity {graph_id: $graph_id})
                WHERE n.uuid_ > $cursor
                RETURN n ORDER BY n.uuid_ LIMIT $limit
            """
            params = {"graph_id": graph_id, "cursor": uuid_cursor, "limit": limit}
        else:
            query = """
                MATCH (n:Entity {graph_id: $graph_id})
                RETURN n ORDER BY n.uuid_ LIMIT $limit
            """
            params = {"graph_id": graph_id, "limit": limit}

        with get_driver().session() as session:
            result = session.run(query, **params)
            return [_node_from_record(r["n"]) for r in result]

    def get_entity_edges(self, node_uuid: str) -> List[LocalEdge]:
        with get_driver().session() as session:
            result = session.run(
                """
                MATCH (src:Entity {uuid_: $uuid_})-[r:RELATES_TO]->(tgt:Entity)
                RETURN r, src.uuid_ AS src_uuid, tgt.uuid_ AS tgt_uuid
                UNION
                MATCH (src:Entity)-[r:RELATES_TO]->(tgt:Entity {uuid_: $uuid_})
                RETURN r, src.uuid_ AS src_uuid, tgt.uuid_ AS tgt_uuid
                """,
                uuid_=node_uuid
            )
            return [_edge_from_record(r["r"], r["src_uuid"], r["tgt_uuid"]) for r in result]


class EdgeAPI:
    def get_by_graph_id(
        self,
        graph_id: str,
        limit: int = 100,
        uuid_cursor: Optional[str] = None,
    ) -> List[LocalEdge]:
        if uuid_cursor:
            query = """
                MATCH (src:Entity {graph_id: $graph_id})-[r:RELATES_TO]->(tgt:Entity)
                WHERE r.uuid_ > $cursor
                RETURN r, src.uuid_ AS src_uuid, tgt.uuid_ AS tgt_uuid
                ORDER BY r.uuid_ LIMIT $limit
            """
            params = {"graph_id": graph_id, "cursor": uuid_cursor, "limit": limit}
        else:
            query = """
                MATCH (src:Entity {graph_id: $graph_id})-[r:RELATES_TO]->(tgt:Entity)
                RETURN r, src.uuid_ AS src_uuid, tgt.uuid_ AS tgt_uuid
                ORDER BY r.uuid_ LIMIT $limit
            """
            params = {"graph_id": graph_id, "limit": limit}

        with get_driver().session() as session:
            result = session.run(query, **params)
            return [_edge_from_record(r["r"], r["src_uuid"], r["tgt_uuid"]) for r in result]


# ---------------------------------------------------------------------------
# GraphAPI principale
# ---------------------------------------------------------------------------

class GraphAPI:
    def __init__(self, llm_client_factory: Callable):
        self.node = NodeAPI()
        self.edge = EdgeAPI()
        self.episode = EpisodeAPI()
        self._llm_factory = llm_client_factory
        self._llm = None

    @property
    def _llm_client(self):
        if self._llm is None:
            self._llm = self._llm_factory()
        return self._llm

    # ---- graph lifecycle ----

    def create(self, graph_id: str, name: str, description: str = "") -> None:
        with get_driver().session() as session:
            session.run(
                """
                MERGE (g:GraphMeta {graph_id: $graph_id})
                SET g.name = $name, g.description = $description, g.created_at = $ts
                """,
                graph_id=graph_id, name=name, description=description,
                ts=time.strftime("%Y-%m-%dT%H:%M:%SZ")
            )
        logger.info(f"Grafo creato: {graph_id}")

    def delete(self, graph_id: str) -> None:
        with get_driver().session() as session:
            session.run(
                "MATCH (n:Entity {graph_id: $graph_id}) DETACH DELETE n",
                graph_id=graph_id
            )
            session.run(
                "MATCH (e:Episode {graph_id: $graph_id}) DELETE e",
                graph_id=graph_id
            )
            session.run(
                "MATCH (g:GraphMeta {graph_id: $graph_id}) DELETE g",
                graph_id=graph_id
            )
        logger.info(f"Grafo eliminato: {graph_id}")

    def set_ontology(self, graph_id: str, ontology: Any) -> None:
        """Salva l'ontologia come JSON nella GraphMeta (non blocca il flusso)."""
        try:
            ontology_json = json.dumps(ontology, ensure_ascii=False)
        except Exception:
            ontology_json = str(ontology)
        with get_driver().session() as session:
            session.run(
                "MERGE (g:GraphMeta {graph_id: $gid}) SET g.ontology = $ont",
                gid=graph_id, ont=ontology_json
            )
        logger.info(f"Ontologia salvata per grafo {graph_id}")

    # ---- episode ingestion ----

    def add(self, graph_id: str, type: str = "text", data: str = "") -> LocalEpisode:
        """Singola aggiunta di testo (usata da ZepGraphMemoryUpdater)."""
        ep_uuid = str(uuid.uuid4())
        _store_episode(graph_id, ep_uuid, data)
        _process_episode_async(graph_id, ep_uuid, data, self._llm_client)
        return LocalEpisode(uuid_=ep_uuid, processed=False)

    def add_batch(
        self,
        graph_id: str,
        episodes: List[Any],
    ) -> List[LocalEpisode]:
        """Aggiunta in batch di episodi testuali (usata da GraphBuilderService)."""
        results = []
        for ep in episodes:
            text = getattr(ep, "data", "") or str(ep)
            ep_uuid = str(uuid.uuid4())
            _store_episode(graph_id, ep_uuid, text)
            _process_episode_async(graph_id, ep_uuid, text, self._llm_client)
            results.append(LocalEpisode(uuid_=ep_uuid, processed=False))
        return results

    # ---- search ----

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str = "none",
    ) -> LocalSearchResult:
        """Full-text search su Neo4j con fallback keyword."""
        edges: List[LocalEdge] = []
        nodes: List[LocalNode] = []

        try:
            with get_driver().session() as session:
                # Cerca archi (relazioni) per fact/name
                edge_result = session.run(
                    """
                    CALL db.index.fulltext.queryRelationships('edge_fulltext', $query, {limit: $limit})
                    YIELD relationship, score
                    MATCH (src:Entity)-[relationship]->(tgt:Entity)
                    WHERE src.graph_id = $graph_id
                    RETURN relationship AS r, src.uuid_ AS src_uuid, tgt.uuid_ AS tgt_uuid
                    """,
                    query=query, limit=limit, graph_id=graph_id
                )
                for r in edge_result:
                    edges.append(_edge_from_record(r["r"], r["src_uuid"], r["tgt_uuid"]))

                # Cerca nodi per name/summary
                node_result = session.run(
                    """
                    CALL db.index.fulltext.queryNodes('entity_fulltext', $query, {limit: $limit})
                    YIELD node, score
                    WHERE node.graph_id = $graph_id
                    RETURN node AS n
                    """,
                    query=query, limit=limit, graph_id=graph_id
                )
                for r in node_result:
                    nodes.append(_node_from_record(r["n"]))

        except Exception as e:
            logger.warning(f"Full-text search fallback a keyword: {e}")
            # Fallback: CONTAINS keyword search
            kw = query.lower()
            with get_driver().session() as session:
                edge_result = session.run(
                    """
                    MATCH (src:Entity {graph_id: $gid})-[r:RELATES_TO]->(tgt:Entity)
                    WHERE toLower(r.fact) CONTAINS $kw OR toLower(r.name) CONTAINS $kw
                    RETURN r, src.uuid_ AS src_uuid, tgt.uuid_ AS tgt_uuid
                    LIMIT $limit
                    """,
                    gid=graph_id, kw=kw, limit=limit
                )
                edges = [_edge_from_record(r["r"], r["src_uuid"], r["tgt_uuid"]) for r in edge_result]

                node_result = session.run(
                    """
                    MATCH (n:Entity {graph_id: $gid})
                    WHERE toLower(n.name) CONTAINS $kw OR toLower(n.summary) CONTAINS $kw
                    RETURN n LIMIT $limit
                    """,
                    gid=graph_id, kw=kw, limit=limit
                )
                nodes = [_node_from_record(r["n"]) for r in node_result]

        return LocalSearchResult(edges=edges, nodes=nodes)


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _node_from_record(n) -> LocalNode:
    labels = [l for l in n.labels if l not in ("Entity",)]
    return LocalNode(
        uuid_=n.get("uuid_", ""),
        name=n.get("name", ""),
        labels=labels or ["Entity"],
        summary=n.get("summary", ""),
        attributes=json.loads(n.get("attributes", "{}") or "{}"),
    )


def _edge_from_record(r, src_uuid: str, tgt_uuid: str) -> LocalEdge:
    return LocalEdge(
        uuid_=r.get("uuid_", ""),
        name=r.get("name", ""),
        fact=r.get("fact", ""),
        source_node_uuid=src_uuid,
        target_node_uuid=tgt_uuid,
        attributes=json.loads(r.get("attributes", "{}") or "{}"),
    )


def _store_episode(graph_id: str, ep_uuid: str, data: str):
    with get_driver().session() as session:
        session.run(
            """
            CREATE (e:Episode {
                uuid_: $uuid_, graph_id: $gid,
                data: $data, processed: false,
                created_at: $ts
            })
            """,
            uuid_=ep_uuid, gid=graph_id, data=data[:4000],
            ts=time.strftime("%Y-%m-%dT%H:%M:%SZ")
        )


def _process_episode_async(graph_id: str, ep_uuid: str, text: str, llm_client):
    """Estrae entità/relazioni dal testo con LLM e le salva in Neo4j (in un thread separato)."""
    import threading
    t = threading.Thread(
        target=_extract_and_store,
        args=(graph_id, ep_uuid, text, llm_client),
        daemon=True
    )
    t.start()


def _extract_and_store(graph_id: str, ep_uuid: str, text: str, llm_client):
    """Thread worker: chiama LLM → salva nodi/archi → marca episodio come processato."""
    try:
        extraction = _extract_entities(text, llm_client)
        _save_extraction(graph_id, extraction)
        # Marca episodio come processato
        with get_driver().session() as session:
            session.run(
                "MATCH (e:Episode {uuid_: $uuid_}) SET e.processed = true",
                uuid_=ep_uuid
            )
    except Exception as e:
        logger.error(f"Errore estrazione entità per episodio {ep_uuid}: {e}")
        # Marca comunque come processato per non bloccare il polling
        try:
            with get_driver().session() as session:
                session.run(
                    "MATCH (e:Episode {uuid_: $uuid_}) SET e.processed = true",
                    uuid_=ep_uuid
                )
        except Exception:
            pass


_EXTRACT_PROMPT = """Analizza il seguente testo ed estrai entità e relazioni.

Rispondi SOLO con un JSON valido nel formato:
{
  "entities": [
    {"name": "nome entità", "type": "tipo (es. Persona, Organizzazione, Concetto, Luogo, Normativa)", "summary": "breve descrizione"}
  ],
  "relationships": [
    {"from": "nome entità 1", "to": "nome entità 2", "type": "tipo relazione", "fact": "frase che descrive la relazione"}
  ]
}

Regole:
- Estrai solo entità significative (non articoli, preposizioni)
- Il campo "type" deve essere una categoria semantica
- Il campo "fact" deve essere una frase completa e leggibile
- Se non trovi entità o relazioni, restituisci liste vuote

Testo da analizzare:
"""


def _extract_entities(text: str, llm_client) -> Dict[str, Any]:
    """Chiama il LLM locale per estrarre entità e relazioni."""
    try:
        prompt = _EXTRACT_PROMPT + text[:3000]
        response = llm_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        content = response.choices[0].message.content.strip()

        # Cerca il JSON nella risposta (il modello potrebbe aggiungere testo prima/dopo)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            content = content[start:end]

        return json.loads(content)
    except Exception as e:
        logger.warning(f"Estrazione entità fallita: {e}")
        return {"entities": [], "relationships": []}


def _save_extraction(graph_id: str, extraction: Dict[str, Any]):
    """Salva entità e relazioni estratte in Neo4j."""
    entities = extraction.get("entities", [])
    relationships = extraction.get("relationships", [])

    # Mappa nome → uuid per le relazioni
    name_to_uuid: Dict[str, str] = {}

    with get_driver().session() as session:
        for ent in entities:
            name = ent.get("name", "").strip()
            if not name:
                continue
            ent_type = ent.get("type", "Entity").strip().replace(" ", "_")
            summary = ent.get("summary", "")
            ent_uuid = str(uuid.uuid4())
            name_to_uuid[name.lower()] = ent_uuid

            # Usa MERGE per evitare duplicati (per nome + graph_id)
            session.run(
                f"""
                MERGE (n:Entity:{ent_type} {{graph_id: $gid, name: $name}})
                ON CREATE SET n.uuid_ = $uuid_, n.summary = $summary,
                              n.attributes = '{{}}', n.created_at = $ts
                ON MATCH SET n.summary = CASE WHEN n.summary = '' THEN $summary ELSE n.summary END
                WITH n SET n.uuid_ = coalesce(n.uuid_, $uuid_)
                """,
                gid=graph_id, name=name, uuid_=ent_uuid,
                summary=summary, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ")
            )

        for rel in relationships:
            from_name = rel.get("from", "").strip()
            to_name = rel.get("to", "").strip()
            rel_type = rel.get("type", "RELATED").strip().replace(" ", "_")
            fact = rel.get("fact", "")
            if not from_name or not to_name:
                continue

            rel_uuid = str(uuid.uuid4())
            session.run(
                f"""
                MATCH (src:Entity {{graph_id: $gid, name: $from_name}})
                MATCH (tgt:Entity {{graph_id: $gid, name: $to_name}})
                MERGE (src)-[r:RELATES_TO {{graph_id: $gid, from_name: $from_name, to_name: $to_name}}]->(tgt)
                ON CREATE SET r.uuid_ = $uuid_, r.name = $rel_type,
                              r.fact = $fact, r.attributes = '{{}}'
                """,
                gid=graph_id, from_name=from_name, to_name=to_name,
                uuid_=rel_uuid, rel_type=rel_type, fact=fact
            )

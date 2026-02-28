"""
analogic_core.py - Analogic Reasoning & Association Engine
Omnira Synora AI Memory System

Handles logic relationships, knowledge associations, and
analogic reasoning patterns between memory entries.
"""

import json
import math
import logging
from typing import Optional
from uuid import UUID

from database import get_connection
from security import encrypt, decrypt, encrypt_with_user_key, decrypt_with_user_key, hash_content, sanitize_input

logger = logging.getLogger(__name__)


class AnalogicCore:
    """
    The Analogic Core manages semantic associations and reasoning relationships
    between stored memories, enabling AI-level pattern recall.
    """

    # Predefined association types
    ASSOCIATION_TYPES = {
        "related_to": "General semantic relation",
        "caused_by": "Causal relationship (B caused A)",
        "leads_to": "Forward causation (A leads to B)",
        "contradicts": "Logical contradiction",
        "supports": "Logical support/evidence",
        "part_of": "Hierarchical membership",
        "similar_to": "Analogical similarity",
        "opposite_of": "Semantic antonym",
        "derived_from": "Knowledge derivation",
        "user_preference": "User-stated preference",
    }

    async def create_association(
        self,
        source_id: UUID,
        target_id: UUID,
        association_type: str,
        strength: float = 0.5,
    ) -> dict:
        """Create or update an analogic association between two memories."""
        if association_type not in self.ASSOCIATION_TYPES:
            raise ValueError(f"Unknown association type: {association_type}. "
                             f"Valid types: {list(self.ASSOCIATION_TYPES.keys())}")
        strength = max(0.0, min(1.0, strength))

        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO memory_associations (source_memory_id, target_memory_id, association_type, strength)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (source_memory_id, target_memory_id, association_type)
                DO UPDATE SET strength = $4
                RETURNING id, source_memory_id, target_memory_id, association_type, strength, created_at
                """,
                source_id, target_id, association_type, strength
            )
        return dict(row)

    async def get_associations(
        self,
        memory_id: UUID,
        direction: str = "both",  # "outgoing", "incoming", "both"
        min_strength: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """Retrieve all analogic associations for a memory node."""
        async with get_connection() as conn:
            if direction == "outgoing":
                condition = "source_memory_id = $1"
            elif direction == "incoming":
                condition = "target_memory_id = $1"
            else:
                condition = "(source_memory_id = $1 OR target_memory_id = $1)"

            rows = await conn.fetch(
                f"""
                SELECT id, source_memory_id, target_memory_id,
                       association_type, strength, created_at
                FROM memory_associations
                WHERE {condition} AND strength >= $2
                ORDER BY strength DESC
                LIMIT $3
                """,
                memory_id, min_strength, limit
            )
        return [dict(r) for r in rows]

    async def strengthen_association(
        self, source_id: UUID, target_id: UUID, association_type: str, delta: float = 0.1
    ) -> Optional[dict]:
        """Reinforce an existing association (Hebbian-like learning)."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE memory_associations
                SET strength = LEAST(1.0, strength + $4)
                WHERE source_memory_id = $1 AND target_memory_id = $2 AND association_type = $3
                RETURNING id, source_memory_id, target_memory_id, association_type, strength
                """,
                source_id, target_id, association_type, delta
            )
        return dict(row) if row else None

    async def decay_weak_associations(self, threshold: float = 0.05) -> int:
        """Remove associations that have decayed below the threshold."""
        async with get_connection() as conn:
            result = await conn.execute(
                "DELETE FROM memory_associations WHERE strength < $1", threshold
            )
        count = int(result.split()[-1])
        logger.info(f"Decayed {count} weak associations below threshold {threshold}.")
        return count

    async def get_analogic_context(self, user_id: str, memory_ids: list[UUID]) -> dict:
        """
        Build an analogic context graph for a set of memories.
        Returns enriched relationship data for AI reasoning.
        """
        if not memory_ids:
            return {"nodes": [], "edges": []}

        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT ma.*, 
                       me_s.memory_type AS source_type,
                       me_t.memory_type AS target_type
                FROM memory_associations ma
                JOIN memory_entries me_s ON ma.source_memory_id = me_s.id
                JOIN memory_entries me_t ON ma.target_memory_id = me_t.id
                WHERE (ma.source_memory_id = ANY($1) OR ma.target_memory_id = ANY($1))
                  AND me_s.user_id = $2 AND me_t.user_id = $2
                ORDER BY ma.strength DESC
                LIMIT 200
                """,
                memory_ids, user_id
            )

        edges = [dict(r) for r in rows]
        node_ids = set()
        for e in edges:
            node_ids.add(str(e["source_memory_id"]))
            node_ids.add(str(e["target_memory_id"]))

        return {
            "nodes": list(node_ids),
            "edges": edges,
            "total_connections": len(edges),
        }

    def compute_relevance_score(
        self,
        query_keywords: list[str],
        memory_content: str,
        access_count: int = 0,
        recency_hours: float = 0,
    ) -> float:
        """
        Compute a relevance score for a memory given a query.
        Combines keyword overlap, access frequency, and recency.
        """
        if not memory_content or not query_keywords:
            return 0.0

        content_lower = memory_content.lower()
        keywords_lower = [k.lower() for k in query_keywords]

        # Keyword overlap score (0 to 1)
        matches = sum(1 for kw in keywords_lower if kw in content_lower)
        keyword_score = matches / max(len(keywords_lower), 1)

        # Frequency score (logarithmic)
        freq_score = math.log1p(access_count) / math.log1p(100)  # normalize to ~1 at 100 accesses

        # Recency score (exponential decay, half-life = 24 hours)
        recency_score = math.exp(-recency_hours / 24.0)

        # Weighted combination
        total = (0.50 * keyword_score) + (0.20 * freq_score) + (0.30 * recency_score)
        return round(min(1.0, total), 4)

    async def auto_associate(
        self, new_memory_id: UUID, user_id: str, content: str, tags: list[str]
    ) -> int:
        """
        Automatically find and create associations for a new memory entry
        based on tag overlap with existing memories.
        """
        if not tags:
            return 0

        async with get_connection() as conn:
            # Find memories sharing at least one tag
            existing = await conn.fetch(
                """
                SELECT id, tags FROM memory_entries
                WHERE user_id = $1 AND id != $2 AND is_active = TRUE
                  AND tags && $3
                LIMIT 50
                """,
                user_id, new_memory_id, tags
            )

        created = 0
        for row in existing:
            shared_tags = set(row["tags"]) & set(tags)
            strength = len(shared_tags) / max(len(tags), len(row["tags"]), 1)
            if strength >= 0.1:
                try:
                    await self.create_association(
                        source_id=new_memory_id,
                        target_id=row["id"],
                        association_type="related_to",
                        strength=round(strength, 3),
                    )
                    created += 1
                except Exception as e:
                    logger.warning(f"Auto-associate failed: {e}")

        return created

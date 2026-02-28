"""
memory_engine.py - Core Memory Storage & Recall Engine
Analogic Memory System for Omnira Synora AI

Handles CRUD operations for memory entries, context sessions,
and intelligent memory recall based on context.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from database import get_connection
from security import (
    encrypt_with_user_key, decrypt_with_user_key,
    hash_content, sanitize_input
)
from analogic_core import AnalogicCore

logger = logging.getLogger(__name__)
analogic = AnalogicCore()


class MemoryEngine:
    """
    Core engine for storing, retrieving, and managing AI memory entries.
    Supports short-term context memory and long-term persistent knowledge.
    """

    SHORT_TERM_TTL_HOURS = 24       # Short-term memory expires in 24h
    MAX_CONTENT_LENGTH = 50_000     # 50KB per memory entry
    DEFAULT_RECALL_LIMIT = 20

    # ─────────────────────────────────────────────
    # STORE MEMORY
    # ─────────────────────────────────────────────

    async def store_memory(
        self,
        user_id: str,
        content: str,
        memory_type: str = "general",
        scope: str = "long_term",
        session_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        ttl_hours: Optional[int] = None,
    ) -> dict:
        """
        Store a new memory entry with AES-256-GCM encryption.
        Returns the stored memory metadata (without decrypted content).
        """
        content = sanitize_input(content, max_length=self.MAX_CONTENT_LENGTH)
        user_id = sanitize_input(user_id, max_length=255)
        tags = tags or []

        # Set TTL for short-term memories
        expires_at = None
        if scope == "short_term":
            hours = ttl_hours or self.SHORT_TERM_TTL_HOURS
            expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

        content_encrypted = encrypt_with_user_key(content, user_id)
        content_hash = hash_content(content)

        async with get_connection() as conn:
            # Prevent exact duplicate entries
            existing = await conn.fetchval(
                "SELECT id FROM memory_entries WHERE user_id = $1 AND content_hash = $2 AND is_active = TRUE",
                user_id, content_hash
            )
            if existing:
                logger.info(f"Duplicate memory skipped for user {user_id[:8]}...")
                return {"id": str(existing), "status": "duplicate", "message": "Memory already exists."}

            row = await conn.fetchrow(
                """
                INSERT INTO memory_entries
                    (user_id, session_id, memory_type, scope, content_encrypted, content_hash, tags, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, user_id, memory_type, scope, tags, created_at, expires_at
                """,
                user_id, session_id, memory_type, scope,
                content_encrypted, content_hash, tags, expires_at
            )

        memory_id = row["id"]
        result = dict(row)
        result["id"] = str(memory_id)

        # Auto-associate with related memories
        if tags:
            associations_created = await analogic.auto_associate(memory_id, user_id, content, tags)
            result["associations_created"] = associations_created

        logger.info(f"Memory stored: {memory_id} | user={user_id[:8]}... | type={memory_type}")
        return result

    # ─────────────────────────────────────────────
    # RECALL MEMORY
    # ─────────────────────────────────────────────

    async def recall_memory(
        self,
        user_id: str,
        query: str,
        memory_type: Optional[str] = None,
        scope: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[dict]:
        """
        Retrieve and rank memories relevant to a given query context.
        Returns decrypted, relevance-scored memories.
        """
        query_keywords = query.lower().split()
        now = datetime.now(timezone.utc)

        async with get_connection() as conn:
            conditions = [
                "user_id = $1",
                "is_active = TRUE",
                "(expires_at IS NULL OR expires_at > NOW())"
            ]
            params: list = [user_id]
            param_idx = 2

            if memory_type:
                conditions.append(f"memory_type = ${param_idx}")
                params.append(memory_type)
                param_idx += 1

            if scope:
                conditions.append(f"scope = ${param_idx}")
                params.append(scope)
                param_idx += 1

            if session_id:
                conditions.append(f"session_id = ${param_idx}")
                params.append(session_id)
                param_idx += 1

            if tags:
                conditions.append(f"tags && ${param_idx}")
                params.append(tags)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            rows = await conn.fetch(
                f"""
                SELECT id, memory_type, scope, content_encrypted, tags,
                       relevance_score, access_count, created_at, updated_at
                FROM memory_entries
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT {limit * 3}
                """,
                *params
            )

        results = []
        for row in rows:
            try:
                decrypted = decrypt_with_user_key(bytes(row["content_encrypted"]), user_id)
                recency_hours = (now - row["created_at"].replace(tzinfo=timezone.utc)).total_seconds() / 3600
                score = analogic.compute_relevance_score(
                    query_keywords, decrypted,
                    access_count=row["access_count"],
                    recency_hours=recency_hours,
                )
                results.append({
                    "id": str(row["id"]),
                    "memory_type": row["memory_type"],
                    "scope": row["scope"],
                    "content": decrypted,
                    "tags": row["tags"],
                    "relevance_score": score,
                    "access_count": row["access_count"],
                    "created_at": row["created_at"].isoformat(),
                })
            except Exception as e:
                logger.error(f"Failed to decrypt memory {row['id']}: {e}")

        # Sort by relevance and return top results
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        results = results[:limit]

        # Increment access count for recalled memories
        if results:
            recalled_ids = [UUID(r["id"]) for r in results]
            async with get_connection() as conn:
                await conn.execute(
                    "UPDATE memory_entries SET access_count = access_count + 1, updated_at = NOW() WHERE id = ANY($1)",
                    recalled_ids
                )

        return results

    # ─────────────────────────────────────────────
    # GET / DELETE MEMORY
    # ─────────────────────────────────────────────

    async def get_memory(self, memory_id: UUID, user_id: str) -> Optional[dict]:
        """Retrieve a single memory entry by ID."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, memory_type, scope, content_encrypted, tags,
                       relevance_score, access_count, created_at, expires_at
                FROM memory_entries
                WHERE id = $1 AND user_id = $2 AND is_active = TRUE
                """,
                memory_id, user_id
            )
        if not row:
            return None

        decrypted = decrypt_with_user_key(bytes(row["content_encrypted"]), user_id)
        return {
            "id": str(row["id"]),
            "user_id": row["user_id"],
            "memory_type": row["memory_type"],
            "scope": row["scope"],
            "content": decrypted,
            "tags": row["tags"],
            "access_count": row["access_count"],
            "created_at": row["created_at"].isoformat(),
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        }

    async def delete_memory(self, memory_id: UUID, user_id: str) -> bool:
        """Soft-delete a memory entry."""
        async with get_connection() as conn:
            result = await conn.execute(
                "UPDATE memory_entries SET is_active = FALSE, updated_at = NOW() WHERE id = $1 AND user_id = $2",
                memory_id, user_id
            )
        return result.split()[-1] == "1"

    async def purge_expired_memories(self) -> int:
        """Hard-delete expired short-term memories (run as periodic task)."""
        async with get_connection() as conn:
            result = await conn.execute(
                "DELETE FROM memory_entries WHERE scope = 'short_term' AND expires_at < NOW()"
            )
        count = int(result.split()[-1])
        if count > 0:
            logger.info(f"Purged {count} expired short-term memories.")
        return count

    # ─────────────────────────────────────────────
    # CONTEXT SESSIONS
    # ─────────────────────────────────────────────

    async def create_session(self, user_id: str, session_id: str) -> dict:
        """Initialize a context session for a user."""
        from security import encrypt
        initial_context = json.dumps({"messages": [], "user_id": user_id})
        encrypted_context = encrypt(initial_context)

        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO context_sessions (user_id, session_id, context_data_encrypted)
                VALUES ($1, $2, $3)
                ON CONFLICT (session_id) DO UPDATE SET last_active_at = NOW()
                RETURNING id, user_id, session_id, message_count, started_at, last_active_at
                """,
                user_id, session_id, encrypted_context
            )
        return dict(row)

    async def update_session_context(self, session_id: str, context_data: dict) -> bool:
        """Update the context data for an active session."""
        from security import encrypt
        encrypted = encrypt(json.dumps(context_data))
        async with get_connection() as conn:
            result = await conn.execute(
                """
                UPDATE context_sessions
                SET context_data_encrypted = $1,
                    message_count = message_count + 1,
                    last_active_at = NOW()
                WHERE session_id = $2 AND is_active = TRUE
                """,
                encrypted, session_id
            )
        return result.split()[-1] == "1"

    async def get_session_context(self, session_id: str) -> Optional[dict]:
        """Retrieve and decrypt a session's context."""
        from security import decrypt
        async with get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT context_data_encrypted, user_id, message_count FROM context_sessions WHERE session_id = $1 AND is_active = TRUE",
                session_id
            )
        if not row:
            return None
        context = json.loads(decrypt(bytes(row["context_data_encrypted"])))
        context["message_count"] = row["message_count"]
        return context

    async def get_user_stats(self, user_id: str) -> dict:
        """Get memory statistics for a user."""
        async with get_connection() as conn:
            stats = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) FILTER (WHERE is_active) AS total_memories,
                    COUNT(*) FILTER (WHERE scope = 'long_term' AND is_active) AS long_term_count,
                    COUNT(*) FILTER (WHERE scope = 'short_term' AND is_active) AS short_term_count,
                    SUM(access_count) AS total_accesses,
                    MAX(created_at) AS latest_memory
                FROM memory_entries
                WHERE user_id = $1
                """,
                user_id
            )
        return dict(stats)

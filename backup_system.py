"""
backup_system.py - Multi-Layer Backup & Restore System
Analogic Memory System for Omnira Synora AI

Architecture:
  - Primary backup: Local filesystem (fast, immediate)
  - Secondary backup: S3-compatible object storage (durable)
  - Archive backup: Weekly cold archive with full integrity checks
"""

import asyncio
import gzip
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from database import get_connection
from security import checksum_bytes, decrypt_with_user_key

logger = logging.getLogger(__name__)

# Configuration
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/var/backups/analogic_memory"))
S3_BUCKET = os.getenv("S3_BACKUP_BUCKET", "")
S3_PREFIX = os.getenv("S3_BACKUP_PREFIX", "analogic-memory-backups/")
MAX_LOCAL_BACKUPS = int(os.getenv("MAX_LOCAL_BACKUPS", "48"))  # 48 = 2 days of hourly backups

try:
    import boto3
    from botocore.exceptions import ClientError
    S3_AVAILABLE = bool(S3_BUCKET)
except ImportError:
    S3_AVAILABLE = False
    logger.warning("boto3 not installed. S3 backups disabled.")


class BackupSystem:
    """
    Multi-layer backup system with primary (local), secondary (S3),
    and archive layers.
    """

    def __init__(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        (BACKUP_DIR / "primary").mkdir(exist_ok=True)
        (BACKUP_DIR / "secondary").mkdir(exist_ok=True)
        (BACKUP_DIR / "archive").mkdir(exist_ok=True)

    # ─────────────────────────────────────────────
    # BACKUP OPERATIONS
    # ─────────────────────────────────────────────

    async def run_backup(self, backup_type: str = "primary", user_id: Optional[str] = None) -> dict:
        """
        Execute a full or user-specific backup.
        backup_type: "primary", "secondary", or "archive"
        """
        started_at = datetime.now(timezone.utc)
        backup_id = None

        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO backup_catalog (backup_type, backup_path, checksum, status)
                VALUES ($1, 'pending', 'pending', 'running')
                RETURNING id
                """,
                backup_type
            )
            backup_id = row["id"]

        try:
            backup_data, records_count = await self._export_data(user_id=user_id)
            compressed = self._compress(backup_data)
            checksum = checksum_bytes(compressed)

            timestamp = started_at.strftime("%Y%m%d_%H%M%S")
            scope_tag = f"_user_{user_id[:8]}" if user_id else "_full"
            filename = f"backup_{backup_type}{scope_tag}_{timestamp}.json.gz"

            backup_path = BACKUP_DIR / backup_type / filename
            backup_path.write_bytes(compressed)
            size_bytes = backup_path.stat().st_size

            # Upload to S3 if secondary or archive
            if backup_type in ("secondary", "archive") and S3_AVAILABLE:
                await asyncio.to_thread(self._upload_to_s3, compressed, filename, backup_type)

            completed_at = datetime.now(timezone.utc)
            async with get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE backup_catalog
                    SET backup_path = $1, checksum = $2, size_bytes = $3,
                        records_count = $4, completed_at = $5, status = 'success'
                    WHERE id = $6
                    """,
                    str(backup_path), checksum, size_bytes, records_count, completed_at, backup_id
                )

            # Rotate old local backups
            await asyncio.to_thread(self._rotate_local_backups, backup_type)

            duration_seconds = (completed_at - started_at).total_seconds()
            logger.info(f"Backup {backup_id} ({backup_type}) completed. "
                        f"Records: {records_count}, Size: {size_bytes:,} bytes, Duration: {duration_seconds:.1f}s")

            return {
                "backup_id": str(backup_id),
                "backup_type": backup_type,
                "status": "success",
                "records_count": records_count,
                "size_bytes": size_bytes,
                "checksum": checksum,
                "duration_seconds": duration_seconds,
                "path": str(backup_path),
            }

        except Exception as e:
            logger.exception(f"Backup {backup_id} failed: {e}")
            if backup_id:
                async with get_connection() as conn:
                    await conn.execute(
                        "UPDATE backup_catalog SET status = 'failed', error_message = $1 WHERE id = $2",
                        str(e), backup_id
                    )
            raise

    async def _export_data(self, user_id: Optional[str] = None) -> tuple[bytes, int]:
        """Export memory data to JSON bytes for backup."""
        async with get_connection() as conn:
            if user_id:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, session_id, memory_type, scope,
                           content_encrypted, content_hash, tags, relevance_score,
                           access_count, created_at, updated_at, expires_at
                    FROM memory_entries WHERE user_id = $1
                    """,
                    user_id
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, session_id, memory_type, scope,
                           content_encrypted, content_hash, tags, relevance_score,
                           access_count, created_at, updated_at, expires_at
                    FROM memory_entries
                    """
                )

            assoc_rows = await conn.fetch("SELECT * FROM memory_associations")
            session_rows = await conn.fetch("SELECT id, user_id, session_id, message_count, started_at FROM context_sessions")

        def row_to_dict(row):
            d = dict(row)
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif isinstance(v, (bytes, memoryview)):
                    import base64
                    d[k] = {"__bytes__": True, "data": base64.b64encode(bytes(v)).decode()}
                elif isinstance(v, UUID):
                    d[k] = str(v)
            return d

        export = {
            "metadata": {
                "version": "1.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "scope": "user" if user_id else "full",
                "user_id": user_id,
            },
            "memory_entries": [row_to_dict(r) for r in rows],
            "memory_associations": [row_to_dict(r) for r in assoc_rows],
            "context_sessions": [row_to_dict(r) for r in session_rows],
        }

        return json.dumps(export, default=str).encode("utf-8"), len(rows)

    def _compress(self, data: bytes) -> bytes:
        return gzip.compress(data, compresslevel=6)

    def _decompress(self, data: bytes) -> bytes:
        return gzip.decompress(data)

    def _upload_to_s3(self, data: bytes, filename: str, backup_type: str):
        """Upload backup file to S3-compatible storage."""
        s3 = boto3.client("s3")
        key = f"{S3_PREFIX}{backup_type}/{filename}"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=data,
            ServerSideEncryption="AES256",
            StorageClass="STANDARD_IA" if backup_type == "archive" else "STANDARD",
        )
        logger.info(f"Backup uploaded to S3: s3://{S3_BUCKET}/{key}")

    def _rotate_local_backups(self, backup_type: str):
        """Keep only the most recent N backups locally."""
        backup_dir = BACKUP_DIR / backup_type
        backups = sorted(backup_dir.glob("backup_*.json.gz"), key=lambda p: p.stat().st_mtime)
        while len(backups) > MAX_LOCAL_BACKUPS:
            oldest = backups.pop(0)
            oldest.unlink()
            logger.info(f"Rotated old backup: {oldest.name}")

    # ─────────────────────────────────────────────
    # RESTORE OPERATIONS
    # ─────────────────────────────────────────────

    async def restore_from_backup(self, backup_id: Optional[UUID] = None, backup_path: Optional[str] = None) -> dict:
        """
        Restore memory data from a backup file.
        Optionally specify backup_id to look up the path, or provide backup_path directly.
        """
        if backup_id:
            async with get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT backup_path, checksum, status FROM backup_catalog WHERE id = $1",
                    backup_id
                )
            if not row or row["status"] != "success":
                raise ValueError(f"Backup {backup_id} not found or not successful.")
            path = Path(row["backup_path"])
            expected_checksum = row["checksum"]
        elif backup_path:
            path = Path(backup_path)
            expected_checksum = None
        else:
            raise ValueError("Provide either backup_id or backup_path.")

        if not path.exists():
            raise FileNotFoundError(f"Backup file not found: {path}")

        compressed = path.read_bytes()

        # Integrity check
        actual_checksum = checksum_bytes(compressed)
        if expected_checksum and actual_checksum != expected_checksum:
            raise ValueError(f"Checksum mismatch! Expected {expected_checksum}, got {actual_checksum}.")

        raw = self._decompress(compressed)
        export = json.loads(raw.decode("utf-8"))

        restored_memories = await self._import_memories(export.get("memory_entries", []))
        restored_sessions = await self._import_sessions(export.get("context_sessions", []))

        logger.info(f"Restore complete. Memories: {restored_memories}, Sessions: {restored_sessions}")
        return {
            "status": "success",
            "restored_memories": restored_memories,
            "restored_sessions": restored_sessions,
            "backup_metadata": export.get("metadata", {}),
        }

    async def _import_memories(self, entries: list[dict]) -> int:
        """Upsert memory entries from backup data."""
        import base64
        count = 0
        async with get_connection() as conn:
            for entry in entries:
                try:
                    enc_data = entry.get("content_encrypted")
                    if isinstance(enc_data, dict) and enc_data.get("__bytes__"):
                        enc_bytes = base64.b64decode(enc_data["data"])
                    else:
                        continue

                    await conn.execute(
                        """
                        INSERT INTO memory_entries
                            (id, user_id, session_id, memory_type, scope, content_encrypted,
                             content_hash, tags, relevance_score, access_count, created_at, expires_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        UUID(entry["id"]),
                        entry["user_id"],
                        entry.get("session_id"),
                        entry["memory_type"],
                        entry["scope"],
                        enc_bytes,
                        entry["content_hash"],
                        entry.get("tags") or [],
                        entry.get("relevance_score", 1.0),
                        entry.get("access_count", 0),
                        datetime.fromisoformat(entry["created_at"]),
                        datetime.fromisoformat(entry["expires_at"]) if entry.get("expires_at") else None,
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to import memory {entry.get('id')}: {e}")
        return count

    async def _import_sessions(self, sessions: list[dict]) -> int:
        count = 0
        async with get_connection() as conn:
            for session in sessions:
                try:
                    await conn.execute(
                        """
                        INSERT INTO context_sessions (id, user_id, session_id, message_count, started_at)
                        VALUES ($1,$2,$3,$4,$5)
                        ON CONFLICT (session_id) DO NOTHING
                        """,
                        UUID(session["id"]),
                        session["user_id"],
                        session["session_id"],
                        session.get("message_count", 0),
                        datetime.fromisoformat(session["started_at"]),
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to import session {session.get('id')}: {e}")
        return count

    # ─────────────────────────────────────────────
    # BACKUP CATALOG
    # ─────────────────────────────────────────────

    async def list_backups(self, backup_type: Optional[str] = None, limit: int = 20) -> list[dict]:
        """List recent backup records from the catalog."""
        async with get_connection() as conn:
            if backup_type:
                rows = await conn.fetch(
                    "SELECT id, backup_type, backup_path, checksum, size_bytes, records_count, started_at, completed_at, status FROM backup_catalog WHERE backup_type = $1 ORDER BY started_at DESC LIMIT $2",
                    backup_type, limit
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, backup_type, backup_path, checksum, size_bytes, records_count, started_at, completed_at, status FROM backup_catalog ORDER BY started_at DESC LIMIT $1",
                    limit
                )
        return [dict(r) for r in rows]

    async def verify_backup(self, backup_id: UUID) -> dict:
        """Verify backup file integrity against stored checksum."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT backup_path, checksum, size_bytes FROM backup_catalog WHERE id = $1",
                backup_id
            )
        if not row:
            return {"valid": False, "error": "Backup record not found."}

        path = Path(row["backup_path"])
        if not path.exists():
            return {"valid": False, "error": f"File missing: {path}"}

        data = path.read_bytes()
        actual = checksum_bytes(data)
        valid = actual == row["checksum"]

        return {
            "valid": valid,
            "backup_id": str(backup_id),
            "expected_checksum": row["checksum"],
            "actual_checksum": actual,
            "file_exists": True,
            "size_bytes": len(data),
        }


# ─────────────────────────────────────────────
# SCHEDULED BACKUP TASKS
# ─────────────────────────────────────────────

async def schedule_backups():
    """
    Background task that runs periodic backups:
    - Primary: every 1 hour
    - Secondary: every 6 hours
    - Archive: every 24 hours
    """
    system = BackupSystem()
    hourly_count = 0

    while True:
        try:
            logger.info("Running scheduled PRIMARY backup...")
            await system.run_backup("primary")
            hourly_count += 1

            if hourly_count % 6 == 0:
                logger.info("Running scheduled SECONDARY backup...")
                await system.run_backup("secondary")

            if hourly_count % 24 == 0:
                logger.info("Running scheduled ARCHIVE backup...")
                await system.run_backup("archive")
                hourly_count = 0

        except Exception as e:
            logger.exception(f"Scheduled backup failed: {e}")

        await asyncio.sleep(3600)  # Sleep 1 hour

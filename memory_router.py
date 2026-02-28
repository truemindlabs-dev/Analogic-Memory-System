"""
memory_router.py - FastAPI Router for Memory API Endpoints
Analogic Memory System for Omnira Synora AI

Endpoints:
  POST   /memory/store          - Store a new memory
  POST   /memory/recall         - Recall memories by context/query
  GET    /memory/{id}           - Get a specific memory
  DELETE /memory/{id}           - Soft-delete a memory
  GET    /memory/stats/{user}   - User memory statistics
  POST   /memory/session        - Create/update a context session
  GET    /memory/session/{id}   - Get session context
  POST   /backup/run            - Trigger manual backup
  GET    /backup/list           - List backup records
  POST   /backup/restore        - Restore from a backup
  GET    /backup/verify/{id}    - Verify backup integrity
  POST   /analogic/associate    - Create analogic association
  GET    /analogic/graph/{id}   - Get association graph
"""

import logging
import os
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from pydantic import BaseModel, Field

from memory_engine import MemoryEngine
from backup_system import BackupSystem
from analogic_core import AnalogicCore
from security import verify_api_token, hash_api_token

logger = logging.getLogger(__name__)

router = APIRouter()
engine = MemoryEngine()
backup_sys = BackupSystem()
analogic = AnalogicCore()

# Stored API token hash (in production, store in DB or secrets manager)
_API_TOKEN_HASH = hash_api_token(os.getenv("API_TOKEN", "dev-token-change-in-production"))


# ─────────────────────────────────────────────
# AUTH DEPENDENCY
# ─────────────────────────────────────────────

async def require_auth(x_api_token: str = Header(..., alias="X-API-Token")):
    if not verify_api_token(x_api_token, _API_TOKEN_HASH):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token.")
    return True


# ─────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────

class StoreMemoryRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    memory_type: str = Field("general", pattern="^(general|context|knowledge|association)$")
    scope: str = Field("long_term", pattern="^(short_term|long_term)$")
    session_id: Optional[str] = None
    tags: Optional[list[str]] = Field(default_factory=list)
    ttl_hours: Optional[int] = Field(None, ge=1, le=8760)

class RecallMemoryRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=255)
    query: str = Field(..., min_length=1)
    memory_type: Optional[str] = None
    scope: Optional[str] = None
    session_id: Optional[str] = None
    tags: Optional[list[str]] = None
    limit: int = Field(20, ge=1, le=100)

class CreateSessionRequest(BaseModel):
    user_id: str
    session_id: str

class UpdateSessionRequest(BaseModel):
    session_id: str
    context_data: dict

class BackupRequest(BaseModel):
    backup_type: str = Field("primary", pattern="^(primary|secondary|archive)$")
    user_id: Optional[str] = None

class RestoreRequest(BaseModel):
    backup_id: Optional[str] = None
    backup_path: Optional[str] = None

class AssociateRequest(BaseModel):
    source_memory_id: str
    target_memory_id: str
    association_type: str
    strength: float = Field(0.5, ge=0.0, le=1.0)


# ─────────────────────────────────────────────
# MEMORY ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/memory/store", tags=["Memory"])
async def store_memory(req: StoreMemoryRequest, _auth=Depends(require_auth)):
    """Store a new encrypted memory entry."""
    try:
        result = await engine.store_memory(
            user_id=req.user_id,
            content=req.content,
            memory_type=req.memory_type,
            scope=req.scope,
            session_id=req.session_id,
            tags=req.tags,
            ttl_hours=req.ttl_hours,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"store_memory error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")


@router.post("/memory/recall", tags=["Memory"])
async def recall_memory(req: RecallMemoryRequest, _auth=Depends(require_auth)):
    """Recall relevant memories based on query context."""
    try:
        results = await engine.recall_memory(
            user_id=req.user_id,
            query=req.query,
            memory_type=req.memory_type,
            scope=req.scope,
            session_id=req.session_id,
            tags=req.tags,
            limit=req.limit,
        )
        return {"success": True, "count": len(results), "memories": results}
    except Exception as e:
        logger.exception(f"recall_memory error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")


@router.get("/memory/{memory_id}", tags=["Memory"])
async def get_memory(memory_id: UUID, user_id: str = Query(...), _auth=Depends(require_auth)):
    """Retrieve a specific memory entry by ID."""
    result = await engine.get_memory(memory_id, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"success": True, "data": result}


@router.delete("/memory/{memory_id}", tags=["Memory"])
async def delete_memory(memory_id: UUID, user_id: str = Query(...), _auth=Depends(require_auth)):
    """Soft-delete a memory entry."""
    deleted = await engine.delete_memory(memory_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found or already deleted.")
    return {"success": True, "message": "Memory deleted."}


@router.get("/memory/stats/{user_id}", tags=["Memory"])
async def get_user_stats(user_id: str, _auth=Depends(require_auth)):
    """Get memory statistics for a user."""
    stats = await engine.get_user_stats(user_id)
    return {"success": True, "user_id": user_id, "stats": stats}


# ─────────────────────────────────────────────
# SESSION ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/memory/session/create", tags=["Session"])
async def create_session(req: CreateSessionRequest, _auth=Depends(require_auth)):
    """Create or refresh a context session."""
    result = await engine.create_session(req.user_id, req.session_id)
    return {"success": True, "data": dict(result)}


@router.post("/memory/session/update", tags=["Session"])
async def update_session(req: UpdateSessionRequest, _auth=Depends(require_auth)):
    """Update the context data for a session."""
    updated = await engine.update_session_context(req.session_id, req.context_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"success": True, "message": "Session context updated."}


@router.get("/memory/session/{session_id}", tags=["Session"])
async def get_session(session_id: str, _auth=Depends(require_auth)):
    """Retrieve the context data for a session."""
    context = await engine.get_session_context(session_id)
    if not context:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"success": True, "session_id": session_id, "context": context}


# ─────────────────────────────────────────────
# BACKUP ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/backup/run", tags=["Backup"])
async def run_backup(req: BackupRequest, _auth=Depends(require_auth)):
    """Trigger a manual backup."""
    try:
        result = await backup_sys.run_backup(backup_type=req.backup_type, user_id=req.user_id)
        return {"success": True, "backup": result}
    except Exception as e:
        logger.exception(f"Backup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Backup failed: {str(e)}")


@router.get("/backup/list", tags=["Backup"])
async def list_backups(
    backup_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    _auth=Depends(require_auth)
):
    """List available backups from the catalog."""
    backups = await backup_sys.list_backups(backup_type=backup_type, limit=limit)
    return {"success": True, "count": len(backups), "backups": backups}


@router.post("/backup/restore", tags=["Backup"])
async def restore_backup(req: RestoreRequest, _auth=Depends(require_auth)):
    """Restore data from a backup file."""
    try:
        backup_id = UUID(req.backup_id) if req.backup_id else None
        result = await backup_sys.restore_from_backup(backup_id=backup_id, backup_path=req.backup_path)
        return {"success": True, "restore": result}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Restore failed: {e}")
        raise HTTPException(status_code=500, detail="Restore failed.")


@router.get("/backup/verify/{backup_id}", tags=["Backup"])
async def verify_backup(backup_id: UUID, _auth=Depends(require_auth)):
    """Verify the integrity of a specific backup."""
    result = await backup_sys.verify_backup(backup_id)
    return {"success": True, "verification": result}


# ─────────────────────────────────────────────
# ANALOGIC ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/analogic/associate", tags=["Analogic"])
async def create_association(req: AssociateRequest, _auth=Depends(require_auth)):
    """Create an analogic association between two memories."""
    try:
        result = await analogic.create_association(
            source_id=UUID(req.source_memory_id),
            target_id=UUID(req.target_memory_id),
            association_type=req.association_type,
            strength=req.strength,
        )
        return {"success": True, "association": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/analogic/graph/{memory_id}", tags=["Analogic"])
async def get_association_graph(
    memory_id: UUID,
    user_id: str = Query(...),
    direction: str = Query("both"),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
    _auth=Depends(require_auth)
):
    """Retrieve the analogic association graph for a memory node."""
    associations = await analogic.get_associations(
        memory_id=memory_id,
        direction=direction,
        min_strength=min_strength,
    )
    context = await analogic.get_analogic_context(user_id, [memory_id])
    return {
        "success": True,
        "memory_id": str(memory_id),
        "associations": associations,
        "graph_context": context,
    }

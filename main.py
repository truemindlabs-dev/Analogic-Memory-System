"""
main.py - Application Entrypoint
Analogic Memory System for Omnira Synora AI

Production-ready FastAPI application with:
  - PostgreSQL-backed permanent memory storage
  - AES-256-GCM encryption at rest
  - Multi-layer backup system (primary / secondary / archive)
  - Analogic reasoning and association engine
  - Scheduled background tasks
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from database import init_database, close_pool
from memory_router import router
from backup_system import schedule_backups
from memory_engine import MemoryEngine

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# APP LIFECYCLE
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Analogic Memory System starting up...")

    # Initialize database schema
    await init_database()
    logger.info("✅ Database initialized.")

    # Start scheduled backup task
    backup_task = asyncio.create_task(schedule_backups())
    logger.info("✅ Backup scheduler started.")

    # Start memory cleanup task (purge expired short-term memories hourly)
    cleanup_task = asyncio.create_task(periodic_cleanup())
    logger.info("✅ Memory cleanup scheduler started.")

    yield

    # Shutdown
    backup_task.cancel()
    cleanup_task.cancel()
    await close_pool()
    logger.info("🛑 Analogic Memory System shut down cleanly.")


async def periodic_cleanup():
    """Purge expired short-term memories every hour."""
    engine = MemoryEngine()
    while True:
        try:
            count = await engine.purge_expired_memories()
            if count:
                logger.info(f"Cleanup: purged {count} expired memories.")
        except Exception as e:
            logger.warning(f"Cleanup task error: {e}")
        await asyncio.sleep(3600)


# ─────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────

app = FastAPI(
    title="Analogic Memory System",
    description=(
        "High-permanence, encrypted memory and analogic reasoning system "
        "designed for advanced AI agents such as Omnira Synora."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Token", "Content-Type"],
)

# Only enable trusted hosts middleware in production
TRUSTED_HOSTS = os.getenv("TRUSTED_HOSTS", "")
if TRUSTED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS.split(","))


# ─────────────────────────────────────────────
# GLOBAL ERROR HANDLER
# ─────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"success": False, "error": "An internal error occurred."},
    )


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

app.include_router(router, prefix="/api/v1")


@app.get("/", tags=["Health"])
async def root():
    return {
        "system": "Analogic Memory System",
        "version": "1.0.0",
        "status": "operational",
        "ai_agent": "Omnira Synora",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for Railway / load balancers."""
    from database import get_pool
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "version": "1.0.0",
    }


# ─────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENV", "production") == "development",
        log_level="info",
        access_log=True,
    )

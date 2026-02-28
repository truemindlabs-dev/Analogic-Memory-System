# 🧠 Analogic Memory System
### Permanent High-Level Memory for Omnira Synora AI

Production-ready memory infrastructure with AES-256-GCM encryption, analogic reasoning, and multi-layer automated backup.

---

## 📁 Project Structure

```
analogic_memory_system/
├── main.py              # FastAPI app entrypoint, lifecycle management
├── memory_engine.py     # Core memory CRUD + session management
├── analogic_core.py     # Analogic reasoning & association graph engine
├── backup_system.py     # Multi-layer backup (primary / secondary / archive)
├── database.py          # PostgreSQL connection pool + schema initialization
├── security.py          # AES-256-GCM encryption, hashing, auth utilities
├── memory_router.py     # All API route handlers (FastAPI Router)
├── requirements.txt     # Python dependencies
└── .env.example         # Environment variable template
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your DATABASE_URL, API_TOKEN, MASTER_ENCRYPTION_KEY
```

### 3. Run the Server

```bash
python main.py
# or with uvicorn directly:
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. Access API Docs

```
http://localhost:8000/docs
```

---

## 🔐 Security

| Feature | Implementation |
|---|---|
| Memory Encryption | AES-256-GCM (per-user derived keys) |
| Key Derivation | PBKDF2-HMAC-SHA256 (480,000 iterations) |
| API Authentication | HMAC token with constant-time comparison |
| Input Validation | Pydantic v2 + custom sanitization |
| Backup Integrity | SHA-256 checksums on every backup file |

---

## 📡 API Reference

All endpoints require the header: `X-API-Token: <your-token>`
Base URL: `/api/v1`

### Memory

| Method | Endpoint | Description |
|---|---|---|
| POST | `/memory/store` | Store encrypted memory entry |
| POST | `/memory/recall` | Recall relevant memories by query |
| GET | `/memory/{id}?user_id=` | Retrieve specific memory |
| DELETE | `/memory/{id}?user_id=` | Soft-delete memory |
| GET | `/memory/stats/{user_id}` | User memory statistics |

### Session

| Method | Endpoint | Description |
|---|---|---|
| POST | `/memory/session/create` | Create/refresh context session |
| POST | `/memory/session/update` | Update session context data |
| GET | `/memory/session/{id}` | Retrieve session context |

### Backup

| Method | Endpoint | Description |
|---|---|---|
| POST | `/backup/run` | Trigger manual backup |
| GET | `/backup/list` | List backup catalog |
| POST | `/backup/restore` | Restore from backup |
| GET | `/backup/verify/{id}` | Verify backup integrity |

### Analogic

| Method | Endpoint | Description |
|---|---|---|
| POST | `/analogic/associate` | Create memory association |
| GET | `/analogic/graph/{id}` | Get association graph |

---

## 💾 Backup Architecture

```
Hourly  → Primary  (local filesystem, last 48 kept)
6-hourly → Secondary (local + S3-compatible object storage)
Daily   → Archive   (local + S3 STANDARD_IA cold storage)
```

All backups are gzip-compressed and verified with SHA-256 checksums.

---

## 🚂 Railway Deployment

1. Connect your GitHub repo to Railway
2. Add a PostgreSQL plugin in Railway
3. Set environment variables (Railway auto-injects `DATABASE_URL`)
4. Deploy — Railway will run `python main.py` automatically

```bash
# Procfile (optional)
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## 🧩 Integration with Base44

Use the `recall` endpoint to inject relevant memories into your AI prompt context:

```python
import httpx

headers = {"X-API-Token": "your-token"}

# Store memory
httpx.post("https://your-api.railway.app/api/v1/memory/store", headers=headers, json={
    "user_id": "user-123",
    "content": "User prefers concise answers and dislikes jargon.",
    "memory_type": "user_preference",
    "scope": "long_term",
    "tags": ["preference", "communication"]
})

# Recall before generating AI response
response = httpx.post("https://your-api.railway.app/api/v1/memory/recall", headers=headers, json={
    "user_id": "user-123",
    "query": "How should I format this response?",
    "limit": 5
})
memories = response.json()["memories"]
# Inject memories into your system prompt
```

# 🚂 Panduan Deploy ke Railway — Analogic Memory System

---

## Prasyarat

- Akun Railway → https://railway.app
- Akun GitHub (untuk connect repo)
- Git terinstall di komputer kamu

---

## LANGKAH 1 — Siapkan Repository GitHub

```bash
# Di folder project kamu
git init
git add .
git commit -m "Initial commit: Analogic Memory System"

# Buat repo baru di GitHub, lalu:
git remote add origin https://github.com/USERNAME/analogic-memory.git
git branch -M main
git push -u origin main
```

---

## LANGKAH 2 — Buat Project di Railway

1. Buka https://railway.app → klik **"New Project"**
2. Pilih **"Deploy from GitHub repo"**
3. Pilih repo `analogic-memory` yang baru kamu push
4. Railway akan otomatis mendeteksi Python dan mulai build

---

## LANGKAH 3 — Tambahkan PostgreSQL

1. Di dashboard Railway project kamu, klik **"+ New"**
2. Pilih **"Database" → "PostgreSQL"**
3. Railway otomatis meng-inject variabel `DATABASE_URL` ke service kamu
4. **Tidak perlu konfigurasi manual** — langsung terhubung

---

## LANGKAH 4 — Set Environment Variables

Di Railway dashboard → klik service kamu → tab **"Variables"** → tambahkan satu per satu:

```
# WAJIB diisi
API_TOKEN          = (generate: python -c "import secrets; print(secrets.token_urlsafe(48))")
MASTER_ENCRYPTION_KEY = (generate: python -c "import secrets; print(secrets.token_hex(32))")
SECRET_PASSPHRASE  = kata-sandi-rahasia-kamu
KEY_SALT           = salt-unik-kamu-2024
ENV                = production

# Opsional — jika pakai S3 untuk backup offsite
S3_BACKUP_BUCKET   = nama-bucket-s3-kamu
AWS_ACCESS_KEY_ID  = (dari AWS IAM)
AWS_SECRET_ACCESS_KEY = (dari AWS IAM)
AWS_DEFAULT_REGION = ap-southeast-1

# CORS — ganti dengan domain frontend kamu
ALLOWED_ORIGINS    = https://app-kamu.base44.com,https://frontend-kamu.railway.app
```

> ⚠️ **DATABASE_URL tidak perlu diisi manual** — Railway inject otomatis dari plugin PostgreSQL.

---

## LANGKAH 5 — Verifikasi Deploy

Setelah deploy selesai (biasanya 1–3 menit), Railway memberikan URL publik seperti:
```
https://analogic-memory-production.up.railway.app
```

Cek endpoint health:
```bash
curl https://analogic-memory-production.up.railway.app/health
```

Response yang diharapkan:
```json
{
  "status": "ok",
  "database": "connected",
  "version": "1.0.0"
}
```

Buka API docs:
```
https://analogic-memory-production.up.railway.app/docs
```

---

## LANGKAH 6 — Test API

```bash
# Simpan token dari environment variable
TOKEN="api-token-yang-kamu-set"
BASE="https://analogic-memory-production.up.railway.app/api/v1"

# ✅ Store memory
curl -X POST "$BASE/memory/store" \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "content": "Pengguna lebih suka jawaban singkat dan padat.",
    "memory_type": "user_preference",
    "scope": "long_term",
    "tags": ["preferensi", "gaya_komunikasi"]
  }'

# ✅ Recall memory
curl -X POST "$BASE/memory/recall" \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "query": "bagaimana cara menjawab dengan baik?",
    "limit": 5
  }'

# ✅ Trigger backup manual
curl -X POST "$BASE/backup/run" \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"backup_type": "primary"}'
```

---

## Integrasi dengan Base44

Di Base44, gunakan HTTP Request node dengan konfigurasi:

```
Method : POST
URL    : https://analogic-memory-production.up.railway.app/api/v1/memory/recall
Headers:
  X-API-Token : <API_TOKEN kamu>
  Content-Type: application/json
Body:
  {
    "user_id": "{{user.id}}",
    "query": "{{input.message}}",
    "limit": 5
  }
```

Hasil recall bisa langsung diinjeksikan ke system prompt AI kamu sebelum generate response.

---

## Backup Otomatis

Setelah deploy, backup berjalan otomatis di background:

| Interval | Tipe | Lokasi |
|---|---|---|
| Setiap 1 jam | Primary | `/var/backups/analogic_memory/primary/` |
| Setiap 6 jam | Secondary | Local + S3 (jika dikonfigurasi) |
| Setiap 24 jam | Archive | Local + S3 STANDARD_IA |

Cek daftar backup:
```bash
curl "$BASE/backup/list" -H "X-API-Token: $TOKEN"
```

---

## Troubleshooting

| Masalah | Solusi |
|---|---|
| Build gagal | Pastikan `requirements.txt` ada di root folder |
| `DATABASE_URL` error | Pastikan PostgreSQL plugin sudah ditambahkan di Railway |
| 401 Unauthorized | Cek `API_TOKEN` di Railway Variables sudah sama |
| Enkripsi error | Pastikan `MASTER_ENCRYPTION_KEY` adalah 64 karakter hex |
| `/health` mengembalikan `degraded` | Cek log Railway → kemungkinan DB belum siap |

---

## Update & Redeploy

Setiap `git push` ke branch `main` akan otomatis trigger redeploy di Railway:

```bash
git add .
git commit -m "Update fitur XYZ"
git push origin main
# Railway otomatis redeploy dalam 1-2 menit
```

> ✅ Data memori **tidak hilang** saat redeploy karena tersimpan permanen di PostgreSQL.

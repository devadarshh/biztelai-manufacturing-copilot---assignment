# BiztelAI Manufacturing Copilot

A source-backed manufacturing document assistant for supervisors who need to find procedures, maintenance steps, quality requirements, and safety guidance from approved PDFs.

## Problem statement

Manufacturing supervisors often need an answer while coordinating work on a phone, tablet, or workstation. Searching multiple manuals and SOPs takes time, and using an outdated procedure can create operational risk. This MVP indexes local PDF documents, retrieves relevant passages, and asks Gemini to answer from those passages with source citations and revision status.

## Features

- Ask questions in a responsive React chat interface.
- Extract PDF text with PyMuPDF, split it into overlapping chunks, embed locally with Sentence Transformers by default, and store vectors in Qdrant.
- Generate answers with Gemini using retrieved document context rather than a hardcoded sample-answer path.
- Return document, page, section (when available), revision, and revision status with cited evidence.
- Detect revision labels/effective dates from PDF cover text and filenames; label superseded and future-effective revisions.
- Prefer current revisions; exclude not-yet-effective content from answer context; identify superseded evidence.
- Re-index documents from the UI or API. Re-indexing is needed after adding or replacing PDFs.
- Read root-level PDFs from the Supabase Storage `files` bucket when configured, cache them privately, and fall back to the local PDF folder if Supabase is unavailable.
- Save chat IDs, titles, user/assistant messages, citations, and timestamps in this browser's local storage; restore the selected chat after refresh and reopen recent chats without a chat database.
- Upload PDFs from the UI; index synchronously for local development or through a Redis/RQ worker when shared Redis and Qdrant Cloud are configured.
- Configure local persistent Qdrant or Qdrant Cloud.
- Expose a health endpoint that reports whether backend services initialized successfully.
- Generate a Markdown report from the ten assignment questions using actual responses from a running API.

## Technology and architecture

- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS, Radix UI/shadcn-style primitives.
- **Backend:** Python 3.11, FastAPI, Pydantic Settings, LangChain.
- **LLM:** Google Gemini (`gemini-2.5-flash` by default).
- **Embeddings:** Local Sentence Transformers (`sentence-transformers/all-MiniLM-L6-v2` by default); hosted Hugging Face Inference API is optional.
- **Vector store:** Qdrant local persistent mode or Qdrant Cloud.
- **Job queue:** Redis/RQ for queued ingestion when shared Redis and Qdrant Cloud are configured.
- **Chat history:** Browser local storage, scoped to the current browser profile and local app origin; no database or chat API endpoints are used.

```text
Browser (React/Vite)
  ├─ /api/ask ────────────────┐
  ├─ /api/documents            │ Vite proxy (default: 127.0.0.1:8001)
  ├─ /api/storage              │
  ├─ /api/ingest ──────────────┤
  └─ /api/documents/upload ────┘
                                  FastAPI
Supabase Storage (`files`) ──> private local cache ──> PyMuPDF page extraction
          └─ on sync failure: PDFs in storage/documents ──┘
                         ──> recursive chunking + revision metadata
                         ──> local sentence embeddings ──> Qdrant
Question ──> local query embedding ──> Qdrant retrieval
         ──> Gemini with retrieved context ──> answer + validated citations

Queued uploads: browser ──> FastAPI ──> Supabase Storage ──> Redis/RQ
                RQ worker ──> PDF extraction/chunking ──> local embeddings ──> Qdrant
```

## Project structure

```text
backend/
  app/
    main.py                   FastAPI application and endpoints
    config.py                 Environment-backed settings
    models.py                 Request and response schemas
    services/
      document_service.py     Supabase/local PDF sync, extraction, and inventory
      document_revision.py    Revision/date parsing and classification
      ingestion_service.py    Chunking and indexing
      embedding_service.py    Local or hosted Hugging Face embeddings
      ingestion_queue.py     Redis/RQ queue configuration and job lookup
      vector_store.py         Qdrant operations
      rag_service.py          Retrieval, prompt construction, and citations
    worker.py                 RQ worker task and process entry point
  scripts/
    run_assignment_eval.py    Run and save the ten real API responses
  tests/
frontend/
  src/App.tsx                 Chat and indexed-document UI
  src/components/ui/          Radix-based UI primitives
  vite.config.ts              Tailwind, LAN binding, and API proxy
storage/
  documents/                  Local fallback for approved PDF files
  supabase_cache/              Private cache of PDFs synced from Supabase
  uploads/                     Local-development PDF uploads
```

## Prerequisites

- macOS, Linux, or Windows with Python 3.11 and Node.js 20+.
- Google Gemini API key. Hosted Hugging Face embedding inference is optional; the default local model downloads once and runs without hosted inference credits.
- Either local Qdrant storage (default) or a Qdrant Cloud URL and API key.
- Network access to the configured model providers when using their hosted APIs.

## Environment configuration

Copy `.env.example` to `.env` in the project root and fill in **your own rotated credentials**. Never commit `.env`; it is ignored by Git. Do not put credentials in the frontend source or any `VITE_` variable.

Create a Gemini key in [Google AI Studio](https://aistudio.google.com/apikey), then put it in the root `.env` as `GEMINI_API_KEY=your_key_here`. Never paste the key into chat or commit it.

| Variable | Required | Purpose |
|---|---:|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key for answer generation |
| `GEMINI_MODEL` | No | Defaults to `gemini-2.5-flash` |
| `GEMINI_TIMEOUT_SECONDS` | No | Gemini request timeout; defaults to 45 seconds |
| `HF_EMBEDDING_PROVIDER` | No | `local` (default) runs the model on this machine; `api` uses hosted Hugging Face inference |
| `HUGGINGFACE_API_KEY` | Only for `api` | Token for hosted embeddings; local inference does not require this key |
| `HF_EMBEDDING_MODEL` | No | Defaults to `sentence-transformers/all-MiniLM-L6-v2` |
| `HF_EMBEDDING_DIMENSION` | No | Defaults to 384 for the default MiniLM embedding model; update it if you choose a model with a different output size |
| `SUPABASE_URL` | For Supabase Storage | Supabase project URL; only needed when PDFs are stored in Supabase Storage |
| `SUPABASE_SERVICE_ROLE_KEY` | For Supabase Storage | Backend-only Storage access key; never put it in frontend variables or commit it |
| `SUPABASE_STORAGE_BUCKET` | No | Defaults to `files`; PDFs are read from the bucket root |
| `SUPABASE_CACHE_PATH` | No | Defaults to `./storage/supabase_cache`; synced cache is git-ignored |
| `DOCUMENT_STORAGE_MODE` | No | `auto` uses Supabase when configured; `local` forces the local folder; `supabase` requests remote storage |
| `DOCUMENT_UPLOAD_PATH` | No | Local upload destination; defaults to `./storage/uploads` |
| `DOCUMENT_EXCLUDE_FILES` | No | Optional comma-separated PDF filenames to leave out of indexing |
| `QDRANT_MODE` | No | `auto` selects cloud when `QDRANT_URL` is set; `local` or `cloud` forces a mode |
| `REDIS_URL` | For queued jobs | Redis-compatible connection URL; never put it in a frontend variable |
| `INGESTION_MODE` | No | `auto` queues only with Redis, Supabase Storage, and shared Qdrant Cloud; `sync` is local development; `queue` requires all three shared services |
| `INGESTION_QUEUE_NAME` | No | RQ queue name; defaults to `document-ingestion` |
| `INGESTION_JOB_TIMEOUT_SECONDS` | No | Maximum worker runtime per document; defaults to 900 seconds |
| `MAX_UPLOAD_SIZE_MB` | No | PDF upload limit; defaults to 50 MB |
| `QDRANT_URL` | No | Leave blank for local mode; set for Qdrant Cloud |
| `QDRANT_API_KEY` | For cloud | Cloud Qdrant authentication |
| `QDRANT_STORAGE_PATH` | Local mode | Defaults to `./storage/qdrant_db` |
| `QDRANT_COLLECTION_NAME` | No | Defaults to `manufacturing_documents` |
| `DOCUMENT_STORAGE_PATH` | No | Defaults to `./storage/documents` |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | No | Defaults to 1000 / 200 characters |
| `TOP_K` | No | Number of primary evidence chunks (default 4) |
| `RETRIEVAL_THRESHOLD` | No | Minimum cosine score; when unset, runtime uses a conservative 0.35 starting threshold. Calibrate with the actual PDF corpus before operational use. |
| `APP_HOST` / `APP_PORT` | No | Defaults to `0.0.0.0` / `8001` |
| `CORS_ORIGINS` | For direct API access | Comma-separated browser origins. For local-network direct access, include `http://<laptop-LAN-IP>:5173`. |
| `VITE_API_BASE_URL` | No | Defaults to same-origin `/api`; use `http://<laptop-LAN-IP>:8001/api` for direct frontend-to-backend calls. |
| `VITE_API_PROXY_TARGET` | No | Vite proxy target; defaults to `http://127.0.0.1:8001`. |

`VITE_` values are visible to browser users and must never contain secrets. `DATABASE_URL`, Google OAuth, and NextAuth variables are not used by this MVP.

Chat history is stored in the browser's local storage for the current origin (for example, `http://localhost:5174`). It requires no Supabase setup and will not sync to another browser or device. Supabase configuration is only used for PDF storage when that option is enabled.

## Installation

From the project root:

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend install
cp .env.example .env
```

Edit `.env` with rotated credentials before starting the API. If `.env` already exists, keep it private and update it locally rather than overwriting it.

## Run locally

Recommended one-command local development from the project root:

```bash
./run-local.sh
```

Open <http://localhost:5174>. This uses a separate backend port (`8001`) so an unrelated/stale service on port `8000` cannot intercept API requests. It uses the four company PDFs in `sample-docs/` as a local development corpus, excludes `Sample_Questions.pdf`, and forces local Qdrant, local embeddings, and synchronous indexing even if `.env` contains cloud settings. The first indexing or question request downloads the embedding model once; this does not use Hugging Face inference credits. Click **Re-index** after switching from hosted embeddings so indexed document vectors match the local model. Upload PDFs from the UI; they are stored under `storage/local_uploads` and indexed immediately. The script does not terminate any existing services.

Alternatively, run both services manually in separate terminals:

**Terminal 1 — backend:**

```bash
cd backend
DOCUMENT_STORAGE_MODE=local \
DOCUMENT_STORAGE_PATH="$(cd .. && pwd)/sample-docs" \
DOCUMENT_EXCLUDE_FILES=Sample_Questions.pdf \
QDRANT_MODE=local \
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

**Terminal 2 — frontend:**

```bash
cd frontend
VITE_API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev -- --host 0.0.0.0 --port 5174
```

The launcher waits for `/api/health` before starting Vite, so the first browser requests cannot race backend startup. If its default ports are occupied, choose free ports without stopping unknown processes:

```bash
BACKEND_PORT=8002 FRONTEND_PORT=5175 LOCAL_QDRANT_PATH=./storage/alternate_local_qdrant ./run-local.sh
```

### Scalable queued ingestion

For queued ingestion, configure `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `REDIS_URL`, `QDRANT_URL`, and `QDRANT_API_KEY` in the private root `.env`. Embeddings run locally by default on the API and worker, so a Hugging Face inference API key/credit balance is not needed. Stop any already-running local app, then start the backend, worker, and frontend together from the project root:

```bash
./run-queued.sh
```

The launcher checks the shared services, starts the worker and backend, waits for a healthy API, and then starts the frontend. Press `Ctrl+C` to stop all three processes. Ports default to 8001 (backend) and 5174 (frontend); override them with `BACKEND_PORT` and `FRONTEND_PORT` if those ports are occupied.

The upload endpoint stores the PDF in shared Supabase Storage first, enqueues the document name (not file bytes), and returns a job ID. If the shared storage upload fails, the request fails instead of queuing an inaccessible local-only file. The worker syncs the stored PDF, extracts and chunks it, generates local embeddings, and upserts them to shared Qdrant. The UI polls job status and reports completion or failure. `INGESTION_MODE=auto` uses the queue only when Redis, Supabase Storage, and shared Qdrant Cloud are configured; otherwise it indexes synchronously. Do not run independent workers with embedded local Qdrant storage.

### Local-network access from a phone/tablet

1. Connect the laptop and mobile device to the same trusted Wi-Fi.
2. Find the laptop LAN address (macOS example: `ipconfig getifaddr en0`).
3. Keep the backend bound to `0.0.0.0` and start Vite with `--host 0.0.0.0`.
4. Open `http://<laptop-LAN-IP>:5174` on the mobile device. Same-origin `/api` requests go through Vite's proxy to the backend.
5. If configuring direct API requests instead, set `VITE_API_BASE_URL=http://<laptop-LAN-IP>:8001/api` and add `http://<laptop-LAN-IP>:5174` to `CORS_ORIGINS`.
6. With the launcher, allow port 5174 through the laptop firewall only on the trusted private network; the backend stays bound to localhost behind the Vite proxy.

This repository environment has not been tested from a separate physical device. Follow the steps above and verify health, document listing, and an API question on the phone.

## PDF ingestion and revision handling

1. Provide the four approved PDFs in the root of the configured Supabase bucket when Supabase is configured; otherwise put them in the configured `DOCUMENT_STORAGE_PATH` (the local launcher uses `sample-docs/`):
   - `QC-SOP-014 Rev03 – Incoming Material Inspection`
   - `MNT-MAN-CNC-02 – Preventive Maintenance Manual`
   - `QC-SOP-014 Rev04 – Incoming Material Inspection`
   - `SAF-SOP-003 – Lockout/Tagout Procedure`
   Supabase is authoritative when configured, and a successfully listed empty bucket remains empty (it does not use local fallback files).
2. Confirm the document list reports each file and its extraction status. The UI shows whether Supabase or local fallback storage is active, including an explicit warning when Supabase sync fails.
3. Click **Re-index** in the UI, or run:

   ```bash
   curl -X POST http://127.0.0.1:8001/api/ingest \
     -H 'Content-Type: application/json' \
     -d '{"reindex": true}'
   ```

4. Confirm `pdf_count`, `chunks_indexed`, and `errors` in the response. A filename being listed does not prove it was indexed.

Unchanged chunks use deterministic IDs and are upserted; changed file content replaces stale chunks for that filename after the new chunks have been written. `reindex: true` rebuilds the collection from available extractable PDFs. Empty or failed extraction does not erase the existing index.

Revision extraction is heuristic: it reads explicit revision labels and effective dates from the first three PDF pages and known revision/date patterns in filenames. Ambiguous revision families are left `revision_unknown`; review document statuses before relying on them. PDFs with scanned images need OCR, which is not currently implemented.

Local mode needs no separate database service. Qdrant persists under `storage/qdrant_db`. Cloud mode requires `QDRANT_URL` and `QDRANT_API_KEY`.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Backend readiness flags |
| `GET` | `/api/documents` | PDF inventory, extraction status, and revision status |
| `GET` | `/api/storage` | Active PDF source and fallback warning |
| `POST` | `/api/ingest` | Index PDFs; body `{"reindex": true}` rebuilds the collection |
| `POST` | `/api/documents/upload` | Upload a PDF as multipart field `file`; queues when configured or indexes synchronously |
| `GET` | `/api/jobs/{job_id}` | Poll queued ingestion status |
| `POST` | `/api/ask` | Ask a grounded question; body `{"question":"..."}` |

`/api/ask` returns an answer, `found_in_documents`, confidence, and source evidence containing document/page and revision metadata. Requests must contain a nonblank question of at most 2000 characters. Service failures return explicit non-success HTTP statuses. An uploaded file becomes eligible for retrieval after upload/indexing or the queued job reports completion. `GET /api/storage` reports the active document source and any local-fallback warning without exposing credentials.

Quick checks:

```bash
curl http://127.0.0.1:8001/api/health
curl http://127.0.0.1:8001/api/documents
curl -X POST http://127.0.0.1:8001/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Who is allowed to remove a lockout lock from a machine?"}'
```

## Assignment questions and actual results

The four assigned manufacturing PDFs and `Sample_Questions.pdf` are present in `sample-docs/` and were locally extracted (8 pages total). They were not placed in the configured Supabase bucket or default local source, and no live model/API requests were made. Therefore no API-generated answers or citations can be reported yet. The table intentionally records **NOT RUN** rather than substituting expected answers or fabricated results.

| # | Question | Actual result |
|---:|---|---|
| 1 | Recommended coolant concentration for the TX-200 CNC turning centre? | NOT RUN — live API evaluation not performed |
| 2 | Meaning and operator response for alarm AL-104? | NOT RUN — live API evaluation not performed |
| 3 | Who may remove a machine lockout lock? | NOT RUN — live API evaluation not performed |
| 4 | Outer diameter tolerance for the BH-220 bearing housing? | NOT RUN — live API evaluation not performed |
| 5 | Spindle runout check frequency and responsible role? | NOT RUN — live API evaluation not performed |
| 6 | What happens if the supplier's Material Test Certificate is missing? | NOT RUN — live API evaluation not performed |
| 7 | How many samples should be inspected for a lot of 1,000 pieces? | NOT RUN — live API evaluation not performed |
| 8 | What is the normal hydraulic system pressure? | NOT RUN — live API evaluation not performed |
| 9 | What is the procedure for removing a lock when its owner is absent? | NOT RUN — live API evaluation not performed |
| 10 | What is the factory manager's salary? | NOT RUN — live API evaluation not performed |

After adding the PDFs and starting the services, run:

```bash
cd backend
../.venv/bin/python scripts/run_assignment_eval.py
```

The script sends all ten questions to the live `/api/ask` endpoint and creates `assignment_qa_results.md` with the actual returned answers and citations. Review that file before including its answers in a submission. It does not contain expected answers or fixture responses.

## Tests and checks

```bash
cd backend && ../.venv/bin/python -m pytest -q
cd frontend && npm run lint
cd frontend && npm run build
```

The backend tests include generated-PDF text extraction, local PDF upload/indexing, queued-upload storage-failure handling, mocked Supabase synchronization/fallback behavior, chunk metadata, duplicate replacement filters, revision parsing/classification, retrieval preference, and invalid-citation abstention. The five PDFs in `sample-docs/` also extracted locally; these checks are not a substitute for live provider quality evaluation.

## Known limitations and operational cautions

- Ten assignment questions have not been evaluated through the live API. The provided PDFs are in `sample-docs/`, not the configured Supabase bucket or default local document folder.
- The default local embedding model must be downloaded from Hugging Face the first time it is used. Hosted `HF_EMBEDDING_PROVIDER=api` requires valid credentials and may require paid inference credits.
- Supabase behavior was tested with a mocked SDK; no live bucket sync was performed.
- Physical phone/tablet access over Wi-Fi is not available in this environment and requires manual verification.
- Revision parsing/classification is heuristic and cannot establish document-control approval authority. Review ambiguous dates/revisions and do not treat `revision_unknown` as current.
- Similarity thresholds require calibration against the real documents. A threshold can reject valid answers or admit weak matches.
- There is no OCR for scanned PDFs, authentication/authorization, per-user document isolation, user-specific audit trail, or persistent conversation history.
- LLM and embedding providers require external service availability and valid rotated credentials; API connectivity alone does not establish answer correctness.
- Do not expose this development server to the public internet. Restrict LAN access to a trusted network and add authentication before production deployment.

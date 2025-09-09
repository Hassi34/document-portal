# Document Portal

FastAPI-based platform to analyze, compare, and chat with documents using configurable RAG pipelines (FAISS + multi-provider embeddings / LLMs). Configuration is centralized in `configs/config.yaml`. An independent S3 backup micro-service (in `backup_service/`) handles periodic EFS→S3 snapshots outside the API process.

## Table of Contents
1. [Features](#features)
2. [Architecture Overview](#architecture-overview)
3. [Cloud / Infrastructure Architecture](#cloud--infrastructure-architecture)
4. [Project Structure](#project-structure)
5. [Data Directories](#data-directories)
6. [Prerequisites](#prerequisites)
7. [Installation](#installation)
8. [Configuration](#configuration)
9. [Environment Variables](#environment-variables)
10. [Running the App](#running-the-app)
11. [Core Workflows](#core-workflows)
12. [Azure OpenAI Specifics](#azure-openai-specifics)
13. [Backup Service](#backup-service)
14. [Logging & Troubleshooting](#logging--troubleshooting)
15. [Development](#development)
17. [Linting & Formatting](#linting--formatting)
16. [Security](#security)
17. [License](#license)

## Features

- Config-driven paths, API metadata, and AI settings via YAML
- Versioned API (default `/api/v1`) with OpenAPI docs
- Document analysis (PDF extraction), document comparison, and conversational RAG over your uploads
- FAISS vectorstore with sessionized indexing and idempotent updates
- Provider-pluggable LLM and embeddings with Azure OpenAI support
- Structured logging and helpful exception traces

## Architecture Overview

High-level components:
- API Layer (FastAPI): routers for analysis, chat (RAG), compare, health.
- Embeddings & LLM Providers: OpenAI, Google, Groq, Azure OpenAI (selected via env).
- Vector Store: FAISS (session-aware indices for multi-user isolation).
- Semantic Cache (optional): Initialized conditionally for response reuse.
- Storage Layout: Document artifacts and indices under `data/` with per-domain subfolders.
- Backup Service: Decoupled container/task performing incremental or archive uploads to S3 based on a YAML config; does not import app code.

## Cloud / Infrastructure Architecture

This section describes how the application and backup service run inside AWS. Adjust to match ECS vs EC2 deployment model you choose.

### Core AWS Components
| Component | Purpose |
|----------|---------|
| Amazon ECS (Fargate) or EC2 ASG | Runs the FastAPI API container (stateless) |
| Amazon ECS Scheduled Task / EventBridge Scheduler | Triggers the backup container on an interval (e.g. hourly) |
| Amazon S3 (primary bucket) | Stores uploaded user documents & derived artifacts (optional if using only EFS) |
| Amazon S3 (backup bucket or prefix) | Receives incremental / archive backups from `backup_service` |
| Amazon EFS | Persistent shared file system for API (documents, FAISS indices, logs) and backup task read-only mounts |
| Amazon ECR | Container image registry for API & backup images |
| Amazon CloudWatch Logs | Centralized structured logs (API & backup) |
| Amazon EventBridge Scheduler | Cron / rate scheduling of backup ECS task |
| CloudWatch Metrics / Alarms (optional) | Alert on failed task runs or error patterns |
| AWS Secrets Manager / Parameter Store (optional) | Centralized API key storage; injected as JSON bundle (API_KEYS) |
| IAM Roles (Task Execution / Task Role / Scheduler Role) | Principle-of-least-privilege separation |

### High-Level Data Flow
```
User → API (FastAPI / ECS) → (Embeddings + LLM Providers) → FAISS Index (EFS) → Responses

Backup Flow (Out-of-band):
EventBridge Scheduler → ECS Fargate Task (backup image) → Reads /data & /logs (EFS) → S3 (incremental keys) → CloudWatch Logs
```

### Detailed Sequence (Request Path)
1. Client calls `/api/v1/chat/index` with documents.
2. API stores raw files in EFS under `data/document_chat[/session]`.
3. Text chunks created; embeddings generated via configured provider and inserted into FAISS index (on EFS).
4. Query requests load retriever from FAISS; RAG chain composes final LLM response.
5. Structured logs stream to CloudWatch (JSON) for observability.

### Detailed Sequence (Backup Task)
1. EventBridge Scheduler triggers ECS runTask (Fargate) using backup task definition.
2. Container mounts the same EFS access points (read-only for data & logs).
3. `backup_service` loads `backup_config.yaml`, performs HeadBucket check, scans include directories.
4. Incremental: For each changed file, upload with key pattern: `<prefix>/<root_dir_name>/<relative_path>`.
5. Manifest updates (if incremental) persisted back to the container writable layer or EFS (if configured in include list).
6. CloudWatch Logs capture `scan_summary`, `file_uploaded`, and `backup_completed`.

### Example ASCII Diagram
```
     +-----------------------+
   User ---> |  API (FastAPI / ECS)  |---+--> Providers (OpenAI/Groq/...)
     +-----------+-----------+   |
         |               |
       (writes / reads)      |
         v               |
      +-------------+        |
      |    EFS      |<-------+
      | (data,logs, | 
      |  indices)   |
      +------+------+ 
         ^        
      read-only mount|        
         |        
    +------------+------------+
    |  Backup Service (ECS    |---> S3 (backups prefix)
    |  Scheduled Task)        |
    +-------------------------+
```

### IAM / Security Model
| Role | Permissions (Summary) |
|------|------------------------|
| ecsTaskExecutionRole | Pull images from ECR, write logs to CloudWatch |
| apiTaskRole | Access secrets (if used), optional S3 (if direct upload), no backup bucket write |
| backupTaskRole | `s3:ListBucket` + `s3:PutObject` (+ `DeleteObject` if pruning) on backup prefix; read-only EFS |
| schedulerRole | `ecs:RunTask` + `iam:PassRole` (execution + backup task roles) |

Principles:
- Separate task roles prevent unintended S3 write surface from API container.
- Backup task runs with least-privilege S3 access (scoped ARN with prefix condition recommended).
- Use EFS Access Points enforcing directory & POSIX ownership.

### Networking / VPC
- Private subnets for ECS tasks (no public IP) behind an ALB (if external access required) or private API Gateway.
- Security Groups: ALB → API (TCP 8080); API & backup tasks → outbound 443 (egress to model providers & S3).
- VPC Endpoints (optional optimization): `com.amazonaws.<region>.s3` and Secrets Manager for reduced NAT usage.

### Observability
- CloudWatch Logs group per task family (e.g. `/ecs/document-portal-api`, `/ecs/document-portal-backup`).
- Metric Filters (optional): count occurrences of `file_upload_failed`, `periodic_run_failed`.
- Alarms on: backup task failures (ECS Task State Change → STOPPED with non‑zero exit), high 5XX rate on API.

### Deployment Pipeline (Example)
1. Build & tag images: `api` and `backup`.
2. Push to ECR repositories.
3. Register / update ECS task definitions (API + backup).
4. Update Service (API) via blue/green or rolling.
5. EventBridge schedules reference latest `:prod` or versioned task definition for backup runner.

### Alternative (EC2) Note
If not using ECS, you can: 
- Run API with systemd / PM2 on EC2, mounting EFS via NFS.
- Use cron or AWS Systems Manager State Manager with a separate EC2 (or the same instance) invoking `backup_service` container via Docker.
ECS/Fargate is preferred for isolation, auto-scaling, and reduced ops overhead.

### Cost & Efficiency Tips
- Incremental mode drastically reduces S3 PUT & data transfer for large, mostly-static document sets.
- Use lifecycle policies on the backup bucket to transition older snapshots to Glacier Deep Archive.
- Compress (archive mode) periodically (e.g. daily) while running hourly incremental uploads.

### Hardening Checklist
- Add KMS encryption (S3 bucket + EFS).
- Enforce bucket policy: deny unencrypted PUT, restrict source VPC Endpoint if used.
- Enable CloudTrail data events for sensitive prefixes (if compliance needed).
- Add WAF (if exposing API publicly) and set rate limits.

## Project Structure

```
configs/
  config.yaml                 # Core application configuration
backup_service/               # Standalone S3 backup micro-service (own pyproject, Dockerfile)
  backup_config.yaml          # Backup-specific YAML (bucket, include_dirs, interval)
  README.md                   # Service usage & deployment guidance
data/                         # Runtime data (mounted / persistent volumes)
logs/                         # JSON log output (if enabled / mounted)
notebooks/                    # (Optional) experimentation
src/
  api/                        # FastAPI app, routers, templates/static assets
  ai/                         # RAG assembly, ingestion, retrieval helpers
  schemas/                    # Pydantic request/response models
  utils/                      # Logging, config loader, bootstrap, semantic cache
  client/                     # HTML templates + static frontend assets
tests/                        # (Add test modules here)
Dockerfile                    # Main API container
docker-compose.yml            # (If used locally)
pyproject.toml                # Root project dependencies
uv.lock                       # Resolved lock (uv)
README.md                     # (This file)
```

## Data Directories
Configurable (see `configs/config.yaml`):
- `data/document_chat[/<session_id>]` – Source documents for chat RAG.
- `data/faiss_index[/<session_id>]` – FAISS indices (recreated if embedding dims change).
- `data/document_analysis` – Extracted text & artifacts for analysis.
- `data/document_compare` – Input & diff results for comparison workflows.

## Prerequisites

- Python >= 3.10
- FAISS (faiss-cpu in dependencies)
- Node not required (pure Python backend + minimal HTML templates)

## Installation

Using uv or pip (choose one):

```bash
# Clone your repository
git clone https://github.com/hassi34/document-portal.git
cd document-portal

# Option A: uv (recommended if you use uv)
# uv sync

# Option B: pip + venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This project ships a `pyproject.toml` with pinned libraries including:
- fastapi, uvicorn
- langchain, langchain-core, langchain-community, langchain-openai, langchain-google-genai, langchain-groq
- faiss-cpu, pydantic-related deps, pandas, pymupdf, pypdf, python-dotenv

## Configuration

All configuration lives in `configs/config.yaml`.

- API meta, tags, version and server settings: `api.*`
- Data storage directories and supported extensions: `data.*`
- Vector DB (FAISS) name/path: `ai.vector_db.faiss.*`
- Retriever defaults: `ai.retriever.*` (top_k, search_type, chunking)
- Embeddings models per provider: `ai.embedding_model.*`
- LLM models per provider: `ai.llm.*`

Example (excerpt):

```yaml
ai:
  vector_db:
    faiss:
      index_name: "document_portal"
      index_path: "data/faiss_index"

  embedding_model:
    openai:
      model_name: "text-embedding-3-small"
    azure-openai:
      model_name: "text-embedding-3-large"

  retriever:
    top_k: 10
    search_type: similarity
    chunk_size: 1000
    chunk_overlap: 200

  llm:
    openai:
      model_name: "gpt-4o-mini"
      temperature: 0
    azure-openai:
      model_name: "gpt-4o"
      temperature: 0
```

## Environment Variables

Set providers by environment variable. Defaults are OpenAI for both if not set.

- EMBEDDING_PROVIDER: one of `openai`, `google`, `azure-openai`
- LLM_PROVIDER: one of `openai`, `google`, `groq`, `azure-openai`

API keys (validated on startup):

- GOOGLE_API_KEY
- GROQ_API_KEY
- OPENAI_API_KEY
- AZURE_OPENAI_API_KEY

When using Azure OpenAI, also set:

- AZURE_OPENAI_API_INSTANCE_NAME  # e.g., your-resource-name
- AZURE_OPENAI_API_VERSION        # e.g., 2024-10-21
- AZURE_OPENAI_API_DEPLOYMENT_NAME  # chat/completions deployment name (LLM)
- AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME  # embeddings deployment name

Notes:
- For Azure, use the deployment names you created in Azure AI Studio/Portal, not model names.
- The loader will fail fast if required variables are missing.

### Single JSON env for keys (ECS / secrets managers)

You can provide all API keys via one JSON environment variable, whose name is configurable in `configs/config.yaml` at `secrets.aws_secret_manager_keys_env_var` (default: `API_KEYS`). Example value:

```
{
  "OPENAI_API_KEY": "sk-...",
  "GOOGLE_API_KEY": "...",
  "GROQ_API_KEY": "...",
  "AZURE_OPENAI_API_KEY": "...",
  "AZURE_OPENAI_API_INSTANCE_NAME": "my-azure-openai",
  "AZURE_OPENAI_API_VERSION": "2024-10-21",
  "AZURE_OPENAI_API_DEPLOYMENT_NAME": "gpt-4o",
  "AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME": "text-embedding-3-large"
}
```

The app reads this JSON first. If a key is not present there, it falls back to a same-named environment variable (only for keys listed in `secrets.known_keys`).

## Running the App

```bash
# From project root, with your venv activated
uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

Open:
- API docs: http://localhost:8080/api/v1/docs
- Redoc: http://localhost:8080/api/v1/redoc
- Minimal UI: http://localhost:8080/

## Core Workflows

### 1. Chat over documents (RAG)

- POST `/api/v1/chat/index`
  - multipart/form-data with one or more files (pdf, docx, txt)
  - form fields (defaults come from config):
    - chunk_size, chunk_overlap, k, session_id (optional), use_session_dirs (bool)
  - Creates/updates a FAISS index for the session.

- POST `/api/v1/chat/query`
  - form fields: question, session_id (required if sessionized), k, use_session_dirs
  - Loads FAISS retriever and runs the RAG chain.

Behind the scenes:
- Files are stored under `data/document_chat[/<session_id>]`
- FAISS indices live in `data/faiss_index[/<session_id>]` with name from `index_name`
- The index manager is idempotent and avoids re-adding the same rows
- If you switch embedding models, the system auto-detects dimension mismatch and rebuilds the index for that session

### 2. Document analysis

- Endpoints allow uploading a PDF and receiving extracted text/resources.
- Saved under `data/document_analysis[/<session_id>]`

### 3. Document comparison

- Upload a reference and an actual PDF to compute differences.
- Saved under `data/document_compare[/<session_id>]`

## Azure OpenAI Specifics

- Embeddings use `AzureOpenAIEmbeddings` and require the embeddings deployment name via `AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME`.
- Chat LLM uses `AzureChatOpenAI` and requires `AZURE_OPENAI_API_DEPLOYMENT_NAME`.
- Endpoint is constructed from `AZURE_OPENAI_API_INSTANCE_NAME` as `https://<instance>.openai.azure.com/`.

Common pitfalls:
- Using model name instead of deployment name in Azure env vars
- Mismatched API versions (ensure `AZURE_OPENAI_API_VERSION` matches your resource capability)

## Backup Service

The backup micro-service lives in `backup_service/` and runs independently (ECS Scheduled Task / cron-like). Highlights:
- YAML-driven (`backup_service/backup_config.yaml`).
- Modes: incremental per-file (mtime+size diff with manifest) or archive (`tar.gz` snapshot).
- Directory include list (e.g. `/data`, `/logs`) maps to mounted volumes; uploaded S3 keys use relative paths `prefix/<root_name>/...`.
- Early S3 connectivity check (HeadBucket) and JSON structured logs: `scan_summary`, `file_uploaded`, `backup_completed`.
- See `backup_service/README.md` for: EventBridge Scheduler, IAM policies, Docker build, local testing.

Basic one-shot run (from within `backup_service/`):
```bash
uv sync
uv run -m backup_service.cli --config backup_config.yaml --once
```

Override bucket/prefix on the fly:
```bash
uv run -m backup_service.cli --config backup_config.yaml --bucket my-bucket --prefix adhoc/ --once
```

## Logging & Troubleshooting

- Logs are structured and include helpful context; see the `logs/` folder when enabled.
- Typical errors and fixes:
  - 500 during chat index with FAISS dimension mismatch after switching embeddings → auto-reset now handles this; retry indexing.
  - Missing provider keys → set the required environment variables (see above) and restart.
  - OpenAPI not loading → ensure API_VERSION in config is stringifiable; current code handles this.

## Development

- Type annotations and docstrings are being added across modules; contributions welcome.
- Keep changes config-driven; avoid reintroducing hardcoded paths.
- When changing public behavior, add/update tests and docs.

## Linting & Formatting

Ruff is used for both lint rules and code formatting.

Format the code (applies changes):

```bash
uv run ruff format
```

Run lint check (no changes, CI will fail on violations):

```bash
uv run ruff check .
```

Typical CI pipeline runs `ruff check .` and `ruff format --check .` (or equivalently relies on `check` plus a separate formatting check). If you want to ensure a clean commit locally, run both commands before pushing.

Optional pre-commit hook (create `.git/hooks/pre-commit`):

```bash
#!/usr/bin/env bash
uv run ruff format
uv run ruff check . || exit 1
```

Make it executable:

```bash
chmod +x .git/hooks/pre-commit
```

## Security

- Do not commit secrets. Prefer environment variables or secret managers.
- The debug endpoint is gated behind a config flag.

## License

This project is licensed under the terms of the LICENSE file included in the repository.

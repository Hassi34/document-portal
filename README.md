# Document Portal

A FastAPI-powered application to analyze, compare, and chat with documents using configurable RAG pipelines and a FAISS vector store. The app is fully config-driven via `configs/config.yaml` and supports multiple model providers including OpenAI, Google, Groq, and Azure OpenAI.

## Features

- Config-driven paths, API metadata, and AI settings via YAML
- Versioned API (default `/api/v1`) with OpenAPI docs
- Document analysis (PDF extraction), document comparison, and conversational RAG over your uploads
- FAISS vectorstore with sessionized indexing and idempotent updates
- Provider-pluggable LLM and embeddings with Azure OpenAI support
- Structured logging and helpful exception traces

## Project Structure

```
configs/
  config.yaml                 # Central configuration
src/
  api/                        # FastAPI app, routers, templates/static
  ai/                         # Ingestion, chat RAG, compare utilities
  schemas/                    # Pydantic models (request/response)
  utils/                      # Config loader, logging, model loader, file ops
  client/                     # Templates and static files for the minimal UI
```

Key runtime directories (configurable in YAML):
- `data/document_chat`: uploaded files for chat sessions
- `data/faiss_index`: FAISS indices (sessionized when enabled)
- `data/document_analysis`, `data/document_compare`: analysis/compare workspaces

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

## Running the app

```bash
# From project root, with your venv activated
uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

Open:
- API docs: http://localhost:8080/api/v1/docs
- Redoc: http://localhost:8080/api/v1/redoc
- Minimal UI: http://localhost:8080/

## Workflows

### 1) Chat over documents (RAG)

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

### 2) Document analysis

- Endpoints allow uploading a PDF and receiving extracted text/resources.
- Saved under `data/document_analysis[/<session_id>]`

### 3) Document comparison

- Upload a reference and an actual PDF to compute differences.
- Saved under `data/document_compare[/<session_id>]`

## Azure OpenAI specifics

- Embeddings use `AzureOpenAIEmbeddings` and require the embeddings deployment name via `AZURE_OPENAI_API_EMBEDDING_DEPLOYMENT_NAME`.
- Chat LLM uses `AzureChatOpenAI` and requires `AZURE_OPENAI_API_DEPLOYMENT_NAME`.
- Endpoint is constructed from `AZURE_OPENAI_API_INSTANCE_NAME` as `https://<instance>.openai.azure.com/`.

Common pitfalls:
- Using model name instead of deployment name in Azure env vars
- Mismatched API versions (ensure `AZURE_OPENAI_API_VERSION` matches your resource capability)

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

## Security

- Do not commit secrets. Prefer environment variables or secret managers.
- The debug endpoint is gated behind a config flag.

## License

This project is licensed under the terms of the LICENSE file included in the repository.

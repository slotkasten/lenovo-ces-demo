# Architecture

## Components

| Component | Image | Role |
|---|---|---|
| Web app | `ghcr.io/kastendevhub/northwind-bank` | FastAPI + Uvicorn; serves the portal and `/api/*` |
| Database | `pgvector/pgvector:0.8.7-pg17` | PostgreSQL 17 with the `vector` extension |
| LLM (optional) | `ollama/ollama` | CPU inference for a small open-weight model |
| Seed | same as web app | Helm post-install/upgrade Job: schema + data |

## Data model (one PostgreSQL database)

**Bank data** (targets of the "drop tables" attack): `customers`, `accounts`, `transactions`.
**Assistant data**: `knowledge_documents` (with a `vector(384)` embedding column), `chat_sessions`,
`chat_messages`, `audit_log`.

Keeping the relational data and the vector store in **one database** is deliberate: a single
application-consistent Kasten snapshot protects the bank's records and the AI knowledge base
together, and restores them to the same point in time.

## Request flow for `/api/chat`

1. `intents.detect_intent()` classifies the message with ordered regexes. **Injection patterns are
   matched first** so the easter eggs win over benign routing.
2. Injection → `scenarios.run()` executes (or, with guardrails on, refuses) the scripted scenario
   against the demo's own synthetic data, and writes an `audit_log` row.
3. Banking intent → a handler queries PostgreSQL and returns masked results.
4. Otherwise → **RAG**: embed the question, `ORDER BY embedding <=> query` (pgvector cosine
   distance) to retrieve the top-k documents, and either ask the LLM to answer from that context or,
   when the LLM is off/unavailable, return the most relevant document verbatim.

## Embeddings

`EMBED_PROVIDER=hashing` (default) is a dependency-free hashing-trick bag-of-words embedder
(unigrams + bigrams, sub-linear TF, L2-normalised, 384-dim). It needs no model and runs on any CPU,
which is why the demo deploys without a GPU. `EMBED_PROVIDER=ollama` swaps in a real embedding model
(`all-minilm`) with the same vector dimension, so the pgvector column shape never changes.

## Degradation behaviour

- **No LLM / LLM offline** → templated answers from retrieved documents (`llm.generate()` returns
  `None`, the handler falls back).
- **Vector search fails** → keyword `ILIKE` fallback over the knowledge base.
- **Bank tables dropped** → handlers raise `MissingData`; the UI shows a "restore from Kasten"
  banner instead of crashing. This is what makes the post-attack state legible on stage.

## Security model (demo)

The app masks PII by default and scopes every customer query to the current session's persona. The
prompt-injection scenarios deliberately bypass those controls to show the risk; `GUARDRAILS_ENABLED`
flips the app to detect-and-refuse. All of it operates only on synthetic seed data.

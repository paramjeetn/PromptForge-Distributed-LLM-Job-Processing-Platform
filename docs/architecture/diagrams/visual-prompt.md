# PromptForge — Visual Architecture Diagram Prompt

Create a clean, modern software architecture diagram for **PromptForge**, a distributed LLM batch job processing platform. White background, colorful flat icons, rounded white cards with colored accent borders, thin directional arrows, generous whitespace. Left to right layout. Feels like a premium SaaS landing page.

---

### Components (left to right)

| Component | Color | Hint |
|---|---|---|
| User / Client | Grey | `POST /v1/jobs/init` |
| API Service | Blue | `Cloud Run · Secret Manager auth · Signed URLs` |
| Secret Manager | Red | `API key store · provider LLM keys · fetched at pod startup` |
| Input Bucket | Sky Blue | `prompts.jsonl · Eventarc watched · deleted on completion` |
| Eventarc | Violet | `OBJECT_FINALIZE · zero polling` |
| Job Launcher | Indigo | `Cloud Run · Pydantic validation · per-client job queue` |
| **Execution Engine** | Purple (largest card) | `GKE Job · LiteLLM · TCP-inspired rate learning · byte-range streaming · checkpoint recovery` |
| LiteLLM | Lime | `Provider abstraction · OpenAI · Gemini` |
| Output Bucket | Teal | `results_part_NNN · results_final · errors · state.json` |
| LLM Providers | Green | `OpenAI · Gemini` |
| Firestore | Orange | `job lifecycle · PENDING queue · client_id scoped` |
| Axiom | Amber | `OTel traces · logs · metrics · per-prompt spans` |
| Sentry | Pink | `error tracking · unhandled exceptions · stack traces` |
| Pulumi | Yellow | `infrastructure as code · GCS · GKE · Firestore · IAM` |

---

### Flow

```
User              → API                "POST /v1/jobs/init"
API               → Secret Manager     "verify API key → resolve client_id"
Secret Manager    → API                "client_id resolved"
API               → Firestore          "create job record (status = AWAITING_UPLOAD)"
API               → User               "job_id + signed GCS upload URL"
User              → Input Bucket       "PUT prompts.jsonl directly (no proxy)"
Input Bucket      → Eventarc           "OBJECT_FINALIZE"
Eventarc          → Job Launcher       "CloudEvent"
Job Launcher      → Firestore          "read job metadata"
Job Launcher      → Firestore          "status = QUEUED or PENDING"
Job Launcher      → Execution Engine   "create K8s Job (env vars injected)"
Execution Engine  → Secret Manager     "fetch provider api_key at startup"
Execution Engine  → Input Bucket       "byte-range stream read (O(1) memory)"
Execution Engine  → LiteLLM            "prompt dispatch"
LiteLLM           → LLM Providers      "rate-controlled HTTPS (OpenAI · Gemini)"
LLM Providers     → LiteLLM            "async responses"
LiteLLM           → Execution Engine   "normalized response"
Execution Engine  → Output Bucket      "batched results + state.json checkpoint"
Execution Engine  → Firestore          "status updates (PROCESSING · COMPLETED)"
Execution Engine  → Axiom              "OTel metrics · traces · structured logs" (dashed)
Execution Engine  → Sentry             "unhandled exceptions" (dashed)
Output Bucket     → API                "signed download URLs (1hr expiry)"
API               → User               "GET /results → signed result file URLs"
Pulumi            → GCP                "provisions all infrastructure" (dashed)
```

---

### Callout badges near Execution Engine

- `Event-driven · zero polling anywhere`
- `Self-healing · at-least-once checkpoint recovery`
- `Scales to 10M+ prompts · O(1) memory`
- `TCP-inspired rate learning · slow start + congestion avoidance`
- `LiteLLM · any provider, one interface`

---

### Callout badges near Observability cluster (Axiom + Sentry)

- `OTel-native · one env var to connect`
- `Per-prompt trace spans · full token + latency data`
- `prompt_id never a metric label · cardinality safe`

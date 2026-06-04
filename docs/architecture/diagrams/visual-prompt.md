# PromptForge — Visual Architecture Diagram Prompt

Create a clean, modern software architecture diagram for **PromptForge**, a distributed LLM batch job processing platform. White background, colorful flat icons, rounded white cards with colored accent borders, thin directional arrows, generous whitespace. Left to right layout. Feels like a premium SaaS landing page.

---

### Components (left to right)

| Component | Color | Hint |
|---|---|---|
| User / Client | Grey | `POST /v1/jobs/init` |
| API Service | Blue | `Cloud Run · Signed URLs · api_key_hash` |
| Input Bucket | Sky Blue | `prompts.jsonl · Eventarc watched` |
| Eventarc | Violet | `OBJECT_FINALIZE · zero polling` |
| Job Launcher | Indigo | `Cloud Run · Pydantic validation · job queue` |
| **Execution Engine** | Purple (largest card) | `GKE Job · TCP-inspired rate learning · byte-range streaming · checkpoint recovery` |
| Output Bucket | Teal | `results · errors · state.json` |
| LLM Providers | Green | `OpenAI · Anthropic · Gemini` |
| Observability | Amber | `OTel · traces per prompt · metrics` |
| Firestore | Orange | `job lifecycle · PENDING queue` |

---

### Flow

```
User          → API             "submit job"
User          → Input Bucket    "upload prompts.jsonl directly"
API           → Firestore       "create job record"
API           → User            "job_id + signed URL"
Input Bucket  → Eventarc        "OBJECT_FINALIZE"
Eventarc      → Job Launcher    "CloudEvent"
Job Launcher  → Firestore       "status = QUEUED or PENDING"
Job Launcher  → Execution Engine "create K8s Job"
Execution Engine → Input Bucket  "stream read byte-range"
Execution Engine → LLM Providers "rate-controlled HTTPS dispatch"
LLM Providers → Execution Engine "async responses"
Execution Engine → Output Bucket "batched results + checkpoint"
Execution Engine → Firestore     "status updates"
Execution Engine → Observability "metrics · traces · logs" (dashed)
Output Bucket → API              "signed download URLs"
API           → User             "results ready"
```

---

### Callout badges near Execution Engine

- `Event-driven · zero polling`
- `Self-healing · checkpoint recovery`
- `Scales to 10M+ prompts · O(1) memory`

<p align="center">
  <img src="docs/architecture/diagrams/Architecture.png" alt="PromptForge Architecture" width="900"/>
</p>

<h1 align="center">PromptForge</h1>

<p align="center">
  <strong>Distributed LLM batch processing — at any scale, without babysitting.</strong><br/>
  Upload a file of prompts. Every result comes back. Reliably.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Google%20Cloud-4285F4?style=flat-square&logo=googlecloud&logoColor=white" alt="Platform"/>
  <img src="https://img.shields.io/badge/runtime-Python%20%2B%20asyncio-3776AB?style=flat-square&logo=python&logoColor=white" alt="Runtime"/>
  <img src="https://img.shields.io/badge/infra-GKE%20%2B%20Cloud%20Run-34A853?style=flat-square" alt="Infra"/>
  <img src="https://img.shields.io/badge/observability-OpenTelemetry-F5A623?style=flat-square&logo=opentelemetry&logoColor=white" alt="Observability"/>
  <img src="https://img.shields.io/badge/status-live-brightgreen?style=flat-square" alt="Status"/>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"/>
</p>

---

## The Problem

Running thousands of prompts against an LLM provider is not just an API call problem. It is a systems problem.

Providers impose rate limits. Pods crash mid-job. Files are too large to hold in memory. You need to know exactly which prompts succeeded, which failed, and why — at a per-prompt level — without having to watch it happen.

Most teams write fragile scripts that break at scale. PromptForge is a purpose-built platform that handles all of it: rate learning, failure recovery, checkpointing, result reconciliation, and observability — without any manual configuration.

---

## How It Works

The system is organized into three services. Nothing polls — every state transition is triggered by an event.

### Ingestion — Cloud Run API

A client calls `POST /v1/jobs/init` with a provider, model, and optional rate limits. The API creates a job record in Firestore and returns a signed GCS upload URL. The client writes the prompt file directly to Google Cloud Storage — no proxy, no bottleneck. The API's job ends here.

### Orchestration — Cloud Run Launcher

When the upload lands in GCS, an Eventarc `OBJECT_FINALIZE` event fires. The Launcher validates every line of the JSONL file (invalid lines go to `errors.jsonl`, the job continues), updates the job state in Firestore, and creates a GKE Kubernetes Job. The Launcher exits as soon as the pod is scheduled. It has no ongoing responsibility.

Per-client concurrency is enforced here: if a pod is already running for a client, the new job is queued as `PENDING`. The previous pod promotes it on completion.

### Execution — GKE Pod

One pod per job. It runs four concurrent async loops for its entire lifetime:

- **Dispatch loop** — spaces prompt requests by the learned effective RPM
- **Response handler** — processes LLM responses as they arrive, out of order
- **Result buffer** — accumulates responses and flushes to GCS in batches (500 responses, 50 MB, or 30s — whichever comes first)
- **Checkpoint writer** — writes `state.json` to GCS every 30 seconds

The pod talks to the LLM provider directly over HTTPS via [LiteLLM](https://github.com/BerriAI/litellm). No intermediate queue, no worker fleet — the async event loop is the scheduler.

---

## Rate Learning

PromptForge never trusts static RPM/TPM values. It treats them as upper bounds and discovers real limits at runtime using a TCP-inspired algorithm.

**Slow start** — before seeing any 429, the dispatcher multiplies its RPM target by 1.5 every 30 seconds. Starting from 10 RPM: `10 → 15 → 22 → 33 → 49 → 73 → ...`

**Congestion avoidance** — after a 429, it backs off to 75% of the current target, waits 60 seconds, then increments by +1 every 30 seconds until it hits a ceiling again.

**TPM constraint** — effective RPM is also bounded by token budget: `effective_rpm = min(rpm_target, tpm_limit / p95_output_tokens)`. The P95 token estimate updates after every successful response. As responses grow longer, throughput drops automatically.

This means a job submitted with `rpm: 500` won't saturate at 500 if the provider's real limit is lower. It will converge to whatever the provider actually allows.

---

## Reliability

**Crash recovery** — on pod restart, the execution service reads `state.json` from GCS and resumes from the last checkpoint offset. At-least-once delivery: prompts near the checkpoint boundary may be re-dispatched, but none are silently dropped.

**Reconciliation** — job completion includes a bitset pass across all result and error files. Every `prompt_id` is accounted for. If any ID is missing, it is logged explicitly — there is no silent data loss.

**Duplicate Eventarc events** — the Launcher checks the current Firestore job status before acting. Duplicate `OBJECT_FINALIZE` events (common in GCS) are safely idempotent.

---

## Observability

All three services are instrumented with **OpenTelemetry**, exporting to Axiom over OTLP. Errors are captured to Sentry.

Every prompt dispatch produces a child span carrying token counts, latency, attempt number, and error code. Key rate learning gauges are emitted in real time:

| Metric | What it tells you |
|---|---|
| `rate.rpm_target` | Current learned RPM target |
| `rate.effective_rpm` | RPM after TPM constraint |
| `rate.p95_tokens` | Rolling P95 completion token estimate |
| `rate.429_events` | Rate limit events since job start |
| `prompts.completed` | Successfully processed |
| `prompts.failed` | Permanently failed after max retries |
| `llm.latency_ms` | Per-request provider latency histogram |
| `queue.retry_depth` | Prompts currently awaiting retry |

`prompt_id` is never used as a metric label — cardinality is kept flat regardless of job size.

---

## API

Authentication uses `X-API-Key` on every request. Keys are stored in GCP Secret Manager and cached in-process with a 5-minute TTL.

| Endpoint | Description |
|---|---|
| `POST /v1/jobs/init` | Create a job. Returns `job_id` and a signed GCS upload URL. |
| `GET /v1/jobs/{job_id}` | Poll status and progress counts. |
| `GET /v1/jobs/{job_id}/results` | Returns signed download URLs for result JSONL files. Available once `COMPLETED`. |
| `DELETE /v1/jobs/{job_id}` | Cancel. In-flight prompts complete naturally; pending ones are abandoned. |

**Input format:** newline-delimited JSON, one `{"prompt_id": int, "prompt": string}` per line. `prompt_id` must be unique within the job — it is used for deduplication, retry tracking, and reconciliation.

**Output format:** `results_final.jsonl` (or `results_part_NNN.jsonl` for large jobs) — one record per prompt carrying `response`, `tokens`, `latency_ms`, and `attempt`. Permanently failed prompts go to `errors.jsonl` with error codes and attempt history.

---

## Stack

| Layer | Technology |
|---|---|
| API service | Python / FastAPI on Cloud Run |
| Job launcher | Python / FastAPI on Cloud Run (ephemeral) |
| Execution engine | Python / asyncio on GKE (one pod per job) |
| LLM routing | LiteLLM |
| Event routing | GCP Eventarc (`OBJECT_FINALIZE`) |
| Job state | Firestore |
| Object storage | Google Cloud Storage |
| Secrets | GCP Secret Manager |
| Observability | OpenTelemetry → Axiom + Sentry |
| Infrastructure | Pulumi (TypeScript) |
| Supported providers | OpenAI, Gemini |

---

## Design Principles

**Event-driven end to end.** No component polls. Every state transition is triggered by an event: HTTP response, GCS object finalize, async task completion.

**One pod, one job.** Each job runs in a fully isolated GKE pod. A failure or rate event in one job cannot affect another. Accounting is exact.

**Memory-constant.** Prompt files are never loaded in full. The execution pod reads one line at a time via GCS byte-range streaming. A 10M-prompt job uses the same memory as a 100-prompt job.

**At-least-once over at-most-once.** On recovery, prompts near the checkpoint boundary may be re-sent. This is deliberate — duplicate work is preferable to silent data loss.

**No per-response writes.** Results buffer and flush in batches. One million prompts produce roughly 2,000 GCS writes, not one million.

**Reconciliation as a completion step.** Job completion is not just a status flip. A full bitset pass verifies every `prompt_id` was accounted for before the job is marked `COMPLETED`.

---

## Repository Layout

```
infra/              Pulumi TypeScript — all GCP resources
services/
  api/              Cloud Run API (FastAPI)
  launcher/         Cloud Run Job Launcher (FastAPI + CloudEvents)
  execution/        GKE Execution Pod (asyncio)
    loops/          dispatch, response handler, buffer, checkpoint
    rate/           rate learning controller
shared/             Models, Firestore helpers, GCS helpers, observability bootstrap
tests/
  unit/             Isolated unit tests per service
  integration/      Tests against real GCP resources
  e2e/              Full end-to-end flow against live deployment
scripts/
  debug/            GKE log inspection, stale job repair
  deploy/           Image build and push helpers
docs/               Architecture, API spec, design decisions
```

---

## Contributing

Open an issue before submitting a PR for anything beyond a small fix — it helps align on direction before work begins.

1. Fork the repository
2. Create a feature branch
3. Commit with clear messages
4. Open a pull request against `main`

---

## License

MIT. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <em>Built for failure, scale, and visibility — from day one.</em>
</p>

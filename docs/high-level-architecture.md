# PromptForge

### Distributed LLM Batch Job Processing — At Any Scale

---

## The Problem

Running thousands of prompts against an LLM provider is not just an API call problem. It is a systems problem.

Providers impose rate limits. Requests fail. Pods crash. Files are too large to hold in memory. You need to know exactly which prompts succeeded, which failed, and why — at a per-prompt level. And you need to do all of this without babysitting the process.

Most teams either build fragile scripts that break at scale, or pay for expensive managed pipelines that give them no visibility. PromptForge is neither.

---

## What PromptForge Does

You upload a file of prompts. We process all of them — reliably, at whatever scale you need — and give you the results back.

That's it from the outside. Everything in between is what we built.

---

## What We Handle

**Rate limits** — We do not ask you to figure out the right RPM or TPM. We start slow, learn the provider's real limits in real time using a TCP-inspired slow-start and congestion avoidance algorithm, and continuously adapt. No manual tuning.

**Failures** — Every failed prompt is retried. Every provider error is categorised. Prompts that exhaust retries are written to an error log with full context — error code, attempt count, timestamp. Nothing is silently dropped.

**Scale** — Prompts are streamed line by line from storage. Memory usage is constant whether you send 100 prompts or 10 million. We never load your file into memory.

**Recovery** — Every 30 seconds, execution state is checkpointed to storage. If a pod crashes, Kubernetes restarts it and it resumes from the last checkpoint. At-least-once delivery is guaranteed.

**Correctness** — On completion, we run a bitset reconciliation pass across all results and error files. Any prompt that fell through the cracks is logged by ID to our observability stack. You get a complete picture of what happened.

**Observability** — Every prompt dispatch is a traced span with full token counts, latency, retry history, and error codes. Rate learning state — RPM target, P95 token estimate, 429 events — is emitted as real-time metrics. You can pinpoint exactly what happened to prompt #47,832 on attempt 2.

**Job queuing** — Multiple jobs from the same client run sequentially. No resource contention, no prompt collision. The queue is managed automatically.

---

## How It Works

```
You upload prompts.jsonl
        ↓
GCS fires a storage event
        ↓
Job Launcher validates every line, queues the job
        ↓
Execution Pod starts — one pod, one job, fully isolated
        ↓
Prompts stream out, LLM calls go in, responses buffer back
        ↓
Results land in storage, checkpoints keep state safe
        ↓
Job completes, reconciliation confirms nothing was lost
        ↓
You download results via signed URL
```

The system is fully event-driven. Nothing polls. Every transition is triggered by an event.

---

## The Architecture

Three layers. Each does one thing.

**Ingestion** — A Cloud Run API accepts your job request and returns a signed upload URL. You write directly to storage. No proxy, no bottleneck.

**Orchestration** — A Cloud Run Job Launcher catches the storage event, validates your file, and creates a dedicated Kubernetes Job. It starts in milliseconds and exits the moment the pod is running.

**Execution** — A GKE pod takes over entirely. It runs four concurrent loops: a dispatch loop that spaces requests by learned rate limits, a response handler that processes results asynchronously, a result buffer that batches storage writes, and a checkpoint writer that persists state every 30 seconds. All LLM calls go through LiteLLM — a self-hosted provider abstraction layer that normalises OpenAI, Anthropic, Gemini, and Mistral into a single interface. Adding a new provider requires no code change. One pod per job. Isolated, self-healing, accountable.

---

## What Makes It Different

Most batch systems treat rate limits as a configuration problem. We treat them as a learning problem — the system discovers real limits the same way TCP discovers network capacity.

Most batch systems write results one by one. We buffer and flush in batches — 1 million prompts produce roughly 2,000 storage writes instead of 1 million.

Most batch systems give you a success/failure count. We give you a trace per prompt, a metric per second, and a reconciled list of every ID that was processed, failed, or lost.

---

## Built On

**Compute & Storage** — Google Cloud Platform: Cloud Run, GKE, GCS, Firestore, Eventarc.

**Provider Abstraction** — LiteLLM. One interface to every LLM provider. Self-hosted, no data leaves GCP.

**API Key Management** — Unkey. Key issuance, validation, revocation, and per-key rate limiting without custom auth code.

**Infrastructure** — Pulumi. Infrastructure as TypeScript — GCS, GKE, Firestore, IAM, all versioned and repeatable.

**Observability** — Axiom for logs, traces, and metrics via OpenTelemetry. Sentry for error tracking. Better Stack for uptime monitoring and status page.

**Execution Engine** — Python with asyncio. Kubernetes Jobs with automatic restart and checkpoint-based recovery.

---

*PromptForge is a distributed system designed from day one for failure, scale, and visibility.*

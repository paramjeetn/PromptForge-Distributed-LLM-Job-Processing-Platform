# PromptForge — Build vs Buy Strategy

The rule: **build only what is your IP**. Everything else is commodity — buy it, don't maintain it.

Your IP in this system is:
- The streaming prompt dispatch at scale
- The intelligent rate learning engine (slow start + congestion avoidance)
- The job lifecycle state machine
- The checkpoint/recovery logic
- The client-facing API surface

Everything else has a better, maintained, production-hardened version already available.

---

## LiteLLM — Provider Unification Only

Without LiteLLM, every new provider your users bring means writing and maintaining a separate SDK integration:

```python
# What you'd maintain without LiteLLM:
if provider == "openai":
    client = openai.AsyncOpenAI(api_key=key)
    response = await client.chat.completions.create(model=model, messages=messages)
elif provider == "anthropic":
    client = anthropic.AsyncAnthropic(api_key=key)
    response = await client.messages.create(model=model, messages=messages)
elif provider == "gemini":
    # completely different SDK, auth model, response shape...
elif provider == "mistral":
    # yet another one...
```

LiteLLM collapses this to a single call regardless of provider:

```python
# With LiteLLM — one interface, every provider, same response shape:
response = await litellm.acompletion(
    model="openai/gpt-4o",       # or "anthropic/claude-opus-4-6"
    messages=messages,            # or "gemini/gemini-2.5-pro", "mistral/..."
    api_key=user_api_key
)
# response.choices[0].message.content  — always the same shape
# response.usage.prompt_tokens         — always the same shape
```

```
Before:  Execution Engine → OpenAI SDK / Anthropic SDK / Gemini SDK / ...
After:   Execution Engine → LiteLLM → OpenAI / Anthropic / Gemini / ...
```

**What LiteLLM is used for:** provider abstraction and response shape normalization.

**What LiteLLM is NOT used for:** rate limiting, backoff, or throughput management.

LiteLLM's built-in retry/backoff is disabled. Our execution engine (Phase 6) owns all
rate decisions. LiteLLM does not know about our rpm_target, our slow start state, or
our 429 response handling — it just translates the call and returns the raw response.

Adding a new provider in the future: one line in config. No code change in the execution engine.

Self-host as a lightweight sidecar in the GKE pod or a shared deployment. Open source, free, no data leaves your infrastructure.

---

## Phase-by-Phase Swap Analysis

### Phase 1 — Infrastructure

| What we planned | Swap | Why |
|---|---|---|
| Hand-written Terraform / manual GCP setup | **Pulumi** (TypeScript) | Infrastructure as real code. Stacks, previews, state management. Much better DX than Terraform HCL. |
| Manual IAM role configuration | Pulumi handles it | Codified, reviewable, repeatable |

No behavioral change. Just much faster provisioning and a repo that documents your infra exactly.

---

### Phase 2 — Job Init API (API Keys)

| What we planned | Swap | Why |
|---|---|---|
| Roll your own API key generation + SHA256 hashing + storage | **Unkey** | Purpose-built for this. Key generation, hashing, per-key rate limits, revocation, usage analytics — all via one API call. Free tier covers early stage. |

```python
# Before: custom key logic, storage, validation middleware
api_key = generate_key()
store_hash(SHA256(api_key))
# ... validation on every request

# After:
key = unkey.keys.create(api_id="promptforge", meta={"user_id": uid})
# On each request:
result = unkey.keys.verify(key=request.api_key)
```

You get per-key rate limiting, expiry, usage dashboards, and revocation for free.
The `api_key_hash` column in Firestore becomes `unkey_key_id`.

---

### Phase 3 — Upload Pipeline

No swap. Eventarc is already GCP-native, free, and exactly the right tool. JSONL validation
is 10 lines of code. Nothing to outsource here.

---

### Phase 4 + 5 — Execution Engine + Result Buffer

No swap. This is core IP.

The streaming dispatch, in-memory state, byte-range GCS reads, and result buffering are what
make PromptForge work at scale. LiteLLM handles the LLM call. Your engine handles everything around it.

---

### Phase 6 — Rate Learning

No swap. This is core IP.

LiteLLM does reactive backoff (hit 429 → wait → retry). That is not sufficient for a system
that processes millions of prompts and needs to maximize throughput without thrashing the provider.

The slow start + congestion avoidance engine is what makes PromptForge meaningfully better
than a naive retry loop. It learns the provider's actual limit without being told, converges
to near-maximum throughput, and handles TPM ceilings automatically via P95 token estimation.

This is your competitive advantage. Build it, own it.

---

### Phase 7 — Checkpointing & Recovery

No swap. The `state.json` checkpoint, SIGTERM handler, and at-least-once recovery guarantee
are ~100 lines of intentional code. Nothing to outsource.

---

### Phase 8 — Job Completion & Reconciliation

No swap. The bitset reconciliation and PENDING → QUEUED job chain are core lifecycle logic.

---

### Phase 9 — Status & Results API

| What we planned | Swap | Why |
|---|---|---|
| Roll your own progress calculation | Keep — it's just Firestore reads | |
| Roll your own signed URL generation | Keep — GCS SDK, one line | |
| Manual API docs | **Scalar** or **Mintlify** | Drop in an OpenAPI spec → hosted docs that look like Stripe's. Zero maintenance. |

---

### Phase 10 — Observability

Running your own Grafana + Prometheus + Tempo + Loki stack is weeks of work and ongoing
maintenance. The SaaS options cost less than one hour of your time per month.

#### Logs + Traces: Axiom

```
Free tier: 500 GB/month ingest, 30-day retention
OTel native: one env var, no code change
Query language: SQL-like, fast, excellent for high-cardinality fields
Dashboards: genuinely beautiful, shareable
```

```bash
# Before: deploy Grafana, Loki, Tempo, configure datasources, build dashboards
# After:
OTEL_EXPORTER_OTLP_ENDPOINT=https://api.axiom.co
AXIOM_TOKEN=your_token
# Done. Every OTel span and log line appears in Axiom automatically.
```

#### Errors: Sentry

```
Free tier: 5,000 errors/month
Automatic: unhandled exceptions captured with full stack trace
Groups: deduplicates the same error across 1,000 pods into one issue
Alerts: Slack / email when a new error type appears
```

```python
import sentry_sdk
sentry_sdk.init(dsn="...", traces_sample_rate=0.1)
# That's it.
```

Sentry catches what Axiom misses — the crashes you didn't instrument.

#### Uptime + Status Page: Better Stack

```
Free tier: 10 monitors, 3-min check interval
Monitors your API endpoints from outside GCP
Pages you when Cloud Run is down
Public status page your users can bookmark
```

---

## Final Phase Summary

| Phase | Build or Buy | Tooling |
|---|---|---|
| 1 — Infrastructure | Build (IaC) | Pulumi |
| 2 — Job Init API | Build + Buy (keys) | Your code + Unkey |
| 3 — Upload Pipeline | Build | Eventarc + Cloud Run |
| 4 — Execution Engine | Build | Your IP |
| 5 — Result Buffer | Build | Your IP |
| 6 — Rate Learning | Build | Your IP — LiteLLM does NOT own this |
| 7 — Checkpointing | Build | Your IP |
| 8 — Completion | Build | Your IP |
| 9 — Status API | Build + Buy (docs) | Your code + Scalar |
| 10 — Observability | Buy | Axiom + Sentry + Better Stack |

LiteLLM sits inside Phase 4 as a call adapter — not a phase of its own.

---

## What This Changes

### Provider support goes from months to minutes

Adding Anthropic, Gemini, or Mistral support is a LiteLLM config line, not a code change
in the execution engine. The rate learning, checkpointing, and dispatch logic are completely
provider-agnostic.

### Phase 10 goes from 2 weeks to 2 hours

```
Axiom:        1 env var
Sentry:       3 lines of code
Better Stack: configure 3 URL monitors in a UI
```

Better observability than a team of 10 could build in a month.

### API key management is no longer a security surface

Rolling your own key hashing and validation is where bugs happen.
Unkey has been audited, handles revocation, and has a management UI without touching your database.

---

## What NOT to Outsource

- **Your execution engine** — this is the product
- **Your rate learning** — this is the competitive moat
- **Your data** — prompts and results stay in your GCS bucket, always
- **Your Firestore state** — this is your system of record

The only things going to third parties:
- API key management (Unkey) — no prompt data touches it
- Error reports (Sentry) — `before_send` strips prompt content
- Logs/traces (Axiom) — OTel SDK strips prompt content before export
- Uptime checks (Better Stack) — just pings your endpoint

No user prompt data leaves your GCP project.

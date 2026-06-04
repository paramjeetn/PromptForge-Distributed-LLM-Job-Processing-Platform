# PromptForge Scheduler Architecture (Execution Phase)

# Overview

Once a job reaches `QUEUED`, a dedicated scheduler pod is assigned to that job.

The scheduler is responsible for:

* Reading prompts from GCS
* Learning RPM limits
* Learning TPM limits
* Dispatching requests
* Tracking retries
* Collecting responses
* Buffering results in memory
* Persisting checkpoints
* Recovering from failures

The scheduler is the active execution engine.

Firestore is no longer involved except for job lifecycle state.

---

# Initial State

The scheduler receives:

```json
{
  "job_id": "job_123",
  "provider": "openai",
  "model": "gpt-4o",

  "rpm_limit": 500,
  "tpm_limit": 100000,

  "prompts_path":
    "jobs/job_123/prompts.jsonl"
}
```

Scheduler creates:

```python
offset = 0

completed = 0
failed = 0
running = 0

retry_queue = []

result_buffer = []

rpm_target = 10
tpm_target = 250

p95_tokens = 1000

dispatch_enabled = True

learning_mode = "slow_start"
```

Everything above exists only in memory.

---

# Input File Structure

```text
jobs/job_123/prompts.jsonl
```

Example:

```json
{"prompt_id":1,"prompt":"..."}
{"prompt_id":2,"prompt":"..."}
{"prompt_id":3,"prompt":"..."}
```

Every prompt receives a unique prompt_id.

This is important for recovery.

---

# Scheduler Components

The scheduler internally consists of four loops.

```text
┌─────────────────────┐
│ Dispatch Loop       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Response Handler    │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Result Buffer       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Checkpoint Writer   │
└─────────────────────┘
```

All run simultaneously.

---

# Dispatch Loop

The dispatch loop continuously decides:

```text
Can I send another request?
```

---

## Effective Rate Calculation

Before dispatching:

```python
effective_rpm = min(
    rpm_target,
    tpm_target / p95_tokens
)
```

Example:

```text
rpm_target = 500

tpm_target = 100000

p95_tokens = 1000
```

Result:

```text
100 requests/minute
```

because TPM becomes the bottleneck.

---

## Request Spacing

Scheduler does NOT send:

```text
100 requests
wait 1 minute
```

Instead:

```text
100 / 60

≈ 1.66 req/sec
```

Requests leave continuously.

Example:

```text
t=0     send request
t=0.6   send request
t=1.2   send request
t=1.8   send request
...
```

This creates a smooth flow.

---

# Reading Prompts

Before every dispatch:

```python
if retry_queue:
    prompt = retry_queue.pop()
else:
    prompt = next_prompt(offset)
```

Retry queue always has priority.

---

## Reading From GCS

The scheduler never downloads the full file.

Instead:

```text
open stream
        ↓
read next line
        ↓
offset++
```

Example:

```text
offset = 125000

read line 125001

offset = 125001
```

Memory remains constant.

Even a file with:

```text
10 million prompts
```

uses the same memory.

---

# Dispatching Request

Prompt:

```json
{
  "prompt_id": 125001,
  "prompt": "..."
}
```

is converted into:

```python
asyncio.create_task(
    call_llm(prompt)
)
```

Update:

```python
running += 1
```

The request now leaves the scheduler.

---

# Response Handler

Responses return asynchronously.

A request sent 20 seconds ago may return after a request sent 2 seconds ago.

Order does not matter.

---

# Success Path

Worker returns:

```http
200 OK
```

Response contains:

```json
{
  "prompt_id":125001,

  "response":"...",

  "usage":
  {
      "prompt_tokens":200,
      "completion_tokens":600
  }
}
```

Scheduler updates:

```python
running -= 1

completed += 1
```

---

# Token Collection

Calculate:

```python
actual_tokens =
prompt_tokens +
completion_tokens
```

Example:

```text
200 + 600

=
800
```

Store:

```python
token_history.append(800)
```

---

# P95 Calculation

Scheduler maintains rolling:

```python
p95_tokens
```

Example history:

```text
700
800
650
900
750
```

Result:

```text
P95 = 850
```

This becomes the future TPM estimate.

---

# Result Buffer

Response is not written immediately.

Stored in:

```python
result_buffer
```

Example:

```json
{
  "prompt_id":125001,
  "response":"..."
}
```

Append:

```python
result_buffer.append(response)
```

---

# Why Buffer?

Without buffering:

```text
1 prompt
=
1 GCS write
```

For:

```text
1 million prompts
```

you get:

```text
1 million writes
```

Instead:

```text
500 responses
=
1 write
```

Now:

```text
1 million prompts

↓

2000 writes
```

Massive cost reduction.

---

# Buffer Flush Conditions

Flush when ANY occurs:

```text
500 responses
OR
50 MB
OR
30 sec
```

Example:

```text
500 responses accumulated
```

Create:

```text
results_part_001.jsonl
```

Example:

```json
{"prompt_id":1,"response":"..."}
{"prompt_id":2,"response":"..."}
...
```

Upload:

```text
GCS
```

Clear:

```python
result_buffer.clear()
```

Continue processing.

---

# Failure Path

Retryable errors:

```http
429
500
502
503
504
timeout
```

Response handler:

```python
running -= 1
```

Prompt moved to:

```python
retry_queue
```

Example:

```python
retry_queue.append(
    prompt
)
```

Prompt is never discarded.

---

# First 429

This is how RPM learning occurs.

Example:

```text
rpm_target = 500

8 req/sec
```

At:

```text
t = 8 sec
```

one request returns:

```http
429
```

Immediately:

```python
dispatch_enabled = False
```

No new requests leave.

---

# In-Flight Drain

Already sent requests continue.

Example:

```text
request 70 → 429

request 71 → already sent

request 72 → already sent
```

Scheduler cannot stop them.

Wait:

```python
while running > 0:
    sleep()
```

All requests finish.

---

# RPM Learning

Current:

```text
rpm_target = 500
```

429 occurred.

Reduce:

```python
rpm_target =
rpm_target * 0.75
```

Result:

```text
375
```

Cooldown:

```text
60 seconds
```

---

# Slow Start

Before first 429:

Every 30 sec:

```python
rpm_target *= 1.5
```

Example:

```text
10
15
22
33
49
73
110
165
...
```

Quickly finds provider limits.

---

# Congestion Avoidance

After first discovered limit:

Switch:

```python
rpm_target += 1
```

Example:

```text
375
376
377
378
```

Slowly probes for unused capacity.

---

# TPM Learning

Successful responses continuously update:

```python
p95_tokens
```

Suppose:

```text
P95 = 800
```

Then:

```python
100000 / 800

=
125 requests/min
```

The scheduler automatically reduces dispatch speed if TPM becomes the bottleneck.

No provider-specific logic required.

---

# Retry Queue Processing

Priority:

```text
Retry Queue
    ↓
New Prompts
```

This guarantees:

* no prompt loss
* no starvation

---

# Checkpoint Writer

Every:

```text
30 sec
```

or after result flush:

Create:

```json
{
  "offset":125000,

  "completed":120000,
  "failed":300,

  "rpm_target":375,
  "tpm_target":100000,

  "p95_tokens":850
}
```

Upload:

```text
state.json
```

to GCS.

---

# SIGTERM Handling

When Kubernetes sends:

```text
SIGTERM
```

Scheduler:

```python
dispatch_enabled = False
```

No new requests leave.

No complex flush required.

---

# Recovery

If scheduler dies:

New scheduler loads:

```text
state.json
```

Restores:

```python
offset
rpm_target
tpm_target
p95_tokens
```

Continues processing.

Some prompts after the last checkpoint may execute twice.

This is acceptable.

The design prefers:

```text
duplicate work
```

over:

```text
lost work
```

---

# Completion

Job completes when:

```text
offset == EOF

AND

running == 0

AND

retry_queue empty
```

Final actions:

```text
Flush result buffer
        ↓
Upload results_final.jsonl
        ↓
Write final state.json
        ↓
Firestore:
status=COMPLETED
```

---

# Final Storage Layout

```text
jobs/job_123/

├── prompts.jsonl

├── state.json

├── results_part_001.jsonl
├── results_part_002.jsonl
├── results_part_003.jsonl

├── results_final.jsonl

└── errors.jsonl
```

The final architecture keeps execution state in scheduler memory, persists only periodic checkpoints, streams prompts from GCS, buffers responses before writing, dynamically learns RPM and TPM limits, and scales to millions of prompts with minimal database and storage operations.

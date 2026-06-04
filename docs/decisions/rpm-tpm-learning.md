# RPM / TPM Learning Logic

## Initialization

```text
rpm_target = 10
tpm_target = 250

p95_tokens = 1000
```

---

## Dispatch

Every second:

```text
allowed_rps = rpm_target / 60

effective_rps =
min(
  rpm_target/60,
  tpm_target/(p95_tokens*60)
)
```

Scheduler continuously dispatches prompts at this rate.

---

## Success Handling

For every successful response:

```text
completed += 1

collect:
  prompt_tokens
  completion_tokens
```

Update:

```text
actual_tokens =
prompt_tokens + completion_tokens
```

Update rolling:

```text
p95_tokens
```

Result stored in memory buffer.

---

## Result Flush

When:

```text
500 responses
OR
50 MB
OR
30 sec
```

flush:

```text
result_buffer
    ↓
GCS results_part_x.jsonl
```

---

## RPM Ramp Up

Every 30 seconds:

```text
No 429 observed
```

Increase:

```text
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
...
```

---

## TPM Learning

Successful responses continuously update:

```text
p95_tokens
```

Example:

```text
Initial P95 = 1000

Observed:
700
750
800
650

New P95 = 800
```

Scheduler automatically increases throughput because token estimate becomes more accurate.

---

## First 429

Immediately:

```text
dispatch_enabled = False
```

No new prompts leave.

Failed prompts:

```text
retry_queue.push_front()
```

---

## Drain In-Flight Requests

Already-sent requests continue.

Wait until:

```text
running == 0
```

---

## Ramp Down

After drain:

```text
rpm_target =
rpm_target * 0.75
```

Example:

```text
500
↓
375
```

Cooldown:

```text
60 sec
```

---

## Congestion Avoidance

After first limit discovery:

Instead of:

```text
×1.5
```

Use:

```text
rpm_target += 1
```

every evaluation cycle.

Example:

```text
375
376
377
378
...
```

This slowly probes for additional capacity.

---

## Retry Processing

Priority:

```text
1. retry_queue
2. new prompts
```

No prompt is lost.

---

## Core Principle

```text
Successful responses
    ↓
Teach TPM

429 responses
    ↓
Teach RPM

P95 tokens
    ↓
Controls TPM budgeting

RPM target
    ↓
Controls dispatch speed
```

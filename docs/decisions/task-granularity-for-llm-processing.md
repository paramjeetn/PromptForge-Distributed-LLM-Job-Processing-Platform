# Decision: 1 Prompt per Task vs Batched Prompts per Task

## Assumptions

* 100 prompts total
* Average LLM latency = 20 seconds
* LLM RPM allows all requests concurrently
* Cloud Run worker performs async LLM calls
* LLM spend = $100

## Architecture Comparison

| Factor                 | Option A: 100 Tasks × 1 Prompt | Option C: 1 Task × 100 Prompts |
| ---------------------- | ------------------------------ | ------------------------------ |
| Cloud Tasks Created    | 100                            | 1                              |
| Cloud Run Requests     | 100                            | 1                              |
| LLM API Calls          | 100                            | 100                            |
| End-to-End Latency     | ~20 sec                        | ~20 sec                        |
| Cloud Run Compute      | Nearly Same                    | Nearly Same                    |
| Network to LLM         | Same                           | Same                           |
| LLM Cost               | $100                           | $100                           |
| Cloud Task Cost        | Slightly Higher                | Slightly Lower                 |
| Logging Volume         | ~100x Higher                   | Baseline                       |
| Monitoring Noise       | High                           | Low                            |
| Queue Objects          | 100                            | 1                              |
| Retry Granularity      | Per Prompt                     | Custom Logic Needed            |
| Operational Simplicity | Simple                         | Slightly More Complex          |
| Throughput             | Same                           | Same                           |

## Primary Benefits of Batching

* Fewer Cloud Tasks
* Fewer Cloud Run requests
* Less logging
* Lower monitoring noise
* Easier RPM/TPM control
* Better queue efficiency

## Infrastructure Cost Analysis

### Cloud Tasks

* ~$0.04 per million tasks

### Cloud Run Requests

* ~$0.04 per million requests

### Request Logging

Even if the worker does not emit application logs, Cloud Run automatically generates request logs.

Typical request log size:

* ~650–1,000 bytes
* Assume ~750 bytes/request

Calculation:

```text
1,000,000 requests × 750 bytes
≈ 750,000,000 bytes
≈ 0.7 GiB
```

Cloud Logging pricing:

```text
$0.50 per GiB
```

Therefore:

```text
1,000,000 requests
≈ 0.7 GiB logs
≈ $0.35 logging cost
```

## Cost per 1 Million Prompts

### Option A (1 Prompt per Task)

```text
1,000,000 Cloud Tasks
1,000,000 Cloud Run Requests
1,000,000 Request Logs
```

| Component                     | Cost   |
| ----------------------------- | ------ |
| Cloud Tasks                   | ~$0.04 |
| Cloud Run Requests            | ~$0.04 |
| Request Logs                  | ~$0.35 |
| Total Infrastructure Overhead | ~$0.43 |

### Option C (100 Prompts per Task)

```text
10,000 Cloud Tasks
10,000 Cloud Run Requests
10,000 Request Logs
```

| Component                     | Cost     |
| ----------------------------- | -------- |
| Cloud Tasks                   | ~$0.0004 |
| Cloud Run Requests            | ~$0.0004 |
| Request Logs                  | ~$0.0035 |
| Total Infrastructure Overhead | ~$0.0043 |

## Conclusion

Batching reduces:

* Cloud Tasks by ~100x
* Cloud Run requests by ~100x
* Request logs by ~100x
* Queue objects by ~100x

However, infrastructure costs are already extremely small.

For example:

```text
LLM Cost                     ~$100.00
Option A Infrastructure      ~$0.43
Option C Infrastructure      ~$0.004
```

The primary reason to batch is not direct cost savings. The main advantages are cleaner operations, lower monitoring noise, fewer queue objects, easier RPM/TPM management, and better overall scalability.

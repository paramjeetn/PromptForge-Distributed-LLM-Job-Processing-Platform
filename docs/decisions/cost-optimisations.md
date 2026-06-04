# Cost Optimization Decisions

## 1. Firestore Watch Instead of Scheduler Polling

### Old Approach

```text
GKE Scheduler
    ↓
Query Firestore every 1 second
    ↓
Check for QUEUED jobs
```

Problems:

* 86,400 Firestore queries/day even when no jobs exist
* Continuous database reads while idle
* Wasted CPU cycles inside scheduler

### New Approach

```text
Firestore
    ↓
watch(status == "QUEUED")
    ↓
GKE Scheduler
```

Benefits:

* Single long-lived streaming connection
* No periodic polling
* Scheduler sleeps while idle
* Firestore only sends updates when matching documents change

Result:

```text
Idle System
    ≈ 0 Firestore reads

New Job Arrives
    1 document update
    1 notification
```

---

## 2. Event-Driven Upload Detection

### Old Approach

```text
GKE
    ↓
Scan GCS bucket every N minutes
    ↓
Look for uploaded files
```

Problems:

* Bucket listing operations
* Delayed job discovery
* Continuous polling infrastructure

### New Approach

```text
GCS Upload Complete
        ↓
OBJECT_FINALIZE
        ↓
Eventarc
        ↓
Cloud Function
        ↓
Firestore status = QUEUED
```

Benefits:

* No bucket scanning
* Near-instant detection
* Fully event-driven

---

## 3. Prompt Storage Moved to GCS

### Old Approach

```text
Firestore
    ├── Metadata
    └── Prompts
```

Problems:

* Large document count
* Expensive writes
* Firestore used as bulk storage

### New Approach

```text
Firestore
    ├── Metadata
    ├── Status
    ├── Timestamps
    └── Configuration

GCS
    ├── prompts.jsonl
    ├── results.jsonl
    └── errors.jsonl
```

Benefits:

* Firestore acts only as a state machine
* Bulk data stored in object storage
* Much lower storage and write costs

---

## 4. Direct Browser → GCS Upload

### Old Approach

```text
Browser
    ↓
API Server
    ↓
GCS
```

Problems:

* API bandwidth cost
* API CPU/memory usage
* Upload bottleneck

### New Approach

```text
Browser
    ↓
Signed URL
    ↓
GCS
```

Benefits:

* API never touches file contents
* No upload processing cost
* Unlimited upload scalability

---

## 5. Metadata-Only Firestore Records

Current Firestore document contains only:

```text
job_id
status
provider
model
rpm
tpm
api_key_ref
upload_path
timestamps
file_size
prompt_count
```

Benefits:

* Small documents
* Fast reads
* Cheap updates
* Easy indexing

---

## Summary

Major cost reductions achieved:

1. Firestore Watch replaces continuous scheduler polling.
2. OBJECT_FINALIZE replaces GCS bucket scanning.
3. GCS stores prompts/results instead of Firestore.
4. Signed URLs eliminate API upload traffic.
5. Firestore stores only metadata and job state.

Result:

```text
Browser
    ↓
GCS
    ↓
OBJECT_FINALIZE
    ↓
Firestore Status Update
    ↓
Firestore Watch
    ↓
Scheduler

No polling.
No bucket scans.
No prompt storage in Firestore.
Minimal Firestore reads/writes.
```

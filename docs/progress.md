# PromptForge — Progress

## What's Working (End-to-End Verified)

**Phase 1 — Infra** ✅
GKE Autopilot cluster, GCS buckets, Firestore, Secret Manager, Service Accounts — all live on GCP (`promptforge-1212`, `us-central1`).

**Phase 2 — Job Init API** ✅
`POST /v1/jobs/init` creates Firestore doc + returns signed GCS upload URL. 11 integration tests pass against real GCP.

**Phase 3 — Launcher** ✅
GCS upload → Eventarc → Launcher validates JSONL → spawns GKE Job → Firestore `QUEUED`. 11 integration tests pass against real GCP.

**Phase 4 — Execution Pod** ✅
GKE pod streams prompts from GCS → calls LLM (OpenAI gpt-4o-mini) at configured RPM via LiteLLM → writes results_part_NNN.jsonl + results_final.jsonl → Firestore `COMPLETED`. 10/10 prompts completed end-to-end.

---

## What's Left

| Phase | What |
|-------|------|
| Fix Pulumi | Cloud NAT, k8s ServiceAccount, Eventarc trigger manually created — not in infra code yet |
| Phase 5 | Results buffering coded. Needs 500-record/50MB/30s flush validation with larger batches |
| Phase 6 | Rate learning coded. Needs real-world validation with larger prompt sets |
| Phase 7 | Checkpointing (state.json) — crash recovery + resume from offset |
| Phase 8 | Job queue — on COMPLETED, auto-start next PENDING job for same client |
| Phase 9 | GET /v1/jobs/{id}/status and GET /v1/jobs/{id}/results endpoints |
| Phase 10 | Deploy API to Cloud Run, end-to-end client-facing test with real Unkey auth |

---

## Manual GCP Setup (not yet in Pulumi)

- Cloud Router + Cloud NAT (promptforge-router / promptforge-nat, us-central1)
- Kubernetes ServiceAccount `promptforge-exec` with Workload Identity annotation
- Eventarc trigger `promptforge-gcs-trigger` (GCS OBJECT_FINALIZE → launcher)
- Cloud Run service `promptforge-launcher`

import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";

const config = new pulumi.Config("gcp");
const project = config.require("project");

// ─── Service Accounts ─────────────────────────────────────────────────────────

// API service: handles POST /v1/jobs/init — creates Firestore records,
// generates signed GCS upload URLs.
export const apiSa = new gcp.serviceaccount.Account("promptforge-api-sa", {
    accountId: "promptforge-api",
    displayName: "PromptForge API Service",
    project: project,
});

// Launcher service: triggered by Eventarc, validates the uploaded file,
// creates the GKE execution Job.
export const launcherSa = new gcp.serviceaccount.Account("promptforge-launcher-sa", {
    accountId: "promptforge-launcher",
    displayName: "PromptForge Launcher Service",
    project: project,
});

// Execution pod: runs inside GKE, reads prompts from GCS, calls LLM,
// writes results back to GCS, updates Firestore status.
export const execSa = new gcp.serviceaccount.Account("promptforge-exec-sa", {
    accountId: "promptforge-exec",
    displayName: "PromptForge Execution Pod",
    project: project,
});

// ─── API SA roles ──────────────────────────────────────────────────────────────

// Firestore: create job records, read job status
new gcp.projects.IAMMember("api-firestore", {
    project: project,
    role: "roles/datastore.user",
    member: pulumi.interpolate`serviceAccount:${apiSa.email}`,
});

// Sign GCS upload/download URLs (signBlob permission on itself)
new gcp.serviceaccount.IAMMember("api-token-creator", {
    serviceAccountId: apiSa.name,
    role: "roles/iam.serviceAccountTokenCreator",
    member: pulumi.interpolate`serviceAccount:${apiSa.email}`,
});

// GCS output bucket: list result files + generate signed download URLs
new gcp.projects.IAMMember("api-gcs-read", {
    project: project,
    role: "roles/storage.objectViewer",
    member: pulumi.interpolate`serviceAccount:${apiSa.email}`,
});

// ─── Launcher SA roles ─────────────────────────────────────────────────────────

// Firestore: read job record, update status to QUEUED/PENDING
new gcp.projects.IAMMember("launcher-firestore", {
    project: project,
    role: "roles/datastore.user",
    member: pulumi.interpolate`serviceAccount:${launcherSa.email}`,
});

// GCS input bucket: stream-read prompts.jsonl for validation
new gcp.projects.IAMMember("launcher-gcs-read", {
    project: project,
    role: "roles/storage.objectViewer",
    member: pulumi.interpolate`serviceAccount:${launcherSa.email}`,
});

// GCS output bucket: write errors.jsonl for invalid lines found during validation
new gcp.projects.IAMMember("launcher-gcs-write", {
    project: project,
    role: "roles/storage.objectCreator",
    member: pulumi.interpolate`serviceAccount:${launcherSa.email}`,
});

// GKE: authenticate to cluster and create execution Jobs
new gcp.projects.IAMMember("launcher-container", {
    project: project,
    role: "roles/container.developer",
    member: pulumi.interpolate`serviceAccount:${launcherSa.email}`,
});

// ─── Execution pod SA roles ────────────────────────────────────────────────────

// Firestore: read job config on startup, update status to PROCESSING/COMPLETED
new gcp.projects.IAMMember("exec-firestore", {
    project: project,
    role: "roles/datastore.user",
    member: pulumi.interpolate`serviceAccount:${execSa.email}`,
});

// GCS: read prompts.jsonl, write results/errors/state.json, delete prompts.jsonl
new gcp.projects.IAMMember("exec-gcs", {
    project: project,
    role: "roles/storage.objectAdmin",
    member: pulumi.interpolate`serviceAccount:${execSa.email}`,
});

// Secret Manager: read provider LLM API keys stored as secrets
new gcp.projects.IAMMember("exec-secrets", {
    project: project,
    role: "roles/secretmanager.secretAccessor",
    member: pulumi.interpolate`serviceAccount:${execSa.email}`,
});

// GKE: spawn the next queued job after current job completes (Phase 8)
new gcp.projects.IAMMember("exec-container", {
    project: project,
    role: "roles/container.developer",
    member: pulumi.interpolate`serviceAccount:${execSa.email}`,
});

// ─── Secret Manager access ────────────────────────────────────────────────────
// Cloud Run injects secret env vars using the service's SA — it must have
// secretAccessor on each secret referenced in the Cloud Run service definition.

// API SA: reads UNKEY_API_ID, UNKEY_ROOT_KEY, SENTRY_DSN, AXIOM_API_KEY
new gcp.projects.IAMMember("api-secrets", {
    project: project,
    role: "roles/secretmanager.secretAccessor",
    member: pulumi.interpolate`serviceAccount:${apiSa.email}`,
});

// Launcher SA: reads SENTRY_DSN, AXIOM_API_KEY
new gcp.projects.IAMMember("launcher-secrets", {
    project: project,
    role: "roles/secretmanager.secretAccessor",
    member: pulumi.interpolate`serviceAccount:${launcherSa.email}`,
});

// Workload Identity binding lives in gke.ts — it depends on the cluster
// being fully created first (the identity pool only exists after cluster creation).

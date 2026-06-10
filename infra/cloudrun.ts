import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";
import { inputBucket, outputBucket } from "./gcs";
import { apiSa, launcherSa } from "./iam";
import { cluster } from "./gke";
import { registry, registryUrl } from "./registry";
import { unkeyApiIdSecret, unkeyRootKeySecret, sentryDsnSecret, axiomApiKeySecret } from "./secrets";

const gcpConfig = new pulumi.Config("gcp");
const project = gcpConfig.require("project");
const region = gcpConfig.get("region") || "us-central1";

// Image URIs — built and pushed by `make build-push`.
// Cloud Run pulls these from Artifact Registry on each deploy.
const apiImage       = pulumi.interpolate`${registryUrl}/api:latest`;
const launcherImage  = pulumi.interpolate`${registryUrl}/launcher:latest`;
const executionImage = pulumi.interpolate`${registryUrl}/execution:latest`;

// ─── API service ──────────────────────────────────────────────────────────────
// Serves POST /v1/jobs/init, GET /v1/jobs/{id}/status, GET /v1/jobs/{id}/results
// Public endpoint — auth handled by Unkey at the application layer.

export const apiService = new gcp.cloudrun.Service("promptforge-api", {
    name: "promptforge-api",
    location: region,
    project,
    template: {
        metadata: {
            annotations: {
                "autoscaling.knative.dev/minScale": "0",
                "autoscaling.knative.dev/maxScale": "10",
            },
        },
        spec: {
            serviceAccountName: apiSa.email,
            containers: [{
                image: apiImage,
                ports: [{ containerPort: 8080 }],
                envs: [
                    { name: "GCS_INPUT_BUCKET",     value: inputBucket.name },
                    { name: "GCS_OUTPUT_BUCKET",    value: outputBucket.name },
                    { name: "FIRESTORE_PROJECT_ID", value: project },
                    { name: "GCP_PROJECT_ID",       value: project },
                    { name: "AXIOM_DATASET",        value: "promptforge" },
                    {
                        name: "UNKEY_API_ID",
                        valueFrom: { secretKeyRef: { name: unkeyApiIdSecret.secretId, key: "latest" } },
                    },
                    {
                        name: "UNKEY_ROOT_KEY",
                        valueFrom: { secretKeyRef: { name: unkeyRootKeySecret.secretId, key: "latest" } },
                    },
                    {
                        name: "SENTRY_DSN",
                        valueFrom: { secretKeyRef: { name: sentryDsnSecret.secretId, key: "latest" } },
                    },
                    {
                        name: "AXIOM_API_KEY",
                        valueFrom: { secretKeyRef: { name: axiomApiKeySecret.secretId, key: "latest" } },
                    },
                ],
                resources: { limits: { memory: "256Mi", cpu: "1000m" } },
            }],
        },
    },
    traffics: [{ percent: 100, latestRevision: true }],
}, { dependsOn: [registry, unkeyApiIdSecret, unkeyRootKeySecret, sentryDsnSecret, axiomApiKeySecret] });

// Allow unauthenticated invocations — Unkey handles auth at the app layer.
new gcp.cloudrun.IamMember("api-public-invoker", {
    service: apiService.name,
    location: region,
    project,
    role: "roles/run.invoker",
    member: "allUsers",
});

// ─── Launcher service ─────────────────────────────────────────────────────────
// Receives Eventarc CloudEvents from GCS. Validates uploads and spawns GKE Jobs.
// Not public — only the Launcher SA (used by Eventarc) can invoke it.

export const launcherService = new gcp.cloudrun.Service("promptforge-launcher", {
    name: "promptforge-launcher",
    location: region,
    project,
    template: {
        metadata: {
            annotations: {
                "autoscaling.knative.dev/minScale": "0",
                "autoscaling.knative.dev/maxScale": "5",
            },
        },
        spec: {
            serviceAccountName: launcherSa.email,
            containers: [{
                image: launcherImage,
                ports: [{ containerPort: 8080 }],
                envs: [
                    { name: "GCS_INPUT_BUCKET",     value: inputBucket.name },
                    { name: "GCS_OUTPUT_BUCKET",    value: outputBucket.name },
                    { name: "FIRESTORE_PROJECT_ID", value: project },
                    { name: "GCP_PROJECT_ID",       value: project },
                    // GKE details — execution pod spawning
                    { name: "GKE_CLUSTER_ENDPOINT", value: cluster.endpoint },
                    { name: "GKE_CLUSTER_CA",       value: cluster.masterAuth.clusterCaCertificate },
                    { name: "GKE_NAMESPACE",        value: "default" },
                    { name: "EXECUTION_IMAGE",      value: executionImage },
                    { name: "AXIOM_DATASET",        value: "promptforge" },
                    {
                        name: "SENTRY_DSN",
                        valueFrom: { secretKeyRef: { name: sentryDsnSecret.secretId, key: "latest" } },
                    },
                    {
                        name: "AXIOM_API_KEY",
                        valueFrom: { secretKeyRef: { name: axiomApiKeySecret.secretId, key: "latest" } },
                    },
                ],
                resources: { limits: { memory: "256Mi", cpu: "1000m" } },
            }],
        },
    },
    traffics: [{ percent: 100, latestRevision: true }],
}, { dependsOn: [registry, sentryDsnSecret, axiomApiKeySecret] });

// Allow Eventarc (running as launcherSa) to invoke the Launcher service.
new gcp.cloudrun.IamMember("launcher-eventarc-invoker", {
    service: launcherService.name,
    location: region,
    project,
    role: "roles/run.invoker",
    member: pulumi.interpolate`serviceAccount:${launcherSa.email}`,
});

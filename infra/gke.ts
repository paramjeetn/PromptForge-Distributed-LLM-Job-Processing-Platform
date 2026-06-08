import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";
import { execSa } from "./iam";

const config = new pulumi.Config("gcp");
const project = config.require("project");
const region = config.get("region") || "us-central1";

// GKE Autopilot cluster — no node management, pay per pod resource request.
// One execution pod is created per job. Pods are ephemeral — they start,
// run for the job duration, and exit. Autopilot is cost-efficient for this
// pattern because you only pay while a pod is actually running.
//
// Workload Identity is enabled so execution pods can call GCP APIs
// (Firestore, GCS, Secret Manager) without storing credentials in the pod.
// The pod's K8s ServiceAccount is annotated to impersonate the GCP exec SA.
// That binding is defined in iam.ts.
export const cluster = new gcp.container.Cluster("promptforge-cluster", {
    name: "promptforge-cluster",
    project: project,
    location: region,
    enableAutopilot: true,
    workloadIdentityConfig: {
        workloadPool: `${project}.svc.id.goog`,
    },
    // Allow pulumi destroy to delete the cluster without manual intervention
    deletionProtection: false,
});

// Workload Identity binding — must come after cluster creation because the
// identity pool ({project}.svc.id.goog) only exists once the cluster is up.
// Allows the K8s ServiceAccount "promptforge-exec" in the "default" namespace
// to impersonate the GCP exec service account without storing credentials.
new gcp.serviceaccount.IAMMember("exec-workload-identity", {
    serviceAccountId: execSa.name,
    role: "roles/iam.workloadIdentityUser",
    member: pulumi.interpolate`serviceAccount:${project}.svc.id.goog[default/promptforge-exec]`,
}, { dependsOn: [cluster] });

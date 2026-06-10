import * as k8s from "@pulumi/kubernetes";
import * as pulumi from "@pulumi/pulumi";
import { cluster } from "./gke";
import { execSa } from "./iam";

const config = new pulumi.Config("gcp");
const project = config.require("project");
const region = config.get("region") || "us-central1";

// Build a kubeconfig that uses gke-gcloud-auth-plugin for token exchange.
// Prerequisite: `gcloud components install gke-gcloud-auth-plugin`
const kubeconfig = pulumi.all([cluster.name, cluster.endpoint, cluster.masterAuth]).apply(
    ([name, endpoint, auth]) => JSON.stringify({
        apiVersion: "v1",
        clusters: [{
            cluster: {
                "certificate-authority-data": auth.clusterCaCertificate,
                server: `https://${endpoint}`,
            },
            name: "gke",
        }],
        contexts: [{ context: { cluster: "gke", user: "gke" }, name: "gke" }],
        "current-context": "gke",
        kind: "Config",
        users: [{
            name: "gke",
            user: {
                exec: {
                    apiVersion: "client.authentication.k8s.io/v1beta1",
                    command: "gke-gcloud-auth-plugin",
                    provideClusterInfo: true,
                },
            },
        }],
    })
);

const k8sProvider = new k8s.Provider("gke-k8s", {
    kubeconfig,
}, { dependsOn: [cluster] });

// Kubernetes ServiceAccount for execution pods.
// The annotation links this K8s SA to the GCP service account via Workload Identity,
// so pods can call GCP APIs (Firestore, GCS, Secret Manager) without storing credentials.
export const execK8sSa = new k8s.core.v1.ServiceAccount("exec-k8s-sa", {
    metadata: {
        name: "promptforge-exec",
        namespace: "default",
        annotations: {
            "iam.gke.io/gcp-service-account": pulumi.interpolate`promptforge-exec@${project}.iam.gserviceaccount.com`,
        },
    },
}, { provider: k8sProvider, dependsOn: [execSa] });

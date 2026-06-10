import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";

const gcpConfig = new pulumi.Config("gcp");
const project = gcpConfig.require("project");
const region = gcpConfig.get("region") || "us-central1";

// Artifact Registry — stores Docker images for API, Launcher, and Execution services.
// Images are pushed here by `make build-push` before running `pulumi up`.
export const registry = new gcp.artifactregistry.Repository("promptforge-registry", {
    repositoryId: "promptforge",
    format: "DOCKER",
    location: region,
    project,
    description: "PromptForge service Docker images",
});

// Base URL — append /{service}:{tag} for a full image URI.
// e.g. us-central1-docker.pkg.dev/promptforge-1212/promptforge/api:latest
export const registryUrl = pulumi.interpolate`${region}-docker.pkg.dev/${project}/promptforge`;

// Grant the Cloud Run service agent read access to pull images.
// Without this, Cloud Run cannot pull images from Artifact Registry.
const projectData = gcp.organizations.getProjectOutput({ projectId: project });
new gcp.projects.IAMMember("cloudrun-registry-read", {
    project,
    role: "roles/artifactregistry.reader",
    member: pulumi.interpolate`serviceAccount:service-${projectData.number}@serverless-robot-prod.iam.gserviceaccount.com`,
}, { dependsOn: [registry] });

// Grant the GKE service agent read access so execution pods can pull their image.
new gcp.projects.IAMMember("gke-registry-read", {
    project,
    role: "roles/artifactregistry.reader",
    member: pulumi.interpolate`serviceAccount:service-${projectData.number}@container-engine-robot.iam.gserviceaccount.com`,
}, { dependsOn: [registry] });

import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";

const config = new pulumi.Config("gcp");
const project = config.require("project");
const region = config.get("region") || "us-central1";

// Clients upload prompts.jsonl here via signed URL.
// Eventarc watches this bucket for OBJECT_FINALIZE events.
// Deleted by the execution pod on job completion.
export const inputBucket = new gcp.storage.Bucket("promptforge-input", {
    name: `promptforge-input-${project}`,
    location: region.toUpperCase(),
    uniformBucketLevelAccess: true,
    forceDestroy: true,
});

// Execution pod writes results, errors, and checkpoints here.
// No Eventarc on this bucket — it is output-only.
export const outputBucket = new gcp.storage.Bucket("promptforge-output", {
    name: `promptforge-output-${project}`,
    location: region.toUpperCase(),
    uniformBucketLevelAccess: true,
    forceDestroy: true,
});

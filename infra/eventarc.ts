import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";
import { inputBucket } from "./gcs";
import { launcherSa } from "./iam";

const config = new pulumi.Config("gcp");
const project = config.require("project");
const region = config.get("region") || "us-central1";

// Eventarc trigger: fires on every new object in the input bucket.
// Delivers a CloudEvent (HTTP POST) to the Launcher Cloud Run service.
//
// NOT imported in index.ts for Phase 1 — the Launcher Cloud Run service
// must exist before this trigger can be created. This file is applied
// in Phase 3 after the Launcher is deployed.
//
// To apply: add `import "./eventarc";` to index.ts, then run `pulumi up`.
export const uploadTrigger = new gcp.eventarc.Trigger("upload-trigger", {
    name: "promptforge-upload-trigger",
    project: project,
    location: region,
    matchingCriterias: [
        {
            attribute: "type",
            value: "google.cloud.storage.object.v1.finalized",
        },
        {
            attribute: "bucket",
            value: inputBucket.name,
        },
    ],
    destination: {
        cloudRunService: {
            service: "promptforge-launcher",
            region: region,
            path: "/",
        },
    },
    serviceAccount: launcherSa.email,
});

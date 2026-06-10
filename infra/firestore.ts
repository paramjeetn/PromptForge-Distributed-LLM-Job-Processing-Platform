import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";

const config = new pulumi.Config("gcp");
const project = config.require("project");

// Firestore in Native mode — single "jobs" collection.
// No per-prompt records. No rate state. Job metadata only.
// Location must match the region all other services use.
export const firestoreDb = new gcp.firestore.Database("promptforge-db", {
    name: "(default)",
    project: project,
    locationId: "us-central1",
    type: "FIRESTORE_NATIVE",
    deleteProtectionState: "DELETE_PROTECTION_DISABLED",
    deletionPolicy: "DELETE",
});

// Composite index required by the job queue query:
//   WHERE client_id == X AND status == "PENDING" ORDER BY created_at ASC
// Without this index Firestore returns a FAILED_PRECONDITION error.
export const jobQueueIndex = new gcp.firestore.Index("job-queue-index", {
    project: project,
    database: "(default)",
    collection: "jobs",
    fields: [
        { fieldPath: "client_id", order: "ASCENDING" },
        { fieldPath: "status",    order: "ASCENDING" },
        { fieldPath: "created_at", order: "ASCENDING" },
    ],
}, { dependsOn: [firestoreDb] });

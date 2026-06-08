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

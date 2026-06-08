import * as pulumi from "@pulumi/pulumi";

import { inputBucket, outputBucket } from "./gcs";
import { apiSa, launcherSa, execSa } from "./iam";
import { firestoreDb } from "./firestore";
import { cluster } from "./gke";

// eventarc.ts is applied in Phase 3 after the Launcher Cloud Run
// service is deployed. Uncomment the line below then run `pulumi up`.
// import "./eventarc";

// ─── Outputs ──────────────────────────────────────────────────────────────────
// These values are printed after `pulumi up` and can be read
// via `pulumi stack output <name>`.

export const inputBucketName = inputBucket.name;
export const outputBucketName = outputBucket.name;

export const clusterName = cluster.name;
export const clusterEndpoint = cluster.endpoint;

export const firestoreDbName = firestoreDb.name;

export const apiServiceAccountEmail = apiSa.email;
export const launcherServiceAccountEmail = launcherSa.email;
export const execServiceAccountEmail = execSa.email;

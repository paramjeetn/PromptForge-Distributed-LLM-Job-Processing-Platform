import * as pulumi from "@pulumi/pulumi";

import { inputBucket, outputBucket } from "./gcs";
import { apiSa, launcherSa, execSa } from "./iam";
import { firestoreDb } from "./firestore";
import { cluster } from "./gke";
import { nat } from "./nat";
import { execK8sSa } from "./k8s";
import "./eventarc";

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

export const natName = nat.name;
export const execK8sSaName = execK8sSa.metadata.name;

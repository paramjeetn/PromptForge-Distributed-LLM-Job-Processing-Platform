import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";
import { cluster } from "./gke";

const config = new pulumi.Config("gcp");
const project = config.require("project");
const region = config.get("region") || "us-central1";

// GKE Autopilot pods have Private Google Access (can reach GCP APIs directly)
// but cannot reach the public internet (e.g. OpenAI, Anthropic endpoints)
// without Cloud NAT. This router + NAT enables outbound internet from pods.

const router = new gcp.compute.Router("promptforge-router", {
    name: "promptforge-router",
    project: project,
    region: region,
    network: "default",
}, { dependsOn: [cluster] });

export const nat = new gcp.compute.RouterNat("promptforge-nat", {
    name: "promptforge-nat",
    project: project,
    region: region,
    router: router.name,
    natIpAllocateOption: "AUTO_ONLY",
    sourceSubnetworkIpRangesToNat: "ALL_SUBNETWORKS_ALL_IP_RANGES",
    logConfig: {
        enable: false,
        filter: "ERRORS_ONLY",
    },
}, { dependsOn: [router] });

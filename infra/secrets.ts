import * as gcp from "@pulumi/gcp";
import * as pulumi from "@pulumi/pulumi";

const gcpConfig = new pulumi.Config("gcp");
const project = gcpConfig.require("project");

// Sensitive values stored as Pulumi secrets in stack config.
// Run once before `pulumi up`:
//
//   cd infra
//   pulumi config set --secret unkeyApiId     <value>
//   pulumi config set --secret unkeyRootKey   <value>
//   pulumi config set --secret sentryDsn      <value>
//   pulumi config set --secret axiomApiKey    <value>
//
const appConfig = new pulumi.Config();

function createSecret(id: string, value: pulumi.Input<string>): gcp.secretmanager.Secret {
    const secret = new gcp.secretmanager.Secret(id, {
        secretId: id,
        project,
        replication: { auto: {} },
    });
    new gcp.secretmanager.SecretVersion(`${id}-version`, {
        secret: secret.id,
        secretData: value,
    });
    return secret;
}

// Unkey — API auth (API service only)
export const unkeyApiIdSecret   = createSecret("unkey-api-id",   appConfig.requireSecret("unkeyApiId"));
export const unkeyRootKeySecret = createSecret("unkey-root-key", appConfig.requireSecret("unkeyRootKey"));

// Observability — Sentry + Axiom (API + Launcher services)
export const sentryDsnSecret    = createSecret("sentry-dsn",     appConfig.requireSecret("sentryDsn"));
export const axiomApiKeySecret  = createSecret("axiom-api-key",  appConfig.requireSecret("axiomApiKey"));

.PHONY: help lint test test-unit test-integration build build-push deploy infra-preview infra-up infra-config dev

GCP_PROJECT  ?= promptforge-1212
GCP_REGION   ?= us-central1
REGISTRY      = $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT)/promptforge

help:
	@echo "PromptForge"
	@echo ""
	@echo "  make dev            Start local dev stack (mock LLM + services)"
	@echo "  make test           Run all tests"
	@echo "  make lint           Lint all Python services"
	@echo "  make build          Build Docker images locally"
	@echo "  make build-push     Build + push images to Artifact Registry"
	@echo "  make deploy         build-push then pulumi up"
	@echo "  make infra-config   Set Pulumi secrets from .env (run once)"
	@echo "  make infra-preview  Pulumi preview (dry run)"
	@echo "  make infra-up       Pulumi up (apply infrastructure)"

# ─── Local Dev ────────────────────────────────────────────────────────────────

dev:
	docker compose up --build

# ─── Test ─────────────────────────────────────────────────────────────────────

test:
	pytest tests/unit -v
	pytest tests/integration -v

test-unit:
	pytest tests/unit -v

test-integration:
	@echo "Requires: gcloud auth application-default login  +  .env with GCP values"
	pytest tests/integration -v -s

# ─── Lint ─────────────────────────────────────────────────────────────────────

lint:
	ruff check services/ shared/ tests/
	ruff format --check services/ shared/ tests/

format:
	ruff format services/ shared/ tests/

# ─── Build ────────────────────────────────────────────────────────────────────

build:
	docker build -t promptforge-api       -f services/api/Dockerfile .
	docker build -t promptforge-launcher  -f services/launcher/Dockerfile .
	docker build -t promptforge-execution -f services/execution/Dockerfile .

# Build + tag for Artifact Registry + push all three images.
# Requires: docker, gcloud auth configure-docker (done automatically below).
build-push:
	gcloud auth configure-docker $(GCP_REGION)-docker.pkg.dev --quiet
	docker build -t $(REGISTRY)/api:latest       -f services/api/Dockerfile .
	docker build -t $(REGISTRY)/launcher:latest  -f services/launcher/Dockerfile .
	docker build -t $(REGISTRY)/execution:latest -f services/execution/Dockerfile .
	docker push $(REGISTRY)/api:latest
	docker push $(REGISTRY)/launcher:latest
	docker push $(REGISTRY)/execution:latest

# ─── Deploy ───────────────────────────────────────────────────────────────────

# Full deploy: build images, push to Artifact Registry, then apply infra.
deploy: build-push
	cd infra && pulumi up --yes

# ─── Infrastructure ───────────────────────────────────────────────────────────

# Run once after cloning — loads secrets from .env into Pulumi stack config.
# Requires .env to have: UNKEY_API_ID, UNKEY_ROOT_KEY, SENTRY_DSN, AXIOM_API_KEY
infra-config:
	@set -a && . ./.env && set +a && cd infra && \
		pulumi config set --secret unkeyApiId   "$$UNKEY_API_ID"   && \
		pulumi config set --secret unkeyRootKey "$$UNKEY_ROOT_KEY" && \
		pulumi config set --secret sentryDsn    "$$SENTRY_DSN"     && \
		pulumi config set --secret axiomApiKey  "$$AXIOM_API_KEY"
	@echo "Pulumi secrets set from .env"

infra-preview:
	cd infra && pulumi preview

infra-up:
	cd infra && pulumi up

.PHONY: help lint test build deploy infra-preview infra-up dev

help:
	@echo "PromptForge"
	@echo ""
	@echo "  make dev            Start local dev stack (mock LLM + services)"
	@echo "  make test           Run all tests"
	@echo "  make lint           Lint all Python services"
	@echo "  make build          Build all Docker images"
	@echo "  make deploy         Deploy all services to GCP"
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
	pytest tests/integration -v

# ─── Lint ─────────────────────────────────────────────────────────────────────

lint:
	ruff check services/ shared/ tests/
	ruff format --check services/ shared/ tests/

format:
	ruff format services/ shared/ tests/

# ─── Build ────────────────────────────────────────────────────────────────────

build:
	docker build -t promptforge-api      -f services/api/Dockerfile .
	docker build -t promptforge-launcher -f services/launcher/Dockerfile .
	docker build -t promptforge-execution -f services/execution/Dockerfile .

# ─── Deploy ───────────────────────────────────────────────────────────────────

deploy:
	@echo "Deploy targets not yet configured. See docs/build_phases/phases.md"

# ─── Infrastructure ───────────────────────────────────────────────────────────

infra-preview:
	cd infra && pulumi preview

infra-up:
	cd infra && pulumi up

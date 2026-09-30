.PHONY: help join-help dev stop up down plan status-aws test lint format typecheck install-backend install-data-plane install-console install-website install-installer install-agent clean migrate seed backend-test data-plane-test console-test agent-test installer-test terraform-check ansible-check observability mqtt-certs test-mqtt test-cloud-e2e dev-control-plane dev-data-plane dev-console dev-website stop-control-plane stop-data-plane stop-console stop-website checkout-ui

COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml
OBS_COMPOSE := $(COMPOSE) -f docker-compose.observability.yml
CP_COMPOSE := COMPOSE_PROJECT_NAME=meteorcloud-cp docker compose --project-directory . -f compose/control-plane.yml
DP_COMPOSE := COMPOSE_PROJECT_NAME=meteorcloud-dp docker compose --project-directory . -f compose/data-plane.yml
CONSOLE_COMPOSE := COMPOSE_PROJECT_NAME=meteorcloud-console docker compose --project-directory . -f compose/console.yml
WEBSITE_COMPOSE := COMPOSE_PROJECT_NAME=meteorcloud-website docker compose --project-directory . -f compose/website.yml
BACKEND_DIR := control-plane
DATA_PLANE_DIR := data-plane
CONSOLE_DIR := console
WEBSITE_DIR := website
INSTALLER_DIR := infrastructure/installer
AGENT_DIR := device-plane/agent
INFRA_DIR := infrastructure
CONFIG ?= installation.yaml

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@$(MAKE) --no-print-directory join-help

join-help:
	@echo ""
	@echo "Join rules (same machine: stacks must use network name meteorcloud):"
	@echo "  Control plane alone: API works; MQTT/ping do not; console can attach if VITE_API_BASE_URL points at that API."
	@echo "  Data plane alone: broker listens; device connect fails until control plane auth is reachable."
	@echo "  Console alone: static UI; unusable until a control plane is reachable at the configured API base URL."
	@echo "  Website alone: landing and docs; no control-plane URL required."
	@echo "  Together: DATA_PLANE_URL and CONTROL_PLANE_URL use Compose service names, not localhost."
	@echo "  Console: VITE_API_BASE_URL=http://localhost:8000 in local browser. Never a data-plane host."
	@echo "  Host-only: DATA_PLANE_URL=http://127.0.0.1:8081 and CONTROL_PLANE_URL=http://127.0.0.1:8000."

dev: ## Start control-plane, data-plane, console, and website
	@test -f .env || cp .env.example .env
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	@test -f $(CONSOLE_DIR)/package.json || (echo "Console source missing. Run: make checkout-ui" && exit 1)
	@test -f $(WEBSITE_DIR)/package.json || (echo "Website source missing. Run: make checkout-ui" && exit 1)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Development stack is starting:"
	@echo "  Website:      http://localhost:3000"
	@echo "  Console:      http://localhost:5173"
	@echo "  Control plane: http://localhost:8000"
	@echo "  Data plane:   http://localhost:8081/health"
	@echo "  OpenAPI:      http://localhost:8000/docs"
	@echo "  MQTT TLS:     mqtts://localhost:8883"
	@echo "  EMQX UI:      http://localhost:18083  (admin / public)"
	@echo "  Seed:         make seed"
	@$(MAKE) --no-print-directory join-help

dev-control-plane: ## Start postgres, redis, and the control-plane API
	@test -f .env || cp .env.example .env
	$(CP_COMPOSE) up --build -d
	@echo "Control plane: http://localhost:8000  (MQTT/ping need the data plane on network meteorcloud)"

dev-data-plane: ## Start EMQX and the data-plane MQTT gateway
	@test -f .env || cp .env.example .env
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	$(DP_COMPOSE) up --build -d
	@echo "Data plane: http://localhost:8081/health  MQTT: mqtts://localhost:8883"

dev-console: ## Start the operator console
	@test -f .env || cp .env.example .env
	@test -f $(CONSOLE_DIR)/package.json || (echo "Console source missing. Run: make checkout-ui" && exit 1)
	$(CONSOLE_COMPOSE) up --build -d
	@echo "Console: http://localhost:5173  (needs VITE_API_BASE_URL pointing at a control plane)"

dev-website: ## Start the public website and docs
	@test -f .env || cp .env.example .env
	@test -f $(WEBSITE_DIR)/package.json || (echo "Website source missing. Run: make checkout-ui" && exit 1)
	$(WEBSITE_COMPOSE) up --build -d
	@echo "Website: http://localhost:3000"

checkout-ui: ## Copy console and website from the private meteor-ui repo
	chmod +x scripts/checkout-ui.sh
	./scripts/checkout-ui.sh

mqtt-certs: ## Generate local MQTT CA and broker certificates
	./scripts/generate-local-mqtt-certs.sh
	-$(COMPOSE) restart emqx

test-mqtt: ## Start local Compose MQTT stack and run live MQTT tests
	chmod +x scripts/test-mqtt.sh
	./scripts/test-mqtt.sh

test-cloud-e2e: ## Terraform+Ansible AWS deploy, same MQTT tests, always destroy
	chmod +x scripts/test-cloud-e2e.sh
	./scripts/test-cloud-e2e.sh

observability: ## Start the development stack plus Prometheus, Loki, and Grafana
	@test -f .env || cp .env.example .env
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	@test -f $(CONSOLE_DIR)/package.json || (echo "Console source missing. Run: make checkout-ui" && exit 1)
	@test -f $(WEBSITE_DIR)/package.json || (echo "Website source missing. Run: make checkout-ui" && exit 1)
	$(OBS_COMPOSE) up --build -d
	@echo ""
	@echo "Observability stack is starting:"
	@echo "  App:        http://localhost:5173"
	@echo "  Website:    http://localhost:3000"
	@echo "  API:        http://localhost:8000"
	@echo "  Metrics:    http://localhost:8000/metrics"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3001  (set GRAFANA_ADMIN_USER/PASSWORD)"

stop: ## Stop the full development stack
	$(COMPOSE) down
	docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.observability.yml down 2>/dev/null

stop-control-plane: ## Stop the control-plane Compose project
	$(CP_COMPOSE) down

stop-data-plane: ## Stop the data-plane Compose project
	$(DP_COMPOSE) down

stop-console: ## Stop the console Compose project
	$(CONSOLE_COMPOSE) down

stop-website: ## Stop the website Compose project
	$(WEBSITE_COMPOSE) down

up: ## Deploy all enabled AWS services (Terraform + Ansible)
	@test -f $(CONFIG) || (echo "Missing $(CONFIG). Copy from infrastructure/installer/edge_installer/config/examples/installation.yaml" && exit 1)
	cd $(INSTALLER_DIR) && edge-installer apply $(CONFIG)

down: ## Destroy the installation
	cd $(INSTALLER_DIR) && edge-installer destroy $(CONFIG) --yes

plan: ## Preview infrastructure changes for enabled services
	cd $(INSTALLER_DIR) && edge-installer plan $(CONFIG)

status-aws: ## Show AWS installation status and health
	cd $(INSTALLER_DIR) && edge-installer status $(CONFIG)

logs: ## Tail development stack logs
	$(COMPOSE) logs -f

migrate: ## Run database migrations
	cd $(BACKEND_DIR) && alembic upgrade head

seed: ## Seed development users and organization
	cd $(BACKEND_DIR) && python scripts/seed.py

install-backend: ## Install control-plane Python dependencies
	cd $(BACKEND_DIR) && python -m pip install -e ".[dev]"

install-data-plane: ## Install data-plane Python dependencies
	cd $(DATA_PLANE_DIR) && python -m pip install -e ".[dev]"

install-console: ## Install console Node dependencies
	@test -f $(CONSOLE_DIR)/package.json || (echo "Console source missing. Run: make checkout-ui" && exit 1)
	cd $(CONSOLE_DIR) && npm install

install-website: ## Install website Node dependencies
	@test -f $(WEBSITE_DIR)/package.json || (echo "Website source missing. Run: make checkout-ui" && exit 1)
	cd $(WEBSITE_DIR) && npm install

install-installer: ## Install installer Python dependencies
	cd $(INSTALLER_DIR) && python -m pip install -e ".[dev]"

install-agent: ## Install reference agent Python dependencies
	cd $(AGENT_DIR) && python -m pip install -e ".[dev]"

install: install-backend install-data-plane install-installer install-agent ## Install backend local dependencies
	@if [ -f $(CONSOLE_DIR)/package.json ]; then $(MAKE) install-console; else echo "skip console (make checkout-ui)"; fi
	@if [ -f $(WEBSITE_DIR)/package.json ]; then $(MAKE) install-website; else echo "skip website (make checkout-ui)"; fi

backend-test: ## Run control-plane tests (dedicated *_test database, never the app DB)
	cd $(BACKEND_DIR) && python -m pytest -q

data-plane-test: ## Run data-plane tests
	cd $(DATA_PLANE_DIR) && python -m pytest -q

console-test: ## Run console tests
	@test -f $(CONSOLE_DIR)/package.json || (echo "Console source missing. Run: make checkout-ui" && exit 1)
	cd $(CONSOLE_DIR) && npm test -- --run

agent-test: ## Run reference agent tests
	cd $(AGENT_DIR) && python -m pytest -q

test: ## Run all tests
	@echo "==> Installer tests"
	cd $(INSTALLER_DIR) && python -m pytest -q
	@echo "==> Control-plane tests"
	cd $(BACKEND_DIR) && python -m pytest -q
	@echo "==> Data-plane tests"
	cd $(DATA_PLANE_DIR) && python -m pytest -q
	@echo "==> Agent tests"
	cd $(AGENT_DIR) && python -m pytest -q
	@if [ -f $(CONSOLE_DIR)/package.json ]; then \
		echo "==> Console tests"; \
		cd $(CONSOLE_DIR) && npm test -- --run; \
	else \
		echo "==> Console tests skipped (make checkout-ui)"; \
	fi

lint: ## Lint all projects
	@echo "==> Installer lint"
	cd $(INSTALLER_DIR) && python -m ruff check .
	@echo "==> Control-plane lint"
	cd $(BACKEND_DIR) && python -m ruff check .
	@echo "==> Data-plane lint"
	cd $(DATA_PLANE_DIR) && python -m ruff check .
	@echo "==> Agent lint"
	cd $(AGENT_DIR) && python -m ruff check .
	@if [ -f $(CONSOLE_DIR)/package.json ]; then echo "==> Console lint"; cd $(CONSOLE_DIR) && npm run lint; else echo "==> Console lint skipped"; fi
	@if [ -f $(WEBSITE_DIR)/package.json ]; then echo "==> Website lint"; cd $(WEBSITE_DIR) && npm run lint; else echo "==> Website lint skipped"; fi

format: ## Format all projects
	@echo "==> Installer format"
	cd $(INSTALLER_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Control-plane format"
	cd $(BACKEND_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Data-plane format"
	cd $(DATA_PLANE_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Agent format"
	cd $(AGENT_DIR) && python -m ruff format . && python -m ruff check --fix .
	@if [ -f $(CONSOLE_DIR)/package.json ]; then echo "==> Console format"; cd $(CONSOLE_DIR) && npm run format; else echo "==> Console format skipped"; fi
	@if [ -f $(WEBSITE_DIR)/package.json ]; then echo "==> Website format"; cd $(WEBSITE_DIR) && npm run lint -- --fix; else echo "==> Website format skipped"; fi

typecheck: ## Run static type checks where configured
	@echo "==> Control-plane typecheck (compileall)"
	cd $(BACKEND_DIR) && python -m compileall app tests scripts
	@echo "==> Data-plane typecheck (compileall)"
	cd $(DATA_PLANE_DIR) && python -m compileall data_plane tests
	@echo "==> Installer typecheck (compileall)"
	cd $(INSTALLER_DIR) && python -m compileall edge_installer tests
	@echo "==> Agent typecheck (compileall)"
	cd $(AGENT_DIR) && python -m compileall meteorcli edge_agent tests

installer-test: ## Run installer tests only
	cd $(INSTALLER_DIR) && python -m pytest -q

terraform-check: ## Validate Terraform formatting and syntax
	rm -rf $(INFRA_DIR)/terraform/aws/modules
	cp -r $(INFRA_DIR)/terraform/modules $(INFRA_DIR)/terraform/aws/modules
	cd $(INFRA_DIR)/terraform/aws && terraform fmt -check
	cd $(INFRA_DIR)/terraform/aws && terraform init -backend=false -input=false
	cd $(INFRA_DIR)/terraform/aws && terraform validate
	rm -rf $(INFRA_DIR)/terraform/aws/modules
	rm -rf $(INFRA_DIR)/terraform/gcp/modules
	mkdir -p $(INFRA_DIR)/terraform/gcp/modules
	cp -r $(INFRA_DIR)/terraform/modules/gcp_cloud_run $(INFRA_DIR)/terraform/gcp/modules/gcp_cloud_run
	cd $(INFRA_DIR)/terraform/gcp && terraform fmt -check
	cd $(INFRA_DIR)/terraform/gcp && terraform init -backend=false -input=false
	cd $(INFRA_DIR)/terraform/gcp && terraform validate
	cd $(INFRA_DIR)/terraform/modules/gcp_cloud_run && terraform fmt -check
	rm -rf $(INFRA_DIR)/terraform/gcp/modules

ansible-check: ## Run Ansible syntax checks
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/site.yml
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/provision.yml
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/deploy.yml
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/services/cloud_app.yml
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/services/vpn.yml
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/upgrade.yml
	cd $(INFRA_DIR)/ansible && ansible-playbook --syntax-check playbooks/destroy.yml

installer-validate: ## Validate installer configuration
	cd $(INSTALLER_DIR) && edge-installer validate $(CONFIG)

installer-plan: ## Plan infrastructure and deployment
	cd $(INSTALLER_DIR) && edge-installer plan $(CONFIG)

installer-apply: ## Apply infrastructure and deploy platform
	cd $(INSTALLER_DIR) && edge-installer apply $(CONFIG)

clean: ## Remove local build artifacts
	$(COMPOSE) down -v --remove-orphans || true
	$(CP_COMPOSE) down -v --remove-orphans || true
	$(DP_COMPOSE) down -v --remove-orphans || true
	$(CONSOLE_COMPOSE) down -v --remove-orphans || true
	$(WEBSITE_COMPOSE) down -v --remove-orphans || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(CONSOLE_DIR)/node_modules $(CONSOLE_DIR)/dist $(CONSOLE_DIR)/coverage
	rm -rf $(WEBSITE_DIR)/node_modules $(WEBSITE_DIR)/.next

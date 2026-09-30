.PHONY: help dev stop up down plan status-aws test lint format typecheck install-backend install-frontend install-installer install-agent clean migrate seed backend-test frontend-test agent-test installer-test terraform-check ansible-check observability mqtt-certs test-mqtt test-cloud-e2e

COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml
OBS_COMPOSE := $(COMPOSE) -f docker-compose.observability.yml
BACKEND_DIR := platform/backend
FRONTEND_DIR := platform/frontend
INSTALLER_DIR := installer
AGENT_DIR := edge-agent
INFRA_DIR := infrastructure
CONFIG ?= installation.yaml

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev: ## Start the development stack
	@test -f .env || cp .env.example .env
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Development stack is starting:"
	@echo "  Backend:  http://localhost:8000"
	@echo "  Frontend: http://localhost:5173"
	@echo "  API docs: http://localhost:8000/docs"
	@echo "  Health:   http://localhost:8000/health"
	@echo "  MQTT TLS: mqtts://localhost:8883"
	@echo "  EMQX UI:  http://localhost:18083  (admin / public)"
	@echo "  Seed:     make seed"

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
	$(OBS_COMPOSE) up --build -d
	@echo ""
	@echo "Observability stack is starting:"
	@echo "  App:        http://localhost:5173"
	@echo "  API:        http://localhost:8000"
	@echo "  Metrics:    http://localhost:8000/metrics"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3001  (set GRAFANA_ADMIN_USER/PASSWORD)"

stop: ## Stop the development stack
	$(COMPOSE) down
	docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.observability.yml down 2>/dev/null

up: ## Deploy enabled services (AWS: Terraform+Ansible, GCP: Cloud Run Terraform)
	@test -f $(CONFIG) || (echo "Missing $(CONFIG). Copy from installer/edge_installer/config/examples/installation.yaml" && exit 1)
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

install-backend: ## Install backend Python dependencies
	cd $(BACKEND_DIR) && python -m pip install -e ".[dev]"

install-frontend: ## Install frontend Node dependencies
	cd $(FRONTEND_DIR) && npm install

install-installer: ## Install installer Python dependencies
	cd $(INSTALLER_DIR) && python -m pip install -e ".[dev]"

install-agent: ## Install reference agent Python dependencies
	cd $(AGENT_DIR) && python -m pip install -e ".[dev]"

install: install-backend install-frontend install-installer install-agent ## Install all local dependencies

backend-test: ## Run backend tests (dedicated *_test database, never the app DB)
	cd $(BACKEND_DIR) && python -m pytest -q

frontend-test: ## Run frontend tests
	cd $(FRONTEND_DIR) && npm test -- --run

agent-test: ## Run reference agent tests
	cd $(AGENT_DIR) && python -m pytest -q

test: ## Run all tests
	@echo "==> Installer tests"
	cd $(INSTALLER_DIR) && python -m pytest -q
	@echo "==> Backend tests"
	cd $(BACKEND_DIR) && python -m pytest -q
	@echo "==> Agent tests"
	cd $(AGENT_DIR) && python -m pytest -q
	@echo "==> Frontend tests"
	cd $(FRONTEND_DIR) && npm test -- --run

lint: ## Lint all projects
	@echo "==> Installer lint"
	cd $(INSTALLER_DIR) && python -m ruff check .
	@echo "==> Backend lint"
	cd $(BACKEND_DIR) && python -m ruff check .
	@echo "==> Agent lint"
	cd $(AGENT_DIR) && python -m ruff check .
	@echo "==> Frontend lint"
	cd $(FRONTEND_DIR) && npm run lint

format: ## Format all projects
	@echo "==> Installer format"
	cd $(INSTALLER_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Backend format"
	cd $(BACKEND_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Agent format"
	cd $(AGENT_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Frontend format"
	cd $(FRONTEND_DIR) && npm run format

typecheck: ## Run static type checks where configured
	@echo "==> Backend typecheck (compileall)"
	cd $(BACKEND_DIR) && python -m compileall app tests scripts
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
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/dist $(FRONTEND_DIR)/coverage

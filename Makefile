.PHONY: help join-help dev stop up down plan status-aws logs test lint format typecheck install install-backend install-data-plane install-console install-website install-installer install-agent clean migrate seed backend-test data-plane-test console-test agent-test installer-test terraform-check ansible-check ansible-lint compose-config compose-smoke helm-lint images smoke observability mqtt-certs test-mqtt test-cloud-e2e dev-control-plane dev-data-plane dev-console dev-website stop-control-plane stop-data-plane stop-console stop-website k8s-up k8s-deploy k8s-status k8s-test k8s-down

COMPOSE_DIR := deploy/compose
ENV_FILE := $(COMPOSE_DIR)/.env
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.dev.yml
OBS_COMPOSE := $(COMPOSE) -f $(COMPOSE_DIR)/docker-compose.observability.yml
CP_COMPOSE := docker compose -p meteorcloud-cp -f $(COMPOSE_DIR)/control-plane.yml
DP_COMPOSE := docker compose -p meteorcloud-dp -f $(COMPOSE_DIR)/data-plane.yml
CONSOLE_COMPOSE := docker compose -p meteorcloud-console -f $(COMPOSE_DIR)/console.yml
BACKEND_DIR := src/control-plane
DATA_PLANE_DIR := src/data-plane
CONSOLE_DIR := console
INSTALLER_DIR := infrastructure/installer
AGENT_DIR := src/device-plane/agent
INFRA_DIR := infrastructure
ANSIBLE_DIR := deploy/ansible
ANSIBLE_PLAYBOOKS := site.yml provision.yml deploy.yml upgrade.yml destroy.yml services/cloud_app.yml services/vpn.yml
CHART := deploy/kubernetes/helm/meteorcloud
CONFIG ?= installation.yaml

# Local Kubernetes (k3d). The cluster is a development tool only.
K8S_CLUSTER ?= meteorcloud
K8S_NAMESPACE ?= meteorcloud
K8S_RELEASE ?= meteorcloud
K8S_HTTP_PORT ?= 8088
K8S_MQTT_PORT ?= 18883
K8S_CERTS_DIR := .k8s/certs
IMAGE_TAG ?= dev

help: ## Show available commands
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@$(MAKE) --no-print-directory join-help

join-help:
	@echo ""
	@echo "Join rules (same machine: stacks must use network name meteorcloud):"
	@echo "  Control plane alone: API works; MQTT/ping do not; console can attach if VITE_API_BASE_URL points at that API."
	@echo "  Data plane alone: broker listens; device connect fails until control plane auth is reachable."
	@echo "  Console alone: static UI; unusable until a control plane is reachable at the configured API base URL."
	@echo "  Website: clone meteor-ui and run make dev-website there (not this tree)."
	@echo "  Together: DATA_PLANE_URL and CONTROL_PLANE_URL use Compose service names, not localhost."
	@echo "  Console: VITE_API_BASE_URL=http://localhost:8000 in local browser. Never a data-plane host."
	@echo "  Host-only: DATA_PLANE_URL=http://127.0.0.1:8081 and CONTROL_PLANE_URL=http://127.0.0.1:8000."

dev: ## Start control-plane, data-plane, and console
	@test -f $(ENV_FILE) || cp $(COMPOSE_DIR)/.env.example $(ENV_FILE)
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Development stack is starting:"
	@echo "  Console:      http://localhost:5173"
	@echo "  Control plane: http://localhost:8000"
	@echo "  Data plane:   http://localhost:8081/health"
	@echo "  OpenAPI:      http://localhost:8000/docs"
	@echo "  MQTT TLS:     mqtts://localhost:8883"
	@echo "  EMQX UI:      http://localhost:18083  (admin / public)"
	@echo "  MinIO API:    http://localhost:9000   (S3 endpoint for artifacts)"
	@echo "  MinIO UI:     http://localhost:9001   (meteorcloud / meteorcloud-dev-secret)"
	@echo "  Seed:         make seed"
	@$(MAKE) --no-print-directory join-help

dev-control-plane: ## Start postgres, redis, and the control-plane API
	@test -f $(ENV_FILE) || cp $(COMPOSE_DIR)/.env.example $(ENV_FILE)
	$(CP_COMPOSE) up --build -d
	@echo "Control plane: http://localhost:8000  (MQTT/ping need the data plane on network meteorcloud)"
	@echo "MinIO UI:      http://localhost:9001  (meteorcloud / meteorcloud-dev-secret)"

dev-data-plane: ## Start EMQX and the data-plane MQTT gateway
	@test -f $(ENV_FILE) || cp $(COMPOSE_DIR)/.env.example $(ENV_FILE)
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	$(DP_COMPOSE) up --build -d
	@echo "Data plane: http://localhost:8081/health  MQTT: mqtts://localhost:8883"

dev-console: ## Start the operator console
	@test -f $(ENV_FILE) || cp $(COMPOSE_DIR)/.env.example $(ENV_FILE)
	$(CONSOLE_COMPOSE) up --build -d
	@echo "Console: http://localhost:5173  (needs VITE_API_BASE_URL pointing at a control plane)"

dev-website: ## Website is not in this repo; clone meteor-ui
	@echo "The public website is not in this tree."
	@echo "  git clone git@github.com:meteor-edge/meteor-ui.git"
	@echo "  cd meteor-ui && make dev-website"

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
	@test -f $(ENV_FILE) || cp $(COMPOSE_DIR)/.env.example $(ENV_FILE)
	@test -f certs/server.crt || ./scripts/generate-local-mqtt-certs.sh
	$(OBS_COMPOSE) up --build -d
	@echo ""
	@echo "Observability stack is starting:"
	@echo "  App:        http://localhost:5173"
	@echo "  API:        http://localhost:8000"
	@echo "  Metrics:    http://localhost:8000/metrics"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3001  (set GRAFANA_ADMIN_USER/PASSWORD)"

stop: ## Stop the full development stack
	$(OBS_COMPOSE) down 2>/dev/null || $(COMPOSE) down

stop-control-plane: ## Stop the control-plane Compose project
	$(CP_COMPOSE) down

stop-data-plane: ## Stop the data-plane Compose project
	$(DP_COMPOSE) down

stop-console: ## Stop the console Compose project
	$(CONSOLE_COMPOSE) down

stop-website: ## Website is not in this repo
	@echo "The public website is not in this tree. From a meteor-ui clone: make stop-website"

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
	cd $(CONSOLE_DIR) && npm install

install-website: ## Website lives in meteor-ui, not this tree
	@echo "The public website is not in this tree. Clone meteor-ui and run make install-website there."
	@exit 1

install-installer: ## Install installer Python dependencies
	cd $(INSTALLER_DIR) && python -m pip install -e ".[dev]"

install-agent: ## Install reference agent Python dependencies
	cd $(AGENT_DIR) && python -m pip install -e ".[dev]"

install: install-backend install-data-plane install-installer install-agent install-console ## Install local dependencies

backend-test: ## Run control-plane tests (dedicated *_test database, never the app DB)
	@python -c "import sqlalchemy" >/dev/null 2>&1 || (echo "Control-plane deps missing. Run: make install-backend" && exit 1)
	cd $(BACKEND_DIR) && python -m pytest -q

data-plane-test: ## Run data-plane tests
	@python -c "import paho.mqtt.client" >/dev/null 2>&1 || (echo "Data-plane deps missing. Run: make install-data-plane" && exit 1)
	cd $(DATA_PLANE_DIR) && python -m pytest -q

console-test: ## Run console tests
	@test -d $(CONSOLE_DIR)/node_modules || (echo "Console deps missing. Run: make install-console" && exit 1)
	cd $(CONSOLE_DIR) && npm test -- --run

agent-test: ## Run reference agent tests
	cd $(AGENT_DIR) && python -m pytest -q

test: ## Run all tests
	@echo "==> Installer tests"
	cd $(INSTALLER_DIR) && python -m pytest -q
	@echo "==> Control-plane tests"
	@python -c "import sqlalchemy" >/dev/null 2>&1 || (echo "Control-plane deps missing. Run: make install-backend" && exit 1)
	cd $(BACKEND_DIR) && python -m pytest -q
	@echo "==> Data-plane tests"
	@python -c "import paho.mqtt.client" >/dev/null 2>&1 || (echo "Data-plane deps missing. Run: make install-data-plane" && exit 1)
	cd $(DATA_PLANE_DIR) && python -m pytest -q
	@echo "==> Agent tests"
	cd $(AGENT_DIR) && python -m pytest -q
	@echo "==> Console tests"
	@test -d $(CONSOLE_DIR)/node_modules || (echo "Console deps missing. Run: make install-console" && exit 1)
	cd $(CONSOLE_DIR) && npm test -- --run

lint: ## Lint all projects
	@echo "==> Installer lint"
	cd $(INSTALLER_DIR) && python -m ruff check .
	@echo "==> Control-plane lint"
	cd $(BACKEND_DIR) && python -m ruff check .
	@echo "==> Data-plane lint"
	cd $(DATA_PLANE_DIR) && python -m ruff check .
	@echo "==> Agent lint"
	cd $(AGENT_DIR) && python -m ruff check .
	@echo "==> Console lint"
	cd $(CONSOLE_DIR) && npm run lint

format: ## Format all projects
	@echo "==> Installer format"
	cd $(INSTALLER_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Control-plane format"
	cd $(BACKEND_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Data-plane format"
	cd $(DATA_PLANE_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Agent format"
	cd $(AGENT_DIR) && python -m ruff format . && python -m ruff check --fix .
	@echo "==> Console format"
	cd $(CONSOLE_DIR) && npm run format

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

terraform-check: ## Validate Terraform formatting and syntax (no cloud credentials)
	cd $(INFRA_DIR)/terraform && terraform fmt -check -recursive
	cd $(INFRA_DIR)/terraform/aws && terraform init -backend=false -input=false
	cd $(INFRA_DIR)/terraform/aws && terraform validate

ansible-check: ## Syntax-check every Ansible playbook
	cd $(ANSIBLE_DIR) && for p in $(ANSIBLE_PLAYBOOKS); do \
		ansible-playbook -i inventory/example/hosts.yml --syntax-check playbooks/$$p || exit 1; done

ansible-lint: ## Run ansible-lint (production profile)
	cd $(ANSIBLE_DIR) && ansible-lint

compose-config: ## Validate the Compose files and overlays
	@test -f $(ENV_FILE) || cp $(COMPOSE_DIR)/.env.example $(ENV_FILE)
	docker compose -f $(COMPOSE_DIR)/docker-compose.yml config --quiet
	$(COMPOSE) config --quiet
	docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.prod.yml config --quiet
	GRAFANA_ADMIN_PASSWORD=check docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.prod.yml \
		-f $(COMPOSE_DIR)/docker-compose.observability.yml config --quiet

compose-smoke: ## Start the minimal production Compose stack, smoke-test it, remove it
	IMAGE_TAG=$(IMAGE_TAG) ./scripts/compose-smoke.sh

helm-lint: ## Lint the Helm chart and render it with every values file
	helm lint --strict $(CHART)
	for f in values-local.yaml values-ci.yaml values-production.yaml; do \
		helm lint --strict $(CHART) -f $(CHART)/$$f && \
		helm template meteorcloud $(CHART) -f $(CHART)/$$f > /dev/null || exit 1; done

images: ## Build the backend, data-plane, and console images (IMAGE_TAG=dev)
	docker build -t meteorcloud/backend:$(IMAGE_TAG) $(BACKEND_DIR)
	docker build -t meteorcloud/data-plane:$(IMAGE_TAG) $(DATA_PLANE_DIR)
	docker build -t meteorcloud/console:$(IMAGE_TAG) $(CONSOLE_DIR)

smoke: ## End-to-end HTTP smoke test (URL=http://localhost:8088)
	python3 scripts/smoke_test.py $(or $(URL),http://localhost:$(K8S_HTTP_PORT))

k8s-up: ## Create a local k3d cluster (ingress on localhost:8088, MQTT on 18883)
	@command -v k3d >/dev/null || (echo "k3d is required: https://k3d.io" && exit 1)
	@k3d cluster list $(K8S_CLUSTER) >/dev/null 2>&1 && echo "Cluster $(K8S_CLUSTER) already exists." || \
		k3d cluster create $(K8S_CLUSTER) --agents 0 --wait \
			-p "$(K8S_HTTP_PORT):80@loadbalancer" -p "$(K8S_MQTT_PORT):8883@loadbalancer"
	kubectl config use-context k3d-$(K8S_CLUSTER)

k8s-deploy: images ## Build images, import them into k3d, and helm upgrade --install
	k3d image import -c $(K8S_CLUSTER) meteorcloud/backend:$(IMAGE_TAG) meteorcloud/data-plane:$(IMAGE_TAG) meteorcloud/console:$(IMAGE_TAG)
	kubectl --context k3d-$(K8S_CLUSTER) create namespace $(K8S_NAMESPACE) --dry-run=client -o yaml | kubectl --context k3d-$(K8S_CLUSTER) apply -f -
	@test -f $(K8S_CERTS_DIR)/server.crt || MQTT_CERTS_SKIP_CHOWN=1 MQTT_PUBLIC_HOST=localhost \
		MQTT_EXTRA_SANS=$(K8S_RELEASE)-emqx,$(K8S_RELEASE)-emqx.$(K8S_NAMESPACE).svc \
		./scripts/generate-local-mqtt-certs.sh $(K8S_CERTS_DIR)
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) create secret generic meteorcloud-mqtt-tls \
		--from-file=ca.crt=$(K8S_CERTS_DIR)/ca.crt --from-file=server.crt=$(K8S_CERTS_DIR)/server.crt \
		--from-file=server.key=$(K8S_CERTS_DIR)/server.key --dry-run=client -o yaml | kubectl --context k3d-$(K8S_CLUSTER) apply -f -
	helm upgrade --install $(K8S_RELEASE) $(CHART) --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) \
		-f $(CHART)/values-local.yaml --set backend.image.tag=$(IMAGE_TAG) --set console.image.tag=$(IMAGE_TAG) \
		--set dataPlane.image.tag=$(IMAGE_TAG) --set config.publicUrl=http://localhost:$(K8S_HTTP_PORT) \
		--wait --timeout 10m
	# Restart so pods pick up images re-imported under the same tag.
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) rollout restart deployment
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) rollout status deployment --timeout 5m
	@echo ""
	@echo "Console and API: http://localhost:$(K8S_HTTP_PORT)  (admin@meteorcloud.local / LocalAdmin123!)"
	@echo "MQTT TLS:        mqtts://localhost:$(K8S_MQTT_PORT)  (CA: $(K8S_CERTS_DIR)/ca.crt)"

k8s-status: ## Show MeteorCloud pods, services, ingress, and volumes in k3d
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) get pods,services,ingress,pvc
	helm --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) status $(K8S_RELEASE)

k8s-test: ## Run helm test and the HTTP smoke test against k3d
	helm --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) test $(K8S_RELEASE) --logs
	python3 scripts/smoke_test.py http://localhost:$(K8S_HTTP_PORT)

k8s-down: ## Delete the local k3d cluster (and everything in it)
	k3d cluster delete $(K8S_CLUSTER)

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
	rm -rf .k8s
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(CONSOLE_DIR)/node_modules $(CONSOLE_DIR)/dist $(CONSOLE_DIR)/coverage

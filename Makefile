.PHONY: help join-help dev stop up down plan status-aws logs test lint format typecheck install install-backend install-data-plane install-console install-website install-installer install-agent clean migrate seed backend-test data-plane-test data-api-test sdk-test console-test agent-test installer-test terraform-check ansible-check ansible-lint compose-config compose-smoke helm-lint images smoke observability observability-check mqtt-certs test-mqtt test-cloud-e2e dev-control-plane dev-data-plane dev-console dev-website stop-control-plane stop-data-plane stop-console stop-website k8s-up k8s-deploy k8s-status k8s-test k8s-down e2e-check e2e-up e2e-test e2e-down install-selfhost aws-up aws-down check-plane-imports agent-dist

COMPOSE_DIR := deploy/compose
ENV_FILE := $(COMPOSE_DIR)/.env
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.dev.yml
OBS_COMPOSE := $(COMPOSE) -f $(COMPOSE_DIR)/docker-compose.observability.yml
# make dev OBS=1 also starts the observability stack (same as make observability).
OBS ?= 0
# Observability components to run (Compose profiles); drop any to turn that component off.
OBS_PROFILES ?= metrics,logs,alerting,dashboards
# Profiles already chosen in .env (postgres, mqtt, ...), kept when observability profiles are added.
ENV_PROFILES = $(shell sed -n 's/^COMPOSE_PROFILES=//p' $(ENV_FILE) 2>/dev/null | tail -1)
OBS_IMAGES_PROMETHEUS := prom/prometheus:v2.55.1
OBS_IMAGES_LOKI := grafana/loki:3.3.2
OBS_IMAGES_ALLOY := grafana/alloy:v1.8.3
OBS_IMAGES_ALERTMANAGER := prom/alertmanager:v0.27.0
CP_COMPOSE := docker compose -p meteorcloud-cp -f $(COMPOSE_DIR)/control-plane.yml
DP_COMPOSE := docker compose -p meteorcloud-dp -f $(COMPOSE_DIR)/data-plane.yml
CONSOLE_COMPOSE := docker compose -p meteorcloud-console -f $(COMPOSE_DIR)/console.yml
BACKEND_DIR := src/control-plane
DATA_PLANE_DIR := src/data-plane
DATA_API_DIR := src/data-plane/go
SDK_DIR := sdk/python
CONSOLE_DIR := console
INSTALLER_DIR := infrastructure/installer
AGENT_DIR := src/device-plane/agent
INFRA_DIR := infrastructure
ANSIBLE_DIR := deploy/ansible
ANSIBLE_PLAYBOOKS := site.yml provision.yml deploy.yml upgrade.yml destroy.yml services/cloud_app.yml services/vpn.yml wireguard.yml device.yml
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
# make k8s-deploy OBS=1: kube-prometheus-stack (Apache-2.0; bundles Grafana, AGPLv3) in k3d only.
K8S_OBS_NAMESPACE ?= monitoring
K8S_OBS_CHART_VERSION := 93.1.1

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

dev: ## Start control-plane, data-plane, and console (OBS=1 adds Prometheus, Loki, Grafana)
ifeq ($(OBS),1)
	@$(MAKE) --no-print-directory observability
else
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
	@echo "  Observability: make dev OBS=1  (Prometheus, Grafana, Loki; see docs/observability.md)"
	@$(MAKE) --no-print-directory join-help
endif

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
	@if echo ",$(OBS_PROFILES)," | grep -q ',dashboards,'; then \
		grep -qE '^[[:space:]]*GRAFANA_ADMIN_PASSWORD=[^[:space:]#]+' $(ENV_FILE) \
			|| { echo "Set GRAFANA_ADMIN_PASSWORD in $(ENV_FILE) (required for Grafana / dashboards)."; exit 1; }; \
	fi
	COMPOSE_PROFILES="$(ENV_PROFILES),$(OBS_PROFILES)" $(OBS_COMPOSE) up --build -d
	@echo ""
	@echo "Observability stack is starting (profiles: $(OBS_PROFILES)):"
	@echo "  App:        http://localhost:5173"
	@echo "  API:        http://localhost:8000"
	@echo "  Metrics:    http://localhost:8000/metrics"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://127.0.0.1:3001  (GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD from $(ENV_FILE))"

stop: ## Stop the full development stack (unified and split Compose projects)
	-GRAFANA_ADMIN_PASSWORD=$${GRAFANA_ADMIN_PASSWORD:-unused} $(OBS_COMPOSE) down --remove-orphans
	-$(COMPOSE) down --remove-orphans
	-$(CP_COMPOSE) down --remove-orphans
	-$(DP_COMPOSE) down --remove-orphans
	-$(CONSOLE_COMPOSE) down --remove-orphans

stop-control-plane: ## Stop the control-plane Compose project
	$(CP_COMPOSE) down

stop-data-plane: ## Stop the data-plane Compose project
	$(DP_COMPOSE) down

stop-console: ## Stop the console Compose project
	$(CONSOLE_COMPOSE) down

stop-website: ## Website is not in this repo
	@echo "The public website is not in this tree. From a meteor-ui clone: make stop-website"

up: ## Install on this VM (PLATFORM_DOMAIN=example.com)
	PLATFORM_DOMAIN="$(or $(PLATFORM_DOMAIN),$(DOMAIN))" $(ANSIBLE_DIR)/install-selfhost.sh $(ARGS)

install-selfhost: up

down: ## Stop VM install, or local Compose when no selfhost inventory (ARGS=--destroy-data wipes VM data)
	@if [ -f $(ANSIBLE_DIR)/inventory/selfhost/hosts.yml ]; then \
		$(ANSIBLE_DIR)/install-selfhost.sh --down $(ARGS); \
	else \
		echo "No selfhost inventory; stopping local development Compose (same as make stop)."; \
		$(MAKE) --no-print-directory stop; \
	fi

aws-up: ## Create a new AWS EC2 and deploy (Terraform + Ansible). ARGS='--route53' creates the DNS records, '--demo' seeds Demo Organization.
	@test -f $(CONFIG) || (echo "Missing $(CONFIG). Copy from infrastructure/installer/edge_installer/config/examples/installation.yaml" && exit 1)
	./scripts/ensure-platform-secrets.sh --run --report -- \
		sh -c 'cd "$(INSTALLER_DIR)" && exec edge-installer apply "$(abspath $(CONFIG))" $(ARGS)'

aws-down: ## Destroy the AWS installation
	cd $(INSTALLER_DIR) && edge-installer destroy $(abspath $(CONFIG)) --yes

plan: ## Preview infrastructure changes for enabled services
	@test -f $(CONFIG) || (echo "Missing $(CONFIG). Copy from infrastructure/installer/edge_installer/config/examples/installation.yaml" && exit 1)
	./scripts/ensure-platform-secrets.sh --run -- \
		sh -c 'cd "$(INSTALLER_DIR)" && edge-installer plan "$(abspath $(CONFIG))"'

status-aws: ## Show AWS installation status and health
	cd $(INSTALLER_DIR) && edge-installer status $(abspath $(CONFIG))

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

data-api-test: ## Run application data plane (Go) tests; DATAPLANE_TEST_DATABASE_URL and DATAPLANE_TEST_CLICKHOUSE_URL add the PostgreSQL and ClickHouse suites
	cd $(DATA_API_DIR) && go vet ./... && go test -race ./...

sdk-test: ## Run Python SDK tests against the real data plane binary (needs Go)
	cd $(SDK_DIR) && python -m pytest -q

console-test: ## Run console tests
	@test -d $(CONSOLE_DIR)/node_modules || (echo "Console deps missing. Run: make install-console" && exit 1)
	cd $(CONSOLE_DIR) && npm test -- --run

agent-test: ## Run reference agent tests
	cd $(AGENT_DIR) && python -m pytest -q

agent-dist: ## Pack meteorcli (agent + runtime) into dist/ for a GitHub Release
	./scripts/agent-dist.sh

check-plane-imports: ## Fail if planes import each other's private Python packages
	python scripts/check_plane_imports.py
	python -m pytest -q tests/architecture

test: ## Run all tests
	@echo "==> Plane import boundaries"
	@$(MAKE) --no-print-directory check-plane-imports
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
	@echo "==> SDK lint"
	cd $(SDK_DIR) && python -m ruff check .
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

E2E_DIR := tests/e2e

e2e-check: ## Full-stack E2E tooling checks (unit tests, Terraform validate, shellcheck; no credentials)
	cd $(E2E_DIR) && python -m pytest -q tests
	cd $(E2E_DIR)/terraform && terraform fmt -check -recursive
	for root in aws onprem; do \
		(cd $(E2E_DIR)/terraform/$$root && TF_DATA_DIR=.terraform-validate terraform init -backend=false -input=false >/dev/null \
		&& TF_DATA_DIR=.terraform-validate terraform validate) || exit 1; done
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck $(E2E_DIR)/meteorcloud-testenv $(E2E_DIR)/testapp/serve.sh; \
	else echo "shellcheck not installed; skipped"; fi

e2e-up: ## Create a full-stack E2E environment and run the suite (tests/e2e/.env)
	$(E2E_DIR)/meteorcloud-testenv up

e2e-test: ## Re-run the E2E suite against the existing environment
	$(E2E_DIR)/meteorcloud-testenv test

e2e-down: ## Destroy the E2E environment (only its tracked resources)
	$(E2E_DIR)/meteorcloud-testenv down

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
	docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.prod.yml \
		-f $(COMPOSE_DIR)/docker-compose.vpn.yml config --quiet
	GRAFANA_ADMIN_PASSWORD=check docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.prod.yml \
		-f $(COMPOSE_DIR)/docker-compose.observability.yml config --quiet
	for p in metrics logs alerting dashboards metrics,logs,alerting,dashboards; do \
		COMPOSE_PROFILES=$$p GRAFANA_ADMIN_PASSWORD=check docker compose -f $(COMPOSE_DIR)/docker-compose.yml \
		-f $(COMPOSE_DIR)/docker-compose.prod.yml -f $(COMPOSE_DIR)/docker-compose.observability.yml \
		config --quiet || exit 1; done

observability-check: ## Validate Prometheus, Loki and Alertmanager config and alert rules (uses Docker)
	docker run --rm -v $(CURDIR)/$(COMPOSE_DIR)/config/observability:/cfg:ro \
		-v $(CURDIR)/$(COMPOSE_DIR)/config/observability/rules:/etc/prometheus/rules:ro --entrypoint promtool \
		$(OBS_IMAGES_PROMETHEUS) check config /cfg/prometheus.yml
	docker run --rm -v $(CURDIR)/$(COMPOSE_DIR)/config/observability:/cfg:ro -w /cfg/rules --entrypoint promtool \
		$(OBS_IMAGES_PROMETHEUS) test rules tests/alerts_test.yml
	docker run --rm -v $(CURDIR)/$(COMPOSE_DIR)/config/observability:/cfg:ro \
		$(OBS_IMAGES_LOKI) -config.file=/cfg/loki.yml -config.expand-env=true -verify-config
	docker run --rm -v $(CURDIR)/$(COMPOSE_DIR)/config/observability:/cfg:ro \
		$(OBS_IMAGES_ALLOY) fmt /cfg/config.alloy >/dev/null
	docker run --rm -v $(CURDIR)/$(COMPOSE_DIR)/config/observability/alertmanager:/cfg:ro --entrypoint amtool \
		$(OBS_IMAGES_ALERTMANAGER) check-config /cfg/alertmanager.yml /cfg/alertmanager.webhook.yml /cfg/alertmanager.webhook-heartbeat.yml

compose-smoke: ## Start the minimal production Compose stack, smoke-test it, remove it
	IMAGE_TAG=$(IMAGE_TAG) ./scripts/compose-smoke.sh

# values-monitoring.yaml renders Prometheus Operator objects, so declare their API.
# helm lint cannot, so that overlay is only rendered (and must fail without the CRDs).
HELM_API_VERSIONS := --api-versions monitoring.coreos.com/v1

helm-lint: ## Lint the Helm chart and render it with every values file
	helm lint --strict $(CHART)
	for f in $(filter-out %/values-monitoring.yaml,$(wildcard $(CHART)/values-*.yaml)); do \
		helm lint --strict $(CHART) -f $$f && \
		helm template meteorcloud $(CHART) -f $$f > /dev/null || exit 1; done
	helm template meteorcloud $(CHART) $(HELM_API_VERSIONS) -f $(CHART)/values-ci.yaml \
		--set mqtt.enabled=true,mqtt.tls.existingSecret=ci,mqtt.publicHost=mqtt.example.com \
		-f $(CHART)/values-monitoring.yaml > /dev/null
	! helm template meteorcloud $(CHART) -f $(CHART)/values-monitoring.yaml > /dev/null 2>&1

images: ## Build the backend, data-plane, and console images (IMAGE_TAG=dev)
	docker build -t meteorcloud/backend:$(IMAGE_TAG) $(BACKEND_DIR)
	docker build -t meteorcloud/data-plane:$(IMAGE_TAG) $(DATA_PLANE_DIR)
	docker build -t meteorcloud/data-api:$(IMAGE_TAG) $(DATA_API_DIR)
	docker build -t meteorcloud/console:$(IMAGE_TAG) $(CONSOLE_DIR)

smoke: ## End-to-end HTTP smoke test (URL=http://localhost:8088)
	python3 scripts/smoke_test.py $(or $(URL),http://localhost:$(K8S_HTTP_PORT))

k8s-up: ## Create a local k3d cluster (ingress on localhost:8088, MQTT on 18883)
	@command -v k3d >/dev/null || (echo "k3d is required: https://k3d.io" && exit 1)
	@k3d cluster list $(K8S_CLUSTER) >/dev/null 2>&1 && echo "Cluster $(K8S_CLUSTER) already exists." || \
		k3d cluster create $(K8S_CLUSTER) --agents 0 --wait \
			-p "$(K8S_HTTP_PORT):80@loadbalancer" -p "$(K8S_MQTT_PORT):8883@loadbalancer"
	kubectl config use-context k3d-$(K8S_CLUSTER)

k8s-deploy: images ## Build images, import them into k3d, and helm upgrade --install (OBS=1 adds Prometheus, Grafana)
	k3d image import -c $(K8S_CLUSTER) meteorcloud/backend:$(IMAGE_TAG) meteorcloud/data-plane:$(IMAGE_TAG) meteorcloud/console:$(IMAGE_TAG)
	kubectl --context k3d-$(K8S_CLUSTER) create namespace $(K8S_NAMESPACE) --dry-run=client -o yaml | kubectl --context k3d-$(K8S_CLUSTER) apply -f -
	@test -f $(K8S_CERTS_DIR)/server.crt || MQTT_CERTS_SKIP_CHOWN=1 MQTT_PUBLIC_HOST=localhost \
		MQTT_EXTRA_SANS=$(K8S_RELEASE)-emqx,$(K8S_RELEASE)-emqx.$(K8S_NAMESPACE).svc \
		./scripts/generate-local-mqtt-certs.sh $(K8S_CERTS_DIR)
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) create secret generic meteorcloud-mqtt-tls \
		--from-file=ca.crt=$(K8S_CERTS_DIR)/ca.crt --from-file=server.crt=$(K8S_CERTS_DIR)/server.crt \
		--from-file=server.key=$(K8S_CERTS_DIR)/server.key --dry-run=client -o yaml | kubectl --context k3d-$(K8S_CLUSTER) apply -f -
ifeq ($(OBS),1)
	@test -n "$$GRAFANA_ADMIN_PASSWORD" || (echo "Set GRAFANA_ADMIN_PASSWORD for the k3d Grafana (OBS=1)." && exit 1)
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
	helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
		--version $(K8S_OBS_CHART_VERSION) --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_OBS_NAMESPACE) --create-namespace \
		-f deploy/kubernetes/k3d-monitoring.yaml --set grafana.adminPassword="$$GRAFANA_ADMIN_PASSWORD" \
		--wait --timeout 10m
endif
	helm upgrade --install $(K8S_RELEASE) $(CHART) --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) \
		-f $(CHART)/values-dev.yaml $(if $(filter 1,$(OBS)),-f $(CHART)/values-monitoring.yaml) --set backend.image.tag=$(IMAGE_TAG) --set console.image.tag=$(IMAGE_TAG) \
		--set dataPlane.image.tag=$(IMAGE_TAG) --set config.publicUrl=http://localhost:$(K8S_HTTP_PORT) \
		--wait --timeout 10m
	# Restart so pods pick up images re-imported under the same tag.
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) rollout restart deployment
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) rollout status deployment --timeout 5m
	@echo ""
	@echo "Console and API: http://localhost:$(K8S_HTTP_PORT)  (admin@meteorcloud.local / LocalAdmin123!)"
	@echo "MQTT TLS:        mqtts://localhost:$(K8S_MQTT_PORT)  (CA: $(K8S_CERTS_DIR)/ca.crt)"
ifeq ($(OBS),1)
	@echo "Grafana:         kubectl -n $(K8S_OBS_NAMESPACE) port-forward svc/kube-prometheus-stack-grafana 3001:80"
	@echo "Prometheus:      kubectl -n $(K8S_OBS_NAMESPACE) port-forward svc/kube-prometheus-stack-prometheus 9090:9090"
else
	@echo "Observability:   make k8s-deploy OBS=1  (kube-prometheus-stack in k3d)"
endif

k8s-status: ## Show MeteorCloud pods, services, ingress, and volumes in k3d
	kubectl --context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) get pods,services,ingress,pvc
	helm --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) status $(K8S_RELEASE)

k8s-test: ## Run helm test and the HTTP smoke test against k3d
	helm --kube-context k3d-$(K8S_CLUSTER) -n $(K8S_NAMESPACE) test $(K8S_RELEASE) --logs
	python3 scripts/smoke_test.py http://localhost:$(K8S_HTTP_PORT)

k8s-down: ## Delete the local k3d cluster (and everything in it)
	k3d cluster delete $(K8S_CLUSTER)

installer-validate: ## Validate installer configuration
	cd $(INSTALLER_DIR) && edge-installer validate $(abspath $(CONFIG))

installer-plan: ## Plan infrastructure and deployment
	cd $(INSTALLER_DIR) && edge-installer plan $(abspath $(CONFIG))

installer-apply: ## Apply infrastructure and deploy platform
	cd $(INSTALLER_DIR) && edge-installer apply $(abspath $(CONFIG))

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

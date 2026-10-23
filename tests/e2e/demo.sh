#!/usr/bin/env bash
# Build a MeteorCloud demo environment step by step, with every Terraform command visible.
#
#   tests/e2e/demo.sh up       1. Terraform: servers + DNS   2. Ansible: install   3. seed: demo data
#   tests/e2e/demo.sh status   URLs, logins, SSH commands
#   tests/e2e/demo.sh down     destroy every server and DNS record of this environment
#
# Settings come from tests/e2e/.env (E2E_ENV_NAME, E2E_DOMAIN, E2E_DNS_ZONE_ID, ...).
# Add --yes to skip the confirmation prompts.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTENV="${HERE}/meteorcloud-testenv"
ACTION="${1:-}"
YES="${2:-}"

if [[ -f "${HERE}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${HERE}/.env"
  set +a
fi

ENV_NAME="${E2E_ENV_NAME:?set E2E_ENV_NAME in tests/e2e/.env}"
REGION="${E2E_AWS_REGION:-eu-central-1}"
STATE="${HERE}/.state/${ENV_NAME}"
TF=(terraform "-chdir=${HERE}/terraform/aws")
VARS="-var-file=${STATE}/terraform.tfvars.json"
APPROVE=()
[[ "${YES}" == "--yes" ]] && APPROVE=(-auto-approve)

export TF_DATA_DIR="${STATE}/.terraform" AWS_REGION="${REGION}" TF_INPUT=0

step() { printf '\n==> %s\n' "$*"; }

write_tfvars() {
  local cidr="${E2E_ALLOWED_SSH_CIDRS:-}"
  if [[ -z "${cidr}" ]]; then
    cidr="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')/32"
  fi
  local cidrs
  # shellcheck disable=SC2086 # split the comma list into words
  cidrs="$(printf '"%s",' ${cidr//,/ })"
  umask 077
  cat > "${STATE}/terraform.tfvars.json" <<EOF
{
  "env_name": "${ENV_NAME}",
  "aws_region": "${REGION}",
  "ssh_public_key": "$(cat "${STATE}/id_ed25519.pub")",
  "allowed_ssh_cidrs": [${cidrs%,}],
  "vpn_placement": "${E2E_VPN_PLACEMENT:-same_host}",
  "device_count": ${E2E_DEVICE_COUNT:-2},
  "control_plane_instance_type": "${E2E_CONTROL_PLANE_INSTANCE_TYPE:-t3.medium}",
  "gateway_instance_type": "${E2E_GATEWAY_INSTANCE_TYPE:-t3.micro}",
  "device_instance_type": "${E2E_DEVICE_INSTANCE_TYPE:-t3.micro}",
  "dns_provider": "${E2E_DNS_PROVIDER:-route53}",
  "dns_zone_id": "${E2E_DNS_ZONE_ID:-}",
  "domain": "${E2E_DOMAIN:-}"
}
EOF
}

tf_init() {
  "${TF[@]}" init -input=false -reconfigure -backend-config="path=${STATE}/terraform.tfstate" >/dev/null
}

case "${ACTION}" in
  up)
    step "0/3 Checking settings, tools and AWS access"
    "${TESTENV}" validate

    step "1/3 Terraform: servers and DNS for ${ENV_NAME}.${E2E_DOMAIN:-<no domain>}"
    mkdir -p "${STATE}" && chmod 700 "${STATE}"
    [[ -f "${STATE}/id_ed25519" ]] || ssh-keygen -q -t ed25519 -N "" -C "meteorcloud-${ENV_NAME}" -f "${STATE}/id_ed25519"
    write_tfvars
    tf_init
    "${TF[@]}" plan "${VARS}"
    "${TF[@]}" apply "${VARS}" "${APPROVE[@]}"
    "${TF[@]}" output

    step "2/3 Ansible: installing MeteorCloud, WireGuard and meteorcli (~20 min)"
    "${TESTENV}" up --no-tests

    step "3/3 Seeding demo data: organization, account, devices, demo-app"
    "${TESTENV}" seed
    ;;

  status)
    "${TESTENV}" status
    [[ -f "${STATE}/demo-login.env" ]] && { echo; echo "Demo login (${STATE}/demo-login.env):"; cat "${STATE}/demo-login.env"; }
    ;;

  down)
    step "Destroying every server and DNS record of ${ENV_NAME}"
    if [[ ! -f "${STATE}/terraform.tfvars.json" ]]; then
      echo "nothing to destroy: ${STATE}/terraform.tfvars.json not found"
      exit 0
    fi
    tf_init
    "${TF[@]}" destroy "${VARS}" "${APPROVE[@]}"
    "${TESTENV}" down --yes >/dev/null  # mark the environment as removed for `status`
    ;;

  *)
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac

#!/bin/sh
# Only Traefik may reach the EC2 metadata service (169.254.169.254).
#
# Traefik uses the instance role for Route53 DNS-01. That role can publish
# _acme-challenge records, which is enough to get certificates for the platform
# domain, so no other container may fetch it. A systemd timer re-runs this
# because Traefik's address changes when its container is recreated.
# Fails closed: with no Traefik container, no container reaches the metadata service.
set -eu

project="${1:-meteorcloud}"
chain=METEORCLOUD-IMDS

traefik_ips=$(
  docker ps -q \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.docker.compose.service=traefik" |
    xargs -r docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'
)

# One atomic commit: declaring the chain flushes it and the new rules replace the old.
{
  echo "*filter"
  echo ":${chain} - [0:0]"
  for ip in ${traefik_ips}; do
    echo "-A ${chain} -s ${ip}/32 -j RETURN"
  done
  echo "-A ${chain} -j DROP"
  echo "COMMIT"
} | iptables-restore --noflush

iptables -C DOCKER-USER -d 169.254.169.254/32 -j "${chain}" 2>/dev/null ||
  iptables -I DOCKER-USER -d 169.254.169.254/32 -j "${chain}"

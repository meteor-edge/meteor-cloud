#!/usr/bin/env bash
# The installers (scripts/dist.sh → install-rpi.sh, install-meteorcli.sh) end to end under real
# systemd: it refuses another board, installs and enrolls with --token, can run
# again as an upgrade that keeps the identity, enrolls a fresh device, and
# prepares an OS image whose card enrolls by approval.
# Needs Docker with --privileged. Usage: test/install/run.sh  (from agent-go/)
set -euo pipefail
cd "$(dirname "$0")/../.."

name="meteorcli-install-test-$$"
ctx="$(mktemp -d)"
cleanup() {
	docker rm -f "$name" >/dev/null 2>&1 || true
	rm -rf "$ctx"
}
[[ -n "${KEEP:-}" ]] || trap cleanup EXIT

arch="$(docker version --format '{{.Server.Arch}}')"
case "$arch" in
amd64 | x86_64) arch=amd64 ;;
arm64 | aarch64) arch=arm64 ;;
*) echo "unsupported Docker platform: $arch" >&2; exit 1 ;;
esac
# The default base URL (the GitHub release): run from /dist, the installer must
# install the files next to it, as after make agent-go-dist on a device.
./scripts/dist.sh "$ctx/dist" 0.0.0-installtest >/dev/null
CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath -o "$ctx/fakeserver" ./test/systemd/fakeserver
cp test/install/Dockerfile "$ctx/"
docker build -q --platform "linux/$arch" -t meteorcli-install-test "$ctx" >/dev/null
docker run -d --platform "linux/$arch" --name "$name" --privileged --cgroupns=private \
	--tmpfs /run --tmpfs /run/lock --tmpfs /tmp meteorcli-install-test >/dev/null
docker cp "$ctx/dist" "$name:/dist"

ctr() { docker exec "$name" "$@"; }
failures=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; failures=$((failures + 1)); }
# expect <exit code> <text the output must contain> <description> -- <command...>
expect() {
	local code="$1" want="$2" what="$3"; shift 4
	local out rc=0
	out="$("$@" 2>&1)" || rc=$?
	if [[ $rc -eq $code && $out == *"$want"* ]]; then
		pass "$what"
	else
		fail "$what (exit $rc, want $code and \"$want\"):"
		sed 's/^/    /' <<<"$out"
	fi
}
heartbeats() { ctr sh -c 'grep -c "POST /api/v1/agent/heartbeat auth=yes" /var/log/fakeserver.log || true'; }
wait_heartbeat_after() {
	local before="$1"
	for _ in $(seq 100); do
		(($(heartbeats) > before)) && return 0
		sleep 0.1
	done
	return 1
}
install() { ctr env SUDO_USER=pi bash /dist/install-rpi.sh "$@"; }
# The generic installer from the agent-v* release: any board, binary by uname -m.
install_any() { ctr env SUDO_USER=pi bash /dist/install-meteorcli.sh "$@"; }

for _ in $(seq 60); do
	state="$(ctr systemctl is-system-running 2>/dev/null || true)"
	[[ $state == running || $state == degraded ]] && break
	sleep 1
done

expect 1 "this installer is for raspberry-pi" "refuses a board that is not a Raspberry Pi" -- install
expect 2 "No such file" "nothing was installed" -- ctr ls /usr/local/bin/meteorcli

before="$(heartbeats)"
expect 0 "Registered device 11111111" "installs (--force: not a Pi) and registers with --token" -- \
	install --force --api-base http://127.0.0.1:8099 --token reg_installtesttoken
if wait_heartbeat_after "$before"; then pass "online right after the installer finished"; else fail "no heartbeat after the install"; fi
expect 0 "Device ID:      11111111-2222-4333-8444-555555555555" "pi runs status without sudo" -- \
	ctr setpriv --reuid=pi --regid=pi --init-groups /usr/local/bin/meteorcli status
expect 0 "0.0.0-installtest" "meteorcli is the Go binary" -- ctr /usr/local/bin/meteorcli --version
expect 0 "srw-rw---- 1 meteor meteorcli" "agent.sock is meteor:meteorcli 0660" -- ctr ls -l /run/meteorcli/agent.sock

before="$(heartbeats)"
expect 0 "meteorcli-admin" "the installing user is in meteorcli-admin (no sudo for meteorcli)" -- ctr id -nG pi
expect 0 "done." "install-meteorcli.sh runs again as an upgrade on any board" -- install_any
if wait_heartbeat_after "$before"; then pass "still online after the upgrade"; else fail "no heartbeat after the upgrade"; fi
expect 0 "11111111-2222-4333-8444-555555555555" "identity kept across the upgrade" -- ctr meteorcli status

# A fresh device enrolled by the installer.
ctr sh -c 'systemctl stop meteorcli.service; rm -rf /var/lib/meteorcli/identity/* /etc/meteorcli/config.json*'
before="$(heartbeats)"
expect 0 "Registered device 11111111" "installs and registers with --token" -- \
	install_any --api-base http://127.0.0.1:8099 --token reg_installtesttoken --name edge-test
if wait_heartbeat_after "$before"; then pass "online right after the installer finished"; else fail "no heartbeat after --token install"; fi
expect 0 "name=edge-test" "the requested name was sent" -- ctr grep "POST /api/v1/agent/register" /var/log/fakeserver.log
expect 0 "" "no secret in the journal" -- ctr sh -c '! journalctl --no-pager | grep -q "installtesttoken"'

# An OS image: install-meteorcli-firstboot.sh --image, then a card with
# .meteorenv. meteorcli-settings.sh (early in boot) hands the enrollment lines
# to /etc/meteorcli/meteorenv; meteorcli.service applies them as root and the
# agent asks for approval by itself.
ctr sh -c 'systemctl stop meteorcli.service; rm -rf /var/lib/meteorcli/identity/* /etc/meteorcli/config.json*'
expect 0 "Ready for an image" "install-meteorcli-firstboot.sh --image (from the release folder)" -- \
	ctr bash /dist/install-meteorcli-firstboot.sh --image
expect 0 "enabled" "meteorcli-settings.service is enabled" -- ctr systemctl is-enabled meteorcli-settings.service
ctr sh -c 'mkdir -p /boot/firmware && printf "METEOR_ENV_VERSION=2\nMETEOR_API_BASE=http://127.0.0.1:8099\nMETEOR_API_KEY=key_installtestkey\nMETEOR_NAME=card-01\n" > /boot/firmware/.meteorenv'
expect 0 "" "meteorcli-settings.sh takes the card's settings" -- ctr /usr/local/sbin/meteorcli-settings.sh
expect 0 "" "the key left the card" -- ctr test ! -e /boot/firmware/.meteorenv
expect 0 "-rw-------" "the hand-off file is root-only" -- ctr ls -l /etc/meteorcli/meteorenv
ctr systemctl restart meteorcli.service
for _ in $(seq 100); do
	ctr grep -q "POST /api/v1/agent/enroll/request auth=yes name=card-01" /var/log/fakeserver.log && break
	sleep 0.1
done
expect 0 "name=card-01" "the agent asked for approval with the card's key and name" -- \
	ctr grep "POST /api/v1/agent/enroll/request" /var/log/fakeserver.log
expect 0 "" "the hand-off file was applied and removed" -- ctr test ! -e /etc/meteorcli/meteorenv
expect 0 "" "no secret in the journal" -- ctr sh -c '! journalctl --no-pager | grep -q installtestkey'

if [[ $failures -gt 0 ]]; then
	echo "--- meteorcli journal"; ctr journalctl -u meteorcli.service --no-pager | tail -40
	echo "$failures check(s) failed"
	exit 1
fi
echo "all install checks passed"

#!/usr/bin/env bash
# The board installer (scripts/dist.sh → install-rpi.sh) end to end under real
# systemd: it refuses another board, takes over a Python install (service
# stopped, identity imported, device online with the imported token), can run
# again as an upgrade, and enrolls a fresh device with --token.
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
./scripts/dist.sh "$ctx/dist" 0.0.0-installtest file:///dist >/dev/null
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

for _ in $(seq 60); do
	state="$(ctr systemctl is-system-running 2>/dev/null || true)"
	[[ $state == running || $state == degraded ]] && break
	sleep 1
done

expect 1 "this installer is for raspberry-pi" "refuses a board that is not a Raspberry Pi" -- install
expect 2 "No such file" "nothing was installed" -- ctr ls /usr/local/bin/meteorcli

# A device running the Python agent: /opt/meteorcli, the /usr/local/bin link,
# its service, and its enrolled /etc/meteorcli layout (pointing at the fake server).
ctr sh -c 'mkdir -p /opt/meteorcli/bin /etc/meteorcli \
 && printf "#!/bin/sh\necho python meteorcli\n" > /opt/meteorcli/bin/meteorcli && chmod 755 /opt/meteorcli/bin/meteorcli \
 && ln -s /opt/meteorcli/bin/meteorcli /usr/local/bin/meteorcli \
 && printf "[Service]\nExecStart=/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n" > /etc/systemd/system/meteorcli.service \
 && systemctl daemon-reload && systemctl enable --now meteorcli.service \
 && printf "{\"server_url\":\"http://127.0.0.1:8099\",\"device_id\":\"11111111-2222-4333-8444-555555555555\",\"organization_id\":\"org-1\",\"name\":\"edge-py\",\"heartbeat_interval_seconds\":60,\"domain\":null,\"api_base\":\"http://127.0.0.1:8099\"}\n" > /etc/meteorcli/config.json \
 && printf "dev_installtestpythontoken\n" > /etc/meteorcli/device-token && chmod 600 /etc/meteorcli/device-token'

before="$(heartbeats)"
expect 0 "importing the Python agent" "takes over the Python install (--force: not a Pi)" -- install --force
if wait_heartbeat_after "$before"; then pass "online with the imported identity, no enrollment"; else fail "no heartbeat after the takeover"; fi
expect 0 "Device ID:      11111111-2222-4333-8444-555555555555" "pi runs status without sudo (imported device id)" -- \
	ctr setpriv --reuid=pi --regid=pi --init-groups /usr/local/bin/meteorcli status
expect 0 "/opt/meteorcli/bin/meteorcli" "the Python install is kept for a rollback" -- ctr ls /opt/meteorcli/bin/meteorcli
expect 0 "0.0.0-installtest" "meteorcli is the Go binary, not the Python link" -- \
	ctr sh -c 'test ! -L /usr/local/bin/meteorcli && /usr/local/bin/meteorcli --version'
expect 0 "srw-rw---- 1 meteor meteorcli" "agent.sock is meteor:meteorcli 0660" -- ctr ls -l /run/meteorcli/agent.sock

before="$(heartbeats)"
expect 0 "done." "runs again as an upgrade" -- install --force
if wait_heartbeat_after "$before"; then pass "still online after the upgrade"; else fail "no heartbeat after the upgrade"; fi
expect 0 "11111111-2222-4333-8444-555555555555" "identity kept across the upgrade" -- ctr meteorcli status

# A fresh device enrolled by the installer.
ctr sh -c 'systemctl stop meteorcli.service; rm -rf /var/lib/meteorcli/identity/* /etc/meteorcli/config.json*'
before="$(heartbeats)"
expect 0 "Registered device 11111111" "installs and registers with --token" -- \
	install --force --api-base http://127.0.0.1:8099 --token reg_installtesttoken --name edge-test
if wait_heartbeat_after "$before"; then pass "online right after the installer finished"; else fail "no heartbeat after --token install"; fi
expect 0 "name=edge-test" "the requested name was sent" -- ctr grep "POST /api/v1/agent/register" /var/log/fakeserver.log
expect 0 "" "no secret in the journal" -- ctr sh -c '! journalctl --no-pager | grep -E -q "installtest(token|pythontoken)"'

if [[ $failures -gt 0 ]]; then
	echo "--- meteorcli journal"; ctr journalctl -u meteorcli.service --no-pager | tail -40
	echo "$failures check(s) failed"
	exit 1
fi
echo "all install checks passed"

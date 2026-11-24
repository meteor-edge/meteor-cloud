#!/usr/bin/env bash
# Socket and helper permissions under real systemd (Debian 12, systemd as PID 1).
# Needs Docker with --privileged. Usage: test/systemd/run.sh  (from agent-go/)
set -euo pipefail
cd "$(dirname "$0")/../.."

name="meteorcli-systemd-test-$$"
ctx="$(mktemp -d)"
cleanup() {
	docker rm -f "$name" >/dev/null 2>&1 || true
	rm -rf "$ctx"
}
[[ -n "${KEEP:-}" ]] || trap cleanup EXIT

# Build for the Docker host's own platform (amd64 in CI, arm64 on an ARM machine).
arch="$(docker version --format '{{.Server.Arch}}')"
case "$arch" in
amd64 | x86_64) arch=amd64 ;;
arm64 | aarch64) arch=arm64 ;;
*) echo "unsupported Docker platform: $arch" >&2; exit 1 ;;
esac
# The updater checks use a throwaway release key that only these builds trust.
keys="$(go run ./tools/release genkey)"
test_priv="$(sed -n 's/^private //p' <<<"$keys")"
test_pub="$(sed -n 's/^public //p' <<<"$keys")"
build() { # build <out> <version>
	CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath \
		-ldflags "-s -w -X main.version=$2 -X meteorcloud/meteorcli/internal/update.releaseKeys=$test_pub" -o "$1" ./cmd/meteorcli
}
sign() { # sign <dir> <version> [private key]
	METEORCLI_RELEASE_SIGNING_KEY="${3:-$test_priv}" go run ./tools/release sign "$1" "$2" >/dev/null
}
build "$ctx/meteorcli" systemd-test
asset="meteorcli-linux-$arch"
mkdir -p "$ctx"/releases/{good,broken,unsigned,foreign}
build "$ctx/releases/good/$asset" 0.2.0-systemd.1
sign "$ctx/releases/good" 0.2.0-systemd.1
# Passes the --version check, then exits at start.
printf '#!/bin/sh\n[ "$1" = --version ] && { echo "meteorcli 0.3.0-broken"; exit 0; }\necho "broken build" >&2\nexit 1\n' \
	>"$ctx/releases/broken/$asset"
chmod 0755 "$ctx/releases/broken/$asset"
sign "$ctx/releases/broken" 0.3.0-broken
build "$ctx/releases/unsigned/$asset" 0.4.0-unsigned
cp "$ctx/releases/unsigned/$asset" "$ctx/releases/foreign/"
sign "$ctx/releases/unsigned" 0.4.0-unsigned
rm "$ctx/releases/unsigned/meteorcli-release.json.sig"
sign "$ctx/releases/foreign" 0.4.0-unsigned "$(go run ./tools/release genkey | sed -n 's/^private //p')"
CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath -o "$ctx/holdconn" ./test/systemd/holdconn
CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath -o "$ctx/fakeserver" ./test/systemd/fakeserver
cp -r packaging test/systemd/Dockerfile "$ctx/"
docker build -q --platform "linux/$arch" -t meteorcli-systemd-test "$ctx" >/dev/null
docker run -d --platform "linux/$arch" --name "$name" --privileged --cgroupns=private \
	--tmpfs /run --tmpfs /run/lock --tmpfs /tmp meteorcli-systemd-test >/dev/null

ctr() { docker exec "$name" "$@"; }
as() { local who="$1"; shift; ctr setpriv --reuid="$who" --regid="$who" --init-groups /usr/local/bin/meteorcli "$@"; }

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

for _ in $(seq 60); do
	state="$(ctr systemctl is-system-running 2>/dev/null || true)"
	[[ $state == running || $state == degraded ]] && break
	sleep 1
done
for _ in $(seq 30); do
	ctr test -S /run/meteorcli/agent.sock && break
	sleep 1
done
expect 0 "active" "meteorcli.service is active" -- ctr systemctl is-active meteorcli.service
expect 0 "srw-rw---- 1 meteor meteorcli" "agent.sock is meteor:meteorcli 0660" -- ctr ls -l /run/meteorcli/agent.sock
expect 0 "srw-rw---- 1 root meteor" "helper.sock is root:meteor 0660" -- ctr ls -l /run/meteorcli/helper.sock
expect 0 "drwxr-x--- 2 meteor meteorcli" "/run/meteorcli is meteor:meteorcli 0750" -- ctr ls -ld /run/meteorcli

# Agent socket: group policy per command.
expect 0 "Agent:          running systemd-test (enrolled)" "alice (meteorcli-admin) runs status without sudo" -- as alice status
expect 0 "Module socket restarted." "alice (meteorcli-admin) runs module restart without sudo" -- as alice module restart socket
sleep 1
expect 0 "Device ID:      dev-test-1" "carol (meteorcli) runs status" -- as carol status
expect 1 "needs the meteorcli-admin group" "carol (meteorcli) cannot restart modules" -- as carol module restart socket
expect 1 "permission denied" "bob (no group) is refused status" -- as bob status
expect 1 "permission denied" "bob (no group) is refused module restart" -- as bob module restart socket
expect 1 "add you to the meteorcli group" "erin (only meteorcli-admin) is told to join meteorcli too" -- as erin status
expect 0 "Agent:          running" "root runs status" -- ctr meteorcli status

# carol keeps her 4 connection slots full with requests she never sends (the
# server drops each after its 5 s read deadline; she reconnects). Once she is
# refused a fifth, alice and root must still get in.
# Attached execs backgrounded on the host (output kept for diagnosis), killed by
# cleanup at the latest.
holders=()
for i in 1 2 3 4; do
	docker exec "$name" setpriv --reuid=carol --regid=carol --init-groups \
		/usr/local/bin/holdconn /run/meteorcli/agent.sock 30 >"$ctx/holder-$i.log" 2>&1 &
	holders+=($!)
done
carol_refused() {
	for _ in $(seq 50); do
		local out
		out="$(as carol status 2>&1 || true)"
		[[ $out == *"too many connections"* ]] && return 0
		sleep 0.2
	done
	echo "    last carol status: $(head -1 <<<"$out")"
	for f in "$ctx"/holder-*.log; do echo "    $(basename "$f"): $(sort "$f" | uniq -c | head -3 | tr '\n' ' ')"; done
	return 1
}
if carol_refused; then pass "carol cannot open a fifth connection"; else fail "carol was never refused a fifth connection"; fi
expect 0 "Agent:          running" "root runs status while carol holds her slots" -- ctr meteorcli status
expect 0 "Agent:          running" "alice runs status while carol holds her slots" -- as alice status
expect 0 "Module socket restarted." "alice runs module restart while carol holds her slots" -- as alice module restart socket
# The restart dropped carol's connections; once she holds them again, root still gets in.
if carol_refused; then pass "carol holds her slots again after the restart"; else fail "carol never held her slots again"; fi
expect 0 "Agent:          running" "root runs status while carol holds her slots again" -- ctr meteorcli status
kill "${holders[@]}" 2>/dev/null || true

# Helper: only uid meteor.
expect 0 '"lines"' "meteor reads the journal through the helper" -- \
	as meteor helper call logs.read '{"source":"systemd","unit":"meteorcli.service","lines":5}'
expect 1 "serves only uid" "dave (in groups meteor+meteorcli, not uid meteor) is refused by the helper" -- \
	as dave helper call logs.read '{"source":"journal","lines":5}'
expect 1 "serves only uid" "root is refused by the helper" -- ctr meteorcli helper call power.reboot
expect 1 "permission denied" "carol cannot open helper.sock" -- as carol helper call power.reboot
expect 1 "meteorcli units cannot be changed" "the helper refuses to touch meteorcli units" -- \
	as meteor helper call systemd.install_unit '{"name":"meteorcli.service","content":"[Service]\nExecStart=/bin/true\n"}'
expect 0 '"installed":"demo-app.service"' "meteor installs a unit through the helper" -- \
	as meteor helper call systemd.install_unit '{"name":"demo-app.service","content":"[Unit]\nDescription=demo\n[Service]\nExecStart=/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n"}'
expect 0 "active" "the installed unit runs" -- ctr systemctl is-active demo-app.service

# Users (the console's add_user and set_password actions), under the helper's sandbox.
key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGR72aezhaCVWi/bpEuEaWtIh9MnmvRaBbdD7eAyFDAK ops'
expect 0 '"created":true' "meteor adds a user with sudo and an SSH key through the helper" -- \
	as meteor helper call users.add "{\"username\":\"ops\",\"password\":\"Fakepass-123\",\"sudo\":true,\"authorized_key\":\"$key\"}"
expect 0 "/home/ops" "the new user has a home directory" -- ctr getent passwd ops
expect 0 "ops ops" "authorized_keys belongs to the new user" -- ctr stat -c '%U %G' /home/ops/.ssh/authorized_keys
expect 0 "$key" "authorized_keys holds the key" -- ctr cat /home/ops/.ssh/authorized_keys
expect 0 "ops ALL=(ALL) NOPASSWD:ALL" "the sudo rule is written" -- ctr cat /etc/sudoers.d/90-meteorcli-ops
before="$(ctr getent shadow ops | cut -d: -f2)"
expect 0 '"changed":true' "meteor changes the user's password through the helper" -- \
	as meteor helper call users.set_password '{"username":"ops","password":"Otherpass-456"}'
after="$(ctr getent shadow ops | cut -d: -f2)"
if [[ -n $after && $after != "$before" && $after == '$'* ]]; then pass "the password hash changed"; else fail "the password hash did not change"; fi
expect 1 "cannot be changed from the console" "the helper refuses to change root's password" -- \
	as meteor helper call users.set_password '{"username":"root","password":"Otherpass-456"}'
expect 1 "already exists" "the helper refuses to add an existing user" -- \
	as meteor helper call users.add '{"username":"alice","password":"Fakepass-123"}'
expect 0 '"entries"' "meteor reads journal warnings through the helper" -- as meteor helper call logs.warnings
expect 0 "" "no user password in any journal" -- ctr sh -c '! journalctl --no-pager | grep -qE "Fakepass|Otherpass"'

# Audit lines in the journal, without secrets.
expect 0 "method=module.restart uid=$(ctr id -u alice)" "agent journal names who restarted a module" -- ctr journalctl -u meteorcli.service --no-pager
expect 0 "outcome=permission_denied" "helper journal records refused callers" -- ctr journalctl -u meteorcli-helper.service --no-pager
expect 0 "" "no token in any journal" -- ctr sh -c '! journalctl --no-pager | grep -q faketoken'

# Hardening as systemd sees it on the running units.
expect 0 "" "agent exposure under 2.0" -- ctr systemd-analyze security --threshold=19 meteorcli.service
expect 0 "" "helper exposure under 6.0" -- ctr systemd-analyze security --threshold=59 meteorcli-helper.service

# ---------------------------------------------------------------- enrollment
# Against test/systemd/fakeserver on 127.0.0.1:8099; it logs one line per request.
server_log=/var/log/fakeserver.log
now_ms() { ctr date +%s%3N; }
# wait_for <pattern> <after unix ms> <seconds>: prints the ms of the first matching log line after then.
wait_for() {
	local pattern="$1" after="$2" deadline=$(($(date +%s) + $3)) line
	while (($(date +%s) < deadline)); do
		line="$(ctr awk -v after="$after" -v pat="$pattern" '$1 >= after && index($0, pat) { print $1; exit }' "$server_log" 2>/dev/null || true)"
		[[ -n $line ]] && { echo "$line"; return 0; }
		sleep 0.1
	done
	return 1
}
wait_active() {
	for _ in $(seq 60); do
		[[ "$(ctr systemctl is-active meteorcli.service 2>/dev/null || true)" == active ]] &&
			ctr test -S /run/meteorcli/agent.sock && return 0
		sleep 0.5
	done
	return 1
}

# Done criterion: with the service running, `meteorcli register` by a
# meteorcli-admin user without sudo makes the device online within 5 s.
# Changing the server (root) drops the old identity.
expect 0 "Cleared previous enrollment" "root points the device at a new server" -- \
	ctr meteorcli config --api-base http://127.0.0.1:8099
ctr sh -c 'printf "reg_systemdtestregtoken\n" > /home/alice/reg-token && chown alice: /home/alice/reg-token && chmod 0600 /home/alice/reg-token'
start="$(now_ms)"
expect 0 "Registered device 11111111" "alice (meteorcli-admin) registers without sudo" -- \
	as alice register --token-file /home/alice/reg-token --name edge-test
if beat="$(wait_for "POST /api/v1/agent/heartbeat auth=yes" "$start" 10)" && ((beat - start <= 5000)); then
	pass "online $((beat - start)) ms after register (limit 5000)"
else
	fail "no heartbeat within 5 s of register (${beat:-none})"
fi
expect 0 "" "the token file is removed after success" -- ctr test ! -e /home/alice/reg-token
expect 0 "Agent:          running systemd-test (enrolled)" "status shows the new enrollment" -- as alice status

# Done criterion: approving a pending request brings the device online with no
# command on the device, also after a reboot while pending.
expect 0 "Cleared previous enrollment" "root moves the device to another server URL" -- \
	ctr meteorcli config --api-base http://localhost:8099
expect 0 "submitted" "alice requests approval without sudo" -- as alice request-token --api-key key_systemdtestapikey
# status exits 1 until the device is enrolled, like the Python CLI.
expect 1 "request aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee is waiting" "status shows the pending request" -- as alice status
ctr systemctl stop meteorcli.service
ctr touch /var/lib/fakeserver/approve # approved in the console while the device is off
start="$(now_ms)"
docker restart "$name" >/dev/null
if wait_active && beat="$(wait_for "POST /api/v1/agent/heartbeat auth=yes" "$start" 30)"; then
	pass "online after the reboot with no command ($((beat - start)) ms after the restart began)"
else
	fail "no heartbeat after approving and rebooting"
fi
expect 0 "" "the API key is deleted after enrollment" -- ctr test ! -e /var/lib/meteorcli/identity/api-key

# First boot: a meteorenv file on the boot partition is applied by root before the
# agent starts, removed, and the agent asks for approval by itself. A first boot
# is not enrolled: drop the identity by moving to another server URL first.
ctr rm -f /var/lib/fakeserver/approve
ctr systemctl stop meteorcli.service
expect 0 "Cleared previous enrollment" "root clears the enrollment (stands in for a fresh image)" -- \
	ctr meteorcli config --api-base http://127.0.0.1:8099
ctr mkdir -p /boot/firmware
ctr sh -c 'printf "# first boot\nMETEOR_API_BASE=http://127.0.0.1:8099\nMETEOR_API_KEY=key_systemdtestbootkey\nMETEOR_NAME=lobby-cam\n" > /boot/firmware/meteorenv.txt'
start="$(now_ms)"
ctr systemctl restart meteorcli.service
if wait_active && wait_for "POST /api/v1/agent/enroll/request auth=yes name=lobby-cam" "$start" 15 >/dev/null; then
	pass "meteorenv applied at start; the agent requested approval by itself"
else
	fail "no enrollment request after a meteorenv start"
fi
expect 0 "" "meteorenv.txt is removed" -- ctr test ! -e /boot/firmware/meteorenv.txt
start="$(now_ms)"
ctr touch /var/lib/fakeserver/approve
if beat="$(wait_for "POST /api/v1/agent/heartbeat auth=yes" "$start" 20)"; then
	pass "approved in the console: online with no command ($((beat - start)) ms)"
else
	fail "no heartbeat after approving the meteorenv request"
fi
expect 0 "" "no enrollment secret in any journal" -- \
	ctr sh -c '! journalctl --no-pager | grep -E -q "systemdtest(devicetoken|regtoken|apikey|bootkey|claimsecret)"'

# ------------------------------------------------------------------ updater
# update_agent from the console: the agent asks the helper, which starts
# meteorcli-update.service; the updater checks the signed release, keeps the
# installed binary as meteorcli.previous, swaps, restarts the agent and waits
# for its heartbeat, else rolls back. The fake server hands out the action
# once and logs each result as name=<id>:<state>.
update_action() { ctr sh -c "printf '{\"id\":\"$1\",\"kind\":\"update_agent\",\"from_version\":\"x\"}' >/var/lib/fakeserver/action"; }
outcome=/var/lib/meteorcli/state/agent-update-outcome.json

expect 0 "Release URL:    file:///srv/releases/good" "root points the updater at a release" -- \
	ctr meteorcli config --release-url file:///srv/releases/good
start="$(now_ms)"
update_action 0f1e2d3c-0000-4000-8000-00000000000a
if wait_for "name=0f1e2d3c-0000-4000-8000-00000000000a:done" "$start" 120 >/dev/null; then
	pass "update_agent to a good build is reported done"
else
	fail "no done report for the update to a good build"
fi
expect 0 "meteorcli 0.2.0-systemd.1" "the new binary is installed" -- ctr meteorcli --version
expect 0 "meteorcli systemd-test" "the replaced binary is kept as meteorcli.previous" -- ctr /usr/local/bin/meteorcli.previous --version
expect 0 "Agent:          running 0.2.0-systemd.1 (enrolled)" "the agent runs the new version" -- as alice status
expect 0 "new version sent a heartbeat" "the updater waited for the new version's heartbeat" -- \
	ctr journalctl -u meteorcli-update.service --no-pager
expect 0 "" "the reported update leaves no pending file" -- ctr test ! -e /var/lib/meteorcli/state/agent-update.json

expect 0 "Release URL:    file:///srv/releases/broken" "root points the updater at a build that exits at start" -- \
	ctr meteorcli config --release-url file:///srv/releases/broken
start="$(now_ms)"
update_action 0f1e2d3c-0000-4000-8000-00000000000b
if wait_for "name=0f1e2d3c-0000-4000-8000-00000000000b:failed" "$start" 180 >/dev/null; then
	pass "update_agent to a build that exits at start is reported failed"
else
	fail "no failed report for the update to a broken build"
fi
expect 0 "meteorcli 0.2.0-systemd.1" "the previous binary is back" -- ctr meteorcli --version
expect 0 "active" "meteorcli.service runs again" -- ctr systemctl is-active meteorcli.service
expect 0 "Agent:          running 0.2.0-systemd.1 (enrolled)" "the agent runs the previous version" -- as alice status
expect 0 "rolled back to 0.2.0-systemd.1" "the console is told why" -- ctr journalctl -u fakeserver.service --no-pager

expect 1 "needs the meteorcli-admin group" "carol (meteorcli) cannot start an update" -- as carol update
expect 0 "Release URL:    file:///srv/releases/unsigned" "root points the updater at an unsigned release" -- \
	ctr meteorcli config --release-url file:///srv/releases/unsigned
ctr rm -f "$outcome"
expect 0 "Update started (meteorcli-update.service)" "alice (meteorcli-admin) starts an update without sudo" -- as alice update
for _ in $(seq 60); do ctr test -e "$outcome" && break; sleep 1; done
expect 0 "not signed by a MeteorCloud release key" "the unsigned release is refused" -- ctr cat "$outcome"
expect 0 "meteorcli 0.2.0-systemd.1" "nothing was installed from the unsigned release" -- ctr meteorcli --version

expect 0 "Release URL:    file:///srv/releases/foreign" "root points the updater at a release signed by another key" -- \
	ctr meteorcli config --release-url file:///srv/releases/foreign
start="$(now_ms)"
update_action 0f1e2d3c-0000-4000-8000-00000000000c
if wait_for "name=0f1e2d3c-0000-4000-8000-00000000000c:failed" "$start" 120 >/dev/null; then
	pass "update_agent to a release signed by another key is reported failed"
else
	fail "no failed report for the release signed by another key"
fi
expect 0 "meteorcli 0.2.0-systemd.1" "nothing was installed from the foreign release" -- ctr meteorcli --version
expect 0 "action_id=0f1e2d3c-0000-4000-8000-00000000000c" "the helper journal records update.start with the action" -- ctr journalctl -u meteorcli-helper.service --no-pager

if [[ $failures -gt 0 ]]; then
	echo "--- updater journal"; ctr journalctl -u meteorcli-update.service --no-pager | tail -40
	echo "--- meteorcli journal"; ctr journalctl -u meteorcli.service -u meteorcli-helper.service --no-pager | tail -40
	echo "$failures check(s) failed"
	exit 1
fi
echo "all systemd checks passed"

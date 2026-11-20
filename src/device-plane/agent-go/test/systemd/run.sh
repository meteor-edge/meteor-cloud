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
CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath -ldflags "-s -w -X main.version=systemd-test" \
	-o "$ctx/meteorcli" ./cmd/meteorcli
CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath -o "$ctx/holdconn" ./test/systemd/holdconn
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

# Audit lines in the journal, without secrets.
expect 0 "method=module.restart uid=$(ctr id -u alice)" "agent journal names who restarted a module" -- ctr journalctl -u meteorcli.service --no-pager
expect 0 "outcome=permission_denied" "helper journal records refused callers" -- ctr journalctl -u meteorcli-helper.service --no-pager
expect 0 "" "no token in any journal" -- ctr sh -c '! journalctl --no-pager | grep -q faketoken'

# Hardening as systemd sees it on the running units.
expect 0 "" "agent exposure under 2.0" -- ctr systemd-analyze security --threshold=19 meteorcli.service
expect 0 "" "helper exposure under 6.0" -- ctr systemd-analyze security --threshold=59 meteorcli-helper.service

if [[ $failures -gt 0 ]]; then
	echo "--- meteorcli journal"; ctr journalctl -u meteorcli.service -u meteorcli-helper.service --no-pager | tail -40
	echo "$failures check(s) failed"
	exit 1
fi
echo "all systemd checks passed"

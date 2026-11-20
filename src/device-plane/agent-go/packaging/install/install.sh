#!/usr/bin/env bash
# Install the Go meteorcli on a device: one script per board (scripts/dist.sh
# fills in BOARD and the release URL), run as root:
#
#   curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/download/meteorcli-go-edge/install-rpi.sh | sudo bash
#   curl -fsSL …/install-rpi.sh | sudo bash -s -- --domain meteor.example.com --token reg_XXXX
#   curl -fsSL …/install-rpi.sh | sudo bash -s -- --domain meteor.example.com --api-key key_XXXX --name lobby-cam
#
# It downloads the binary for this CPU, the systemd units, sysusers.d and
# tmpfiles.d files, checks them against SHA256SUMS, installs them, takes over
# from the Python meteorcli when it is installed (its service is stopped and
# its files imported; /opt/meteorcli is left for a rollback), and starts the
# agent. With --token it enrolls at once; with --api-key the agent asks for
# approval by itself.
#
# Options:
#   --domain HOST | --api-base URL   server to use (as 'meteorcli config')
#   --token TOKEN | --token-file F   enroll with a one-time registration token
#   --api-key KEY                    enroll by approval with an enrollment API key
#   --name NAME                      device name to ask for
#   --user USER                      give USER the meteorcli and meteorcli-admin groups (default: $SUDO_USER)
#   --base-url URL                   where the release files are (also $METEORCLI_BASE_URL; file:// works)
#   --force                          install on a board this script is not for
#   --no-start                       install only
set -euo pipefail

BOARD="@BOARD@"
BASE_URL="${METEORCLI_BASE_URL:-@BASE_URL@}"
BIN=/usr/local/bin/meteorcli
UNIT_DIR=/etc/systemd/system

domain="" api_base="" token="" token_file="" api_key="" name="" user="${SUDO_USER:-}" force=0 start=1
while [[ $# -gt 0 ]]; do
	case "$1" in
	--domain) domain="${2:?--domain needs a host}"; shift 2 ;;
	--api-base) api_base="${2:?--api-base needs a URL}"; shift 2 ;;
	--token) token="${2:?--token needs a value}"; shift 2 ;;
	--token-file) token_file="${2:?--token-file needs a path}"; shift 2 ;;
	--api-key) api_key="${2:?--api-key needs a value}"; shift 2 ;;
	--name) name="${2:?--name needs a value}"; shift 2 ;;
	--user) user="${2:?--user needs a name}"; shift 2 ;;
	--base-url) BASE_URL="${2:?--base-url needs a URL}"; shift 2 ;;
	--force) force=1; shift ;;
	--no-start) start=0; shift ;;
	-h | --help) sed -n '2,/^set -euo/p' "$0" 2>/dev/null | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
	*) echo "error: unknown option $1 (see --help)" >&2; exit 2 ;;
	esac
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root: curl -fsSL … | sudo bash"
for tool in curl sha256sum tar systemctl systemd-sysusers systemd-tmpfiles install; do
	command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done
[[ -d /run/systemd/system ]] || die "systemd is not running"
[[ -n $token && -n $token_file ]] && die "use either --token or --token-file, not both"

# ------------------------------------------------------------------ board, CPU
board_ok() {
	case "$BOARD" in
	raspberry-pi) grep -qa "Raspberry Pi" /proc/device-tree/model 2>/dev/null ;;
	*) return 0 ;;
	esac
}
if ! board_ok; then
	[[ $force -eq 1 ]] || die "this installer is for $BOARD; this device is $(tr -d '\0' </proc/device-tree/model 2>/dev/null || uname -m). Use --force to install anyway"
	say "not a $BOARD; installing anyway (--force)"
fi
case "$(uname -m)" in
aarch64 | arm64) arch=arm64 ;;
armv6l | armv7l | armv8l) arch=armv6 ;; # one ARMv6 build runs on every 32-bit Pi OS
x86_64 | amd64) arch=amd64 ;;
*) die "unsupported CPU $(uname -m)" ;;
esac

# ------------------------------------------------------------------- download
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fetch() { curl -fsSL --retry 3 --connect-timeout 15 -o "$work/$1" "$BASE_URL/$1" || die "cannot download $BASE_URL/$1"; }
say "downloading meteorcli ($arch) from $BASE_URL"
fetch SHA256SUMS
fetch "meteorcli-linux-$arch"
fetch meteorcli-packaging.tar.gz
(cd "$work" && grep -E " (meteorcli-linux-$arch|meteorcli-packaging\.tar\.gz)\$" SHA256SUMS | sha256sum -c --quiet -) ||
	die "checksum mismatch; try again (a release may be uploading right now)"
tar -xzf "$work/meteorcli-packaging.tar.gz" -C "$work"
# Try the binary from its final directory: /tmp is often mounted noexec.
install -m 0755 "$work/meteorcli-linux-$arch" "$BIN.new"
trap 'rm -rf "$work" "$BIN.new"' EXIT
"$BIN.new" --version >/dev/null || die "the downloaded binary does not run on this device"

# ----------------------------------------------------------- the Python agent
python_layout=0
if [[ -L $BIN ]] && [[ "$(readlink -f "$BIN")" == /opt/meteorcli/* ]]; then
	say "the Python meteorcli is installed; stopping it (kept in /opt/meteorcli for a rollback)"
	systemctl disable --now meteorcli.service 2>/dev/null || true
	python_layout=1
fi

# -------------------------------------------------------------------- install
say "installing $BIN, units, users and directories"
rm -f "$BIN"
mv -f "$BIN.new" "$BIN"
install -m 0644 "$work/packaging/meteorcli.service" "$work/packaging/meteorcli-helper.service" \
	"$work/packaging/meteorcli-helper.socket" "$UNIT_DIR/"
install -D -m 0644 "$work/packaging/sysusers.d/meteorcli.conf" /usr/lib/sysusers.d/meteorcli.conf
install -D -m 0644 "$work/packaging/tmpfiles.d/meteorcli.conf" /usr/lib/tmpfiles.d/meteorcli.conf
systemd-sysusers /usr/lib/sysusers.d/meteorcli.conf
systemd-tmpfiles --create /usr/lib/tmpfiles.d/meteorcli.conf
systemctl daemon-reload

if [[ $python_layout -eq 1 ]] || [[ -f /etc/meteorcli/device-token ]]; then
	say "importing the Python agent's settings and identity"
	"$BIN" config import || die "'meteorcli config import' failed; the Python install in /opt/meteorcli is untouched"
fi

# Settings before the agent starts: a saved API key makes it ask for approval by itself.
cfg=()
[[ -n $domain ]] && cfg+=(--domain "$domain")
[[ -n $api_base ]] && cfg+=(--api-base "$api_base")
[[ -n $api_key ]] && cfg+=(--api-key "$api_key")
if [[ ${#cfg[@]} -gt 0 ]]; then
	"$BIN" config "${cfg[@]}"
fi
if [[ -n $name && -z $token && -z $token_file ]]; then
	say "--name is used with --token; with --api-key run 'meteorcli request-token --name $name' to change it"
fi

if [[ -n $user ]] && id "$user" >/dev/null 2>&1; then
	usermod -aG meteorcli,meteorcli-admin "$user"
	say "$user may now run meteorcli without sudo (log in again first)"
fi

[[ $start -eq 1 ]] || { say "installed; start with: systemctl enable --now meteorcli-helper.socket meteorcli"; exit 0; }

systemctl enable meteorcli-helper.socket meteorcli.service >/dev/null 2>&1
systemctl restart meteorcli-helper.socket meteorcli.service
for _ in $(seq 50); do
	[[ -S /run/meteorcli/agent.sock ]] && break
	sleep 0.2
done
[[ -S /run/meteorcli/agent.sock ]] || die "meteorcli.service did not start: journalctl -u meteorcli -n 50"

if [[ -n $token || -n $token_file ]]; then
	reg=(register)
	[[ -n $token_file ]] && reg+=(--token-file "$token_file")
	[[ -n $name ]] && reg+=(--name "$name")
	if [[ -n $token ]]; then
		METEORCLI_TOKEN="$token" "$BIN" "${reg[@]}"
	else
		"$BIN" "${reg[@]}"
	fi
fi

"$BIN" status || true
say "done. Follow the agent with: journalctl -u meteorcli -f"

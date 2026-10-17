#!/usr/bin/env bash
# Tests for open_web_ports in deploy/bootstrap.sh. The function is read out of the script and
# run against a fake ufw on PATH that logs its calls and keeps its rules in a temp file, like
# a fresh Vultr box (active, only 22/tcp open). Needs only bash, grep and sed. Never runs real ufw.
#   bash deploy/tests/test_bootstrap_ufw.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP="$HERE/../bootstrap.sh"

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

PASSED=0
FAILED=0
check() { # check <description> <command...>
  local desc="$1"
  shift
  if "$@"; then
    PASSED=$((PASSED + 1))
    printf 'ok   %s\n' "$desc"
  else
    FAILED=$((FAILED + 1))
    printf 'FAIL %s\n' "$desc"
  fi
}

eq() { [ "$1" = "$2" ]; }

# Load log() and open_web_ports() from bootstrap.sh without running the rest of it.
FUNCS="$(sed -n -e '/^log() /p' -e '/^open_web_ports() {$/,/^}$/p' "$BOOTSTRAP")"
grep -q '^open_web_ports() {$' <<<"$FUNCS" || { echo "open_web_ports not found in bootstrap.sh" >&2; exit 2; }
eval "$FUNCS"

# Only what the function and the fake need, so `command -v ufw` can't find a real ufw in /usr/sbin.
TOOLS="$ROOT/tools"
mkdir -p "$TOOLS"
for tool in bash cat grep; do ln -s "$(command -v "$tool")" "$TOOLS/$tool"; done

FAKE="$ROOT/fake"
mkdir -p "$FAKE"
cat >"$FAKE/ufw" <<'SH'
#!/usr/bin/env bash
# Fake ufw: logs every call; state in $UFW_DIR/state, one rule per line in $UFW_DIR/rules.
printf '%s\n' "$*" >>"$UFW_DIR/calls"
case "$1" in
  status)
    if [ "$(cat "$UFW_DIR/state")" = active ]; then
      echo "Status: active"
      while read -r rule; do echo "$rule ALLOW Anywhere"; done <"$UFW_DIR/rules"
    else
      echo "Status: inactive"
    fi
    ;;
  allow)
    if grep -qx "$2" "$UFW_DIR/rules"; then
      echo "Skipping adding existing rule"
    else
      echo "$2" >>"$UFW_DIR/rules"
      echo "Rule added"
    fi
    ;;
  *) echo "unexpected ufw call: $*" >&2; exit 9 ;;
esac
SH
chmod +x "$FAKE/ufw"

new_box() { # new_box <active|inactive>; ufw with only 22/tcp, like a fresh Vultr image
  export UFW_DIR="$ROOT/box-$1"
  rm -rf "$UFW_DIR"
  mkdir -p "$UFW_DIR"
  echo "$1" >"$UFW_DIR/state"
  echo 22/tcp >"$UFW_DIR/rules"
  : >"$UFW_DIR/calls"
}

run_open() { # run_open <PATH>; sets RC and OUT
  OUT="$ROOT/out"
  RC=0
  (PATH="$1" open_web_ports) >"$OUT" 2>&1 || RC=$?
}

calls() { cat "$UFW_DIR/calls"; }
rules() { cat "$UFW_DIR/rules"; }
no_8000() { ! grep -q 8000 "$UFW_DIR/calls" "$UFW_DIR/rules"; }

# ufw active: both allow commands run, then a re-run adds nothing.
new_box active
run_open "$FAKE:$TOOLS"
check "active: exit 0" eq "$RC" 0
check "active: status, then allow 80/tcp and 443/tcp" eq "$(calls)" "$(printf 'status\nallow 80/tcp\nallow 443/tcp')"
check "active: rules are 22, 80, 443" eq "$(rules)" "$(printf '22/tcp\n80/tcp\n443/tcp')"
check "active: logs what it opens" grep -q 'allowing 80/tcp and 443/tcp' "$OUT"
check "active: never opens 8000" no_8000
run_open "$FAKE:$TOOLS"
check "active re-run: exit 0" eq "$RC" 0
check "active re-run: rules unchanged" eq "$(rules)" "$(printf '22/tcp\n80/tcp\n443/tcp')"
check "active re-run: same three calls again" eq "$(calls | tail -n 3)" "$(printf 'status\nallow 80/tcp\nallow 443/tcp')"
check "active re-run: never opens 8000" no_8000

# ufw inactive: only the status check runs.
new_box inactive
run_open "$FAKE:$TOOLS"
check "inactive: exit 0" eq "$RC" 0
check "inactive: only status was called" eq "$(calls)" status
check "inactive: rules untouched" eq "$(rules)" 22/tcp
check "inactive: prints nothing" eq "$(cat "$OUT")" ""

# ufw missing: nothing runs.
new_box active
run_open "$TOOLS"
check "missing: exit 0" eq "$RC" 0
check "missing: fake ufw never called" eq "$(calls)" ""
check "missing: prints nothing" eq "$(cat "$OUT")" ""

# Ports open before Caddy starts.
line_of() { grep -n -m1 -x -- "$1" "$BOOTSTRAP" | cut -d: -f1; }
check "bootstrap calls open_web_ports before restarting caddy" \
  test "$(line_of 'open_web_ports')" -lt "$(line_of 'systemctl restart caddy')"
no_ufw_8000() { ! grep -v '^[[:space:]]*#' "$BOOTSTRAP" | grep ufw | grep -q 8000; }
check "bootstrap has no ufw command with 8000" no_ufw_8000

printf '\n%d passed, %d failed\n' "$PASSED" "$FAILED"
[ "$FAILED" -eq 0 ]

#!/usr/bin/env bash
# Tests for deploy/update.sh, run against stand-ins in a temp dir: a bare "origin", a clone
# acting as /opt/iris, fake systemctl and pip that log their calls, and a fake /health served
# from the checked-out tree. Needs only bash, git, python3 and curl. Never uses port 8000.
#   bash deploy/tests/test_update.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPDATE="$HERE/../update.sh"
TEST_KEY="k3y-0123456789abcdef0123456789abcdef0123456789ab"
[ "${#TEST_KEY}" -eq 48 ] || { echo "bad test key length" >&2; exit 2; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

ROOT="$(mktemp -d)"
HEALTH_PID=""
cleanup() {
  [ -z "$HEALTH_PID" ] || kill "$HEALTH_PID" 2>/dev/null || true
  rm -rf "$ROOT"
}
trap cleanup EXIT

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

hash_of() { python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"; }
mode_of() { python3 -c 'import os, sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$1"; }
head_of() { git -C "$APP" rev-parse HEAD; }
eq() { [ "$1" = "$2" ]; }
ne() { [ "$1" != "$2" ]; }
calls() { if [ -f "$1" ]; then wc -l <"$1" | tr -d ' '; else echo 0; fi; }
restarts() { grep -cx 'restart iris' "$W/systemctl.log" || true; }

# Serves $APP/health.json (or a not-ready body) on a high port; started and killed by PID.
start_health() {
  cat >"$W/health_server.py" <<'PY'
import http.server, json, os, sys

# argv: checkout dir, env file, world dir. Like the real server, "commit" comes from IRIS_COMMIT
# in the env file. $world/pin_commit pins a different reported commit; $world/no_commit drops it.
app, envfile, world = sys.argv[1:4]

def env_commit():
    for line in open(envfile):
        if line.startswith("IRIS_COMMIT="):
            return line.rstrip("\n").split("=", 1)[1]
    return None

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = os.path.join(app, "health.json")
        h = json.load(open(path)) if os.path.exists(path) else {"ready": False}
        pin = os.path.join(world, "pin_commit")
        if os.path.exists(pin):
            h["commit"] = open(pin).read().strip()
        elif not os.path.exists(os.path.join(world, "no_commit")):
            h["commit"] = env_commit()
        body = json.dumps(h).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

srv = http.server.HTTPServer(("127.0.0.1", 0), H)
print(srv.server_address[1], flush=True)
srv.serve_forever()
PY
  python3 "$W/health_server.py" "$APP" "$ENVF" "$W" >"$W/port" &
  HEALTH_PID=$!
  local _
  for _ in $(seq 1 50); do
    [ -s "$W/port" ] && break
    sleep 0.1
  done
  PORT="$(cat "$W/port")"
  [ "$PORT" -ge 8001 ] || { echo "health port $PORT is below 8001" >&2; exit 2; }
}

commit_tree() { # commit_tree <branch> <health-json> <msg>; run inside $W/seed
  printf '%s\n' "$2" >health.json
  git add health.json
  git commit -q --allow-empty -m "$3"
}

# Fresh world per test: origin with main (c1 good), good (c2, tag rel), bad (c3).
new_world() {
  W="$ROOT/w$1"
  APP="$W/app"
  mkdir -p "$W/bin" "$W/venv/bin"
  GOOD='{"ready": true, "auth": "on"}'
  BAD='{"ready": false, "auth": "on"}'
  git init -q -b main "$W/seed"
  (
    cd "$W/seed"
    mkdir -p server
    : >server/requirements-ml.txt
    git add server
    commit_tree main "$GOOD" c1
    git checkout -q -b good
    commit_tree good "$GOOD" c2
    git tag -f rel >/dev/null
    git checkout -q -b bad
    commit_tree bad "$BAD" c3
    git checkout -q main
  )
  git clone -q --bare "$W/seed" "$W/origin.git"
  git clone -q "$W/origin.git" "$APP"
  : >"$W/pip.log"
  : >"$W/systemctl.log"
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >>"%s/systemctl.log"\n' "$W" >"$W/bin/systemctl"
  # Fake pip: logs its call; exits 1 while $W/pip.fail holds a count above 0 (counting down).
  cat >"$W/venv/bin/pip" <<PIP
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"$W/pip.log"
n=\$(cat "$W/pip.fail" 2>/dev/null || echo 0)
if [ "\$n" -gt 0 ]; then
  echo \$((n - 1)) >"$W/pip.fail"
  exit 1
fi
PIP
  chmod +x "$W/bin/systemctl" "$W/venv/bin/pip"
  # The box has util-linux flock. Where it is missing (macOS), a python fcntl stand-in with the
  # same `flock -n <fd>` behaviour runs the same lock code path.
  if command -v flock >/dev/null 2>&1; then
    FLOCK_KIND=real
  else
    FLOCK_KIND=shim
    cat >"$W/bin/flock" <<'SHIM'
#!/usr/bin/env python3
import fcntl, sys
try:
    fcntl.flock(int(sys.argv[2]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
SHIM
    chmod +x "$W/bin/flock"
  fi
  mkdir -p "$W/etc"
  ENVF="$W/etc/iris.env"
  printf 'IRIS_API_KEY=%s\nIRIS_COMMIT=0a0a0a0\nOTHER_SETTING=a|b&c \\d "q"\n' "$TEST_KEY" >"$ENVF"
  chmod 600 "$ENVF"
  start_health
}

end_world() {
  kill "$HEALTH_PID" 2>/dev/null || true
  wait "$HEALTH_PID" 2>/dev/null || true
  HEALTH_PID=""
}

run_update() { # run_update <args...>; sets RC, OUT, ERR
  OUT="$W/out.$RANDOM"
  ERR="$W/err.$RANDOM"
  set +e
  upd_args
  "${UPD[@]}" bash "$UPDATE" "$@" >"$OUT" 2>"$ERR"
  RC=$?
  set -e
}

# Run a command with the stand-in environment for update.sh (health timeout $HEALTH_T, default 3).
# $EXTRA_PATH, when set, goes first on PATH.
upd_args() {
  UPD=(env IRIS_APP_DIR="$APP" IRIS_ENV_FILE="$ENVF" IRIS_VENV="$W/venv" \
    IRIS_HEALTH_URL="http://127.0.0.1:$PORT/health" IRIS_HEALTH_TIMEOUT_S="${HEALTH_T:-3}" \
    IRIS_SYSTEMCTL="$W/bin/systemctl" IRIS_LOCK_FILE="$W/lock" IRIS_UPDATE_LOG="$W/update.log" \
    PATH="${EXTRA_PATH:+$EXTRA_PATH:}$W/bin:$PATH")
}

# Start an update in the background; sets BGPID, OUT, ERR.
start_update_bg() { # start_update_bg <health-timeout> <args...>
  HEALTH_T="$1"
  shift
  OUT="$W/out.$RANDOM"
  ERR="$W/err.$RANDOM"
  upd_args
  "${UPD[@]}" bash "$UPDATE" "$@" >"$OUT" 2>"$ERR" &
  BGPID=$!
  HEALTH_T=""
}

# Wait until the service was restarted (the deploy is inside its health wait).
wait_in_health() {
  local _
  for _ in $(seq 1 100); do
    [ "$(restarts)" -ge 1 ] && break
    sleep 0.1
  done
  sleep 0.5
}

no_key_leak() { ! grep -qF "$TEST_KEY" "$OUT" "$ERR"; }
no_torch() { ! grep -q torch "$W/pip.log"; }
says() { grep -q -- "$1" "$OUT" "$ERR"; }
env_line() { sed -n "s/^$1=//p" "$ENVF"; }
other_lines() { grep -v '^IRIS_COMMIT=' "$1"; }

# --- no argument: usage
new_world 0
run_update
check "no argument: exits non-zero" ne "$RC" 0
check "no argument: prints usage" says "Usage"
end_world

# --- 1. good ref deploys and updates IRIS_COMMIT
new_world 1
cp "$ENVF" "$W/env.before"
GOOD_SHA="$(git -C "$W/origin.git" rev-parse good)"
run_update good
check "good ref: exit 0" eq "$RC" 0
check "good ref: HEAD is the ref" eq "$(head_of)" "$GOOD_SHA"
check "good ref: IRIS_COMMIT is the short sha" eq "$(env_line IRIS_COMMIT)" "$(git -C "$APP" rev-parse --short=12 HEAD)"
check "good ref: pip installed requirements-ml" grep -q -- '-r .*server/requirements-ml.txt' "$W/pip.log"
check "good ref: pip never touched torch" no_torch
check "good ref: service restarted" eq "$(restarts)" 1
check "good ref: key not in output" no_key_leak
check "good ref: mode kept 600" eq "$(mode_of "$ENVF")" 0o600
check "good ref: other lines byte-for-byte" diff <(other_lines "$W/env.before") <(other_lines "$ENVF")
check "good ref: key line kept" eq "$(env_line IRIS_API_KEY)" "$TEST_KEY"
end_world

# --- 2. bad ref rolls back
new_world 2
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
run_update bad
check "bad ref: exits non-zero" ne "$RC" 0
check "bad ref: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "bad ref: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
check "bad ref: env file identical after rollback" eq "$(hash_of "$ENVF")" "$(hash_of "$W/env.before")"
check "bad ref: says rolled back" says "rolled back to ${PREV_SHA:0:7}"
check "bad ref: restarted twice (deploy and rollback)" eq "$(restarts)" 2
check "bad ref: installed twice (deploy and rollback)" eq "$(calls "$W/pip.log")" 2
check "bad ref: key not in output" no_key_leak
check "bad ref: mode kept 600" eq "$(mode_of "$ENVF")" 0o600
end_world

# --- 2b. rollback that also fails
new_world 2b
printf '%s\n' "$BAD" >"$APP/health.json"
git -C "$APP" commit -q -am "locally broken head"
run_update bad
check "rollback also bad: exits non-zero" ne "$RC" 0
check "rollback also bad: says so" says "ROLLBACK ALSO FAILED"
end_world

# --- 3. unknown ref changes nothing
new_world 3
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
run_update no-such-ref
check "unknown ref: exits non-zero" ne "$RC" 0
check "unknown ref: HEAD unchanged" eq "$(head_of)" "$PREV_SHA"
check "unknown ref: env file identical" eq "$(hash_of "$ENVF")" "$(hash_of "$W/env.before")"
check "unknown ref: no pip calls" eq "$(calls "$W/pip.log")" 0
check "unknown ref: no systemctl calls" eq "$(calls "$W/systemctl.log")" 0
end_world

# --- 4. same ref twice is harmless
new_world 4
run_update good
check "twice: first run exits 0" eq "$RC" 0
HEAD1="$(head_of)"
HASH1="$(hash_of "$ENVF")"
run_update good
check "twice: second run exits 0" eq "$RC" 0
check "twice: same HEAD" eq "$(head_of)" "$HEAD1"
check "twice: same env file" eq "$(hash_of "$ENVF")" "$HASH1"
check "twice: key not in output" no_key_leak
check "twice: second run did not reinstall or restart" eq "$(calls "$W/pip.log") $(restarts)" "1 1"
end_world

# --- 7. a force-moved tag is picked up
new_world 7
run_update rel
check "tag: first deploy exits 0" eq "$RC" 0
(
  cd "$W/seed"
  git checkout -q main
  commit_tree main "$GOOD" c4
  git tag -f rel >/dev/null
  git push -q -f "$W/origin.git" main rel
)
MOVED="$(git -C "$W/origin.git" rev-parse 'rel^{commit}')"
run_update rel
check "tag: moved tag exits 0" eq "$RC" 0
check "tag: HEAD follows the moved tag" eq "$(head_of)" "$MOVED"
end_world

# --- a branch means origin/<branch>; a sha works too
new_world 8
(
  cd "$W/seed"
  git checkout -q main
  commit_tree main "$GOOD" c5
  git push -q "$W/origin.git" main
)
NEWMAIN="$(git -C "$W/origin.git" rev-parse main)"
run_update main
check "branch: follows origin/main" eq "$(head_of)" "$NEWMAIN"
run_update "$(git -C "$W/origin.git" rev-parse good)"
check "sha: deploys the commit" eq "$(head_of)" "$(git -C "$W/origin.git" rev-parse good)"
end_world

owner_of() { python3 -c 'import os, sys; s = os.stat(sys.argv[1]); print(s.st_uid, s.st_gid)' "$1"; }
inode_of() { python3 -c 'import os, sys; print(os.stat(sys.argv[1]).st_ino)' "$1"; }
only_env_file() { [ "$(ls -A "$W/etc")" = "iris.env" ]; }
no_ready_msg() { ! grep -q "ready, auth on" "$OUT" "$ERR"; }

# --- env file: atomic replace, owner and mode kept, no temp files left behind
new_world 9
INODE0="$(inode_of "$ENVF")"
OWNER0="$(owner_of "$ENVF")"
run_update good
check "env atomic: good run leaves no temp file" only_env_file
check "env atomic: inode changed (renamed over)" ne "$(inode_of "$ENVF")" "$INODE0"
check "env atomic: owner kept" eq "$(owner_of "$ENVF")" "$OWNER0"
check "env atomic: mode kept 600" eq "$(mode_of "$ENVF")" 0o600
run_update bad
check "env atomic: bad run leaves no temp file" only_env_file
run_update no-such-ref
check "env atomic: unknown ref leaves no temp file" only_env_file
end_world

# --- a failed pip install fails the deploy and rolls back
new_world 10
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
echo 1 >"$W/pip.fail"
run_update good
check "pip fails: exits non-zero" ne "$RC" 0
check "pip fails: says rolled back" says "rolled back to ${PREV_SHA:0:7}"
check "pip fails: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "pip fails: env file identical after rollback" eq "$(hash_of "$ENVF")" "$(hash_of "$W/env.before")"
check "pip fails: never reports ready" no_ready_msg
check "pip fails: failed deploy did not restart" eq "$(restarts)" 1
end_world

# --- a rollback whose reinstall also fails
new_world 11
echo 2 >"$W/pip.fail"
run_update good
check "pip fails twice: exits non-zero" ne "$RC" 0
check "pip fails twice: says ROLLBACK ALSO FAILED" says "ROLLBACK ALSO FAILED"
end_world

# --- TERM mid-deploy rolls back (health never comes up, so the script is inside its wait)
new_world 12
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
start_update_bg 30 bad
wait_in_health
kill -TERM "$BGPID"
set +e
wait "$BGPID"
RC=$?
set -e
check "TERM: exits non-zero" ne "$RC" 0
check "TERM: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "TERM: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
check "TERM: env file identical" eq "$(hash_of "$ENVF")" "$(hash_of "$W/env.before")"
check "TERM: says rolled back" says "rolled back to ${PREV_SHA:0:7}"
check "TERM: rollback restarted the service" eq "$(restarts)" 2
end_world

# --- a second run is refused while the first holds the lock
new_world 13
PREV_SHA="$(head_of)"
start_update_bg 30 bad
wait_in_health
FIRST_OUT="$OUT"
FIRST_ERR="$ERR"
run_update good
check "lock: concurrent run exits non-zero" ne "$RC" 0
check "lock: concurrent run says already running" says "already running"
check "lock: concurrent run did not restart" eq "$(restarts)" 1
kill -TERM "$BGPID"
set +e
wait "$BGPID"
set -e
OUT="$FIRST_OUT"
ERR="$FIRST_ERR"
check "lock: first run still rolled back" says "rolled back to ${PREV_SHA:0:7}"
check "lock: first run left previous commit" eq "$(head_of)" "$PREV_SHA"
end_world

no_key_in_log() { ! grep -qF "$TEST_KEY" "$W/update.log"; }
log_says() { grep -q -- "$1" "$W/update.log"; }
env_leftovers() { find "$W/etc" -name '.iris.env.*' | wc -l | tr -d ' '; }

# --- health must report the deployed commit
new_world 16
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
echo 0a0a0a0 >"$W/pin_commit" # healthy and ready, but still reporting the old commit
run_update good
check "stale commit: exits non-zero" ne "$RC" 0
check "stale commit: says commit mismatch" says "commit mismatch"
check "stale commit: names expected and reported" says "expected $(git -C "$W/origin.git" rev-parse --short=12 good).*0a0a0a0"
check "stale commit: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "stale commit: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
check "stale commit: says rolled back" says "rolled back to ${PREV_SHA:0:7}"
check "stale commit: key not in output" no_key_leak
end_world

new_world 17
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
: >"$W/no_commit" # /health has no commit field at all
run_update good
check "missing commit: exits non-zero" ne "$RC" 0
check "missing commit: says commit mismatch" says "commit mismatch"
check "missing commit: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "missing commit: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
end_world

new_world 18
run_update good
HEAD1="$(head_of)"
echo "${HEAD1:0:3}dead" >"$W/pin_commit" # re-check of the deployed commit must also match
run_update good
check "re-check: wrong commit fails" ne "$RC" 0
check "re-check: says commit mismatch" says "commit mismatch"
check "re-check: HEAD unchanged" eq "$(head_of)" "$HEAD1"
end_world

# --- commit comparison: 12-char expected, tolerant of shorter hex written by older deploys
set_env_commit() { # set_env_commit <value>; edit in place (test setup only)
  sed -i.bak "s/^IRIS_COMMIT=.*/IRIS_COMMIT=$1/" "$ENVF"
  rm -f "$ENVF.bak"
}

new_world 19
run_update good
HEAD1="$(head_of)"
check "short commit: IRIS_COMMIT written with 12 chars" eq "${#HEAD1}$(env_line IRIS_COMMIT | wc -c | tr -d ' ')" "4013"
set_env_commit "${HEAD1:0:7}" # an older deploy wrote 7 chars
run_update good
check "short commit: 7-char IRIS_COMMIT re-checks OK" eq "$RC" 0
check "short commit: HEAD unchanged" eq "$(head_of)" "$HEAD1"
set_env_commit "${HEAD1:0:7}ffffff" # same first 7, different after
run_update good
check "prefix only: shared 7-char prefix but different commit is a mismatch" ne "$RC" 0
check "prefix only: says commit mismatch" says "commit mismatch"
set_env_commit "${HEAD1:0:6}" # too short to trust
run_update good
check "too short: 6-char commit is a mismatch" ne "$RC" 0
set_env_commit "${HEAD1:0:7}"
echo "${HEAD1:0:7}zzzzz" >"$W/pin_commit" # not hex
run_update good
check "non-hex: reported commit with non-hex chars is a mismatch" ne "$RC" 0
end_world

# --- SIGHUP (dropped SSH session) rolls back even though the terminal is gone
new_world 14
cp "$ENVF" "$W/env.before"
PREV_SHA="$(head_of)"
cat >"$W/pty_run.py" <<'PY'
import fcntl, os, pty, select, subprocess, sys, termios, time

# Run argv with a fresh pty as stdin/stdout/stderr and controlling tty; once the service was
# restarted (logfile has "restart iris"), close the pty master like sshd hanging up.
logfile, argv = sys.argv[1], sys.argv[2:]
master, slave = pty.openpty()
proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                        preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
os.close(slave)
start = time.time()
while time.time() - start < 20 and "restart iris" not in open(logfile).read():
    if select.select([master], [], [], 0.1)[0]:
        os.read(master, 4096)
time.sleep(0.5)
os.close(master)
sys.exit(proc.wait(timeout=90))
PY
HEALTH_T=30
upd_args
set +e
"${UPD[@]}" python3 "$W/pty_run.py" "$W/systemctl.log" bash "$UPDATE" bad >/dev/null 2>&1
RC=$?
set -e
HEALTH_T=""
check "hangup: exits non-zero" ne "$RC" 0
check "hangup: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "hangup: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
check "hangup: env file identical" eq "$(hash_of "$ENVF")" "$(hash_of "$W/env.before")"
check "hangup: two restarts (deploy and rollback)" eq "$(restarts)" 2
check "hangup: update log says rolled back" log_says "rolled back to ${PREV_SHA:0:7}"
check "hangup: key not in update log" no_key_in_log
end_world

check "hangup: fresh update log is mode 600" eq "$(mode_of "$W/update.log")" 0o600

# --- stdout is a pipe whose reader goes away (ssh without a pty dropped): still rolls back.
# Run twice: SIGPIPE default, and SIGPIPE ignored at startup (as on GitHub runners), where bash
# cannot trap it and writes fail with EPIPE instead.
reader_that_leaves() { # wait until the service was restarted, then exit and close the pipe
  local _
  for _ in $(seq 1 100); do
    [ "$(restarts)" -ge 1 ] && break
    sleep 0.1
  done
  sleep 0.5
}
closed_pipe_case() { # closed_pipe_case <label> <world> <ignore-sigpipe 0|1>
  local label="$1"
  new_world "$2"
  local ign="$3" launcher=()
  cp "$ENVF" "$W/env.before"
  PREV_SHA="$(head_of)"
  printf 'import os, signal, sys\nsignal.signal(signal.SIGPIPE, signal.SIG_IGN)\nos.execvp(sys.argv[1], sys.argv[1:])\n' >"$W/ign_sigpipe.py"
  [ "$ign" = 1 ] && launcher=(python3 "$W/ign_sigpipe.py")
  HEALTH_T=4
  upd_args
  set +e
  "${UPD[@]}" ${launcher[@]+"${launcher[@]}"} bash "$UPDATE" bad 2>&1 | reader_that_leaves
  RC=${PIPESTATUS[0]}
  set -e
  HEALTH_T=""
  check "$label: exits non-zero" ne "$RC" 0
  check "$label: not killed by SIGPIPE" ne "$RC" 141
  check "$label: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
  check "$label: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
  check "$label: two restarts (deploy and rollback)" eq "$(restarts)" 2
  check "$label: update log says rolled back" log_says "rolled back to ${PREV_SHA:0:7}"
  check "$label: key not in update log" no_key_in_log
  end_world
}
closed_pipe_case "closed pipe" 22 0
closed_pipe_case "closed pipe, SIGPIPE ignored" 23 1

# --- TERM during the env rewrite leaves no temp copy of the key
new_world 15
mkdir -p "$W/slow"
printf '#!/usr/bin/env bash\nsleep 2\n' >"$W/slow/sync"
chmod +x "$W/slow/sync"
PREV_SHA="$(head_of)"
EXTRA_PATH="$W/slow"
start_update_bg 30 good
EXTRA_PATH=""
for _ in $(seq 1 100); do
  [ "$(env_leftovers)" -ge 1 ] && break
  sleep 0.05
done
check "rewrite: temp file existed mid-rewrite" eq "$([ "$(env_leftovers)" -ge 1 ] && echo yes)" yes
kill -TERM "$BGPID"
set +e
wait "$BGPID"
RC=$?
set -e
check "rewrite TERM: exits non-zero" ne "$RC" 0
check "rewrite TERM: no temp file left" eq "$(env_leftovers)" 0
check "rewrite TERM: previous commit checked out" eq "$(head_of)" "$PREV_SHA"
check "rewrite TERM: previous IRIS_COMMIT restored" eq "$(env_line IRIS_COMMIT)" 0a0a0a0
end_world

echo "flock: $FLOCK_KIND (real = util-linux, shim = python stand-in); skipped: none"
printf '\n%d passed, %d failed\n' "$PASSED" "$FAILED"
[ "$FAILED" -eq 0 ]

#!/usr/bin/env bash
# The `laya` job of .github/workflows/ci.yml: live laya-serve plus in-process laya.Router.
set -euo pipefail

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "start laya-serve (${LAYA_MODELS}, revision ${LAYA_REVISION})"
laya-serve > /tmp/laya-serve.log 2>&1 &
trap 'kill %1 2>/dev/null || true' EXIT
python - <<'EOF' || { cat /tmp/laya-serve.log; exit 1; }
import json, os, time, urllib.request
url = os.environ["SURETY_LAYA_URL"] + "/health"
for _ in range(180):
    try:
        print(json.dumps(json.load(urllib.request.urlopen(url, timeout=5))["revisions"]))
        raise SystemExit(0)
    except OSError:
        time.sleep(2)
raise SystemExit("laya-serve did not become healthy")
EOF

step "pytest -m laya";            pytest -m laya -v
step "example against laya-serve"; python examples/jev_vs_laya/run.py --skip-jev --laya-url "$SURETY_LAYA_URL" --limit "${LAYA_ROWS:-60}"

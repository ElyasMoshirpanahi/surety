#!/usr/bin/env bash
# Run the CI pipeline locally in Docker, same jobs as .github/workflows/ci.yml.
#
#   ci/local.sh            test matrix: Python 3.10, 3.11, 3.12, 3.13
#   ci/local.sh slow       Monte Carlo checks of the guarantee and drift detector
#   ci/local.sh laya       live Laya (CPU PyTorch, laya-serve, model weights cached in a volume)
#   ci/local.sh all        all three
#   PYTHONS="3.13" ci/local.sh test     one Python only
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHONS=${PYTHONS:-"3.10 3.11 3.12 3.13"}
export DOCKER_BUILDKIT=1
results=()

run_job() {  # name, then the command
  local name=$1; shift
  printf '\n\033[1;36m### %s\033[0m\n' "$name"
  local start=$SECONDS
  if "$@"; then results+=("PASS  $name  ($((SECONDS - start))s)")
  else results+=("FAIL  $name  ($((SECONDS - start))s)"); fi
}

test_job() {
  for py in $PYTHONS; do
    run_job "test (python $py)" sh -c \
      "docker build -q -f ci/Dockerfile --target test --build-arg PY=$py -t surety-ci:py$py . >/dev/null \
       && docker run --rm surety-ci:py$py"
  done
}

slow_job() {
  run_job "slow (python 3.13)" sh -c \
    "docker build -q -f ci/Dockerfile --target test --build-arg PY=3.13 -t surety-ci:py3.13 . >/dev/null \
     && docker run --rm surety-ci:py3.13 pytest -m slow -q"
}

laya_job() {
  run_job "laya (live, CPU)" sh -c \
    "docker build -q -f ci/Dockerfile --target laya -t surety-ci:laya . >/dev/null \
     && docker run --rm -v surety-hf-cache:/root/.cache/huggingface -e LAYA_ROWS=${LAYA_ROWS:-60} surety-ci:laya"
}

case "${1:-test}" in
  test) test_job ;;
  slow) slow_job ;;
  laya) laya_job ;;
  all)  test_job; slow_job; laya_job ;;
  *)    sed -n '2,9p' "$0"; exit 2 ;;
esac

printf '\n\033[1mSummary\033[0m\n'
printf '  %s\n' "${results[@]}"
! printf '%s\n' "${results[@]}" | grep -q '^FAIL'

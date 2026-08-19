#!/usr/bin/env bash
# Full automated suite (NEXT_STEP_REQUIREMENTS.md §5.3). Brings up the full
# docker-compose stack (rebuild included, unless SKIP_DOCKER_BUILD=1 is set
# for faster local iteration on an already-running stack) and runs every
# service's tests against it.
#
# Excludes test_payment_unsafe_mode.py on purpose -- see
# scripts/test_unsafe_idempotency.sh for why that one runs separately.
set -euo pipefail
cd "$(dirname "$0")/.."

pip install -q -r requirements-test.txt

pytest services/*/tests -v \
  --ignore=services/payment-service/tests/test_payment_unsafe_mode.py \
  "$@"

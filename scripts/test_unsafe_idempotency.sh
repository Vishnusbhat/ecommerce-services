#!/usr/bin/env bash
# Deliberate exception to "one command runs everything" (NEXT_STEP_REQUIREMENTS.md
# §5.2): proves payment-service's UNSAFE_IDEMPOTENCY_MODE genuinely
# reproduces the double-charge race, which is what makes the *safe* mode's
# protection (asserted in test_payment.py, part of the main suite) meaningful
# rather than vacuous. Restarts the shared payment-service container with a
# different config, so it can't safely share a pytest run with tests that
# assume the default safe behavior -- hence its own script.
set -euo pipefail
cd "$(dirname "$0")/.."

pip install -q -r requirements-test.txt

cp .env .env.unsafe-test-backup
restore() {
  mv .env.unsafe-test-backup .env
  docker compose up -d payment-service >/dev/null 2>&1
  echo "restored payment-service to safe defaults"
}
trap restore EXIT

grep -v '^UNSAFE_IDEMPOTENCY_MODE=\|^PAYMENT_LATENCY_MS_MIN=\|^PAYMENT_LATENCY_MS_MAX=' .env > .env.tmp
mv .env.tmp .env
cat >> .env <<'EOF'
UNSAFE_IDEMPOTENCY_MODE=true
PAYMENT_LATENCY_MS_MIN=300
PAYMENT_LATENCY_MS_MAX=600
EOF

docker compose up -d --build payment-service
sleep 3

pytest services/payment-service/tests/test_payment_unsafe_mode.py -v

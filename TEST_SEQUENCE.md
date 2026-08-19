# Manual Test Sequence

A single ordered walkthrough to confirm the whole system works, start to
finish. Steps 1–21 are one continuous chain — each depends on shell
variables set by earlier steps (`$TOKEN`, `$ORDER_ID`, etc.) — run them all
in **one terminal session**, not as separate copy-pastes into fresh shells,
or the variables won't carry over. Steps after that are independent
branches you can run in any order, each self-contained, some requiring a
brief service restart (clearly marked).

Verified live end-to-end against a running stack while writing this doc —
every expected output below is a real observed result, not a guess.

For per-endpoint reference (every field, every error code) see
`API_REFERENCE.md`. This file is the "just tell me what to type, in what
order" version.

## Prerequisites

```bash
cp .env.example .env   # if not already done
docker compose up -d --build
sleep 5
for p in 8001 8002 8003 8004 8005 8006 8007; do curl -s http://localhost:$p/healthz/ready; echo " <- $p"; done
```
All 7 must show `{"status":"ready"}` before continuing.

---

## Core sequence (run in order)

**1. Register a user**
```bash
EMAIL="test_$(date +%s)@example.com"
curl -s -X POST http://localhost:8001/auth/register \
  -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"password123\"}"
```
Expect `201`, `{"id": "<uuid>", "email": "..."}`.

**2. Log in, capture tokens**
```bash
LOGIN=$(curl -s -X POST http://localhost:8001/auth/login \
  -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"password123\"}")
TOKEN=$(echo $LOGIN | python3 -c "import json,sys;print(json.load(sys.stdin)['accessToken'])")
REFRESH=$(echo $LOGIN | python3 -c "import json,sys;print(json.load(sys.stdin)['refreshToken'])")
echo "$LOGIN"
```
Expect `200`, `accessToken`/`refreshToken`/`tokenType: "bearer"`/`expiresIn: 900`.

**3. Browse the catalog**
```bash
curl -s "http://localhost:8002/catalog/products?limit=5"
```
Expect `200`, a list including `P001`–`P005` with `stock`/`price_cents`.

**4. Empty cart**
```bash
curl -s http://localhost:8003/cart -H "Authorization: Bearer $TOKEN"
```
Expect `200`, `{"items": []}`.

**5. Add two products to the cart**
```bash
curl -s -X POST http://localhost:8003/cart/items -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"productId":"P001","quantity":2}'
curl -s -X POST http://localhost:8003/cart/items -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"productId":"P002","quantity":1}'
```
Expect `200` each time, returning the growing cart.

**6. Confirm cart contents**
```bash
curl -s http://localhost:8003/cart -H "Authorization: Bearer $TOKEN"
```
Expect both `P001` (qty 2) and `P002` (qty 1).

**7. Checkout-intent (read-only preview)**
```bash
curl -s -X POST http://localhost:8003/cart/checkout-intent -H "Authorization: Bearer $TOKEN"
```
Expect `200`, same items as step 6 — cart is **not** cleared by this call.

**8. Check out (cart-sourced, empty body)**
```bash
IDEM_KEY="seq-$(date +%s)"
ORDER=$(curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $IDEM_KEY" -H 'Content-Type: application/json' -d '{}')
ORDER_ID=$(echo $ORDER | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "$ORDER"
```
Expect `201`, `status: "PAID"`, `amountCents` = sum of the two products'
prices, `items` matching the cart.

**9. Confirm cart is now empty (post-checkout clearing)**
```bash
curl -s http://localhost:8003/cart -H "Authorization: Bearer $TOKEN"
```
Expect `{"items": []}` — order-service cleared it after the `PAID` result.

**10. Fetch the order by id**
```bash
curl -s http://localhost:8004/orders/$ORDER_ID -H "Authorization: Bearer $TOKEN"
```
Expect `200`, identical to step 8's response.

**11. List orders**
```bash
curl -s http://localhost:8004/orders -H "Authorization: Bearer $TOKEN"
```
Expect `200`, `items` containing the order from step 8.

**12. Confirm stock was actually decremented**
```bash
curl -s http://localhost:8002/catalog/products/P001
curl -s http://localhost:8002/catalog/products/P002
```
Expect `stock` down by 2 and 1 respectively from step 3's numbers.

**13. Retry the same order with the same Idempotency-Key**
```bash
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $IDEM_KEY" -H 'Content-Type: application/json' -d '{}'
```
Expect the **same** `id` as step 8's response, byte-identical body — and
stock (re-check step 12's numbers) unchanged by this retry, since it never
re-ran the saga.

**14. Tail notification-service to see the async events land**
```bash
docker compose logs notification-service --tail 10
```
Expect log lines: `notification: Order <id> received` and
`notification: Order <id> paid — <amount>`.

**15. Try to review before delivery (should be rejected)**
```bash
curl -s -X POST http://localhost:8007/reviews -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"productId\":\"P002\",\"orderId\":\"$ORDER_ID\",\"rating\":5,\"comment\":\"too soon\"}"
```
Expect `403`, `{"error":{"code":"NOT_ELIGIBLE",...}}`.

**16. Wait for the simulated delivery, then submit the review**
```bash
sleep 35   # DELIVERY_SIMULATION_DELAY_SECONDS default is 30s
curl -s -X POST http://localhost:8007/reviews -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"productId\":\"P002\",\"orderId\":\"$ORDER_ID\",\"rating\":5,\"comment\":\"great\"}"
```
Expect `201`, the created review.

**17. Confirm a second review for the same product is blocked**
```bash
curl -s -X POST http://localhost:8007/reviews -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"productId\":\"P002\",\"orderId\":\"$ORDER_ID\",\"rating\":3,\"comment\":\"again\"}"
```
Expect `403 NOT_ELIGIBLE` (already reviewed).

**18. List reviews for the product**
```bash
curl -s http://localhost:8007/reviews/product/P002
```
Expect `200`, containing step 16's review.

**19. Refresh the access token**
```bash
NEW=$(curl -s -X POST http://localhost:8001/auth/refresh -H 'Content-Type: application/json' \
  -d "{\"refreshToken\":\"$REFRESH\"}")
NEW_TOKEN=$(echo $NEW | python3 -c "import json,sys;print(json.load(sys.stdin)['accessToken'])")
NEW_REFRESH=$(echo $NEW | python3 -c "import json,sys;print(json.load(sys.stdin)['refreshToken'])")
echo "$NEW"
```
Expect `200`, a new token pair.

**20. Confirm the old refresh token is now dead (rotation)**
```bash
curl -s -X POST http://localhost:8001/auth/refresh -H 'Content-Type: application/json' \
  -d "{\"refreshToken\":\"$REFRESH\"}"
```
Expect `401 INVALID_TOKEN`.

**21. Log out**
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8001/auth/logout \
  -H "Authorization: Bearer $NEW_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"refreshToken\":\"$NEW_REFRESH\"}"
curl -s -X POST http://localhost:8001/auth/refresh -H 'Content-Type: application/json' \
  -d "{\"refreshToken\":\"$NEW_REFRESH\"}"
```
Expect `204` then `401 INVALID_TOKEN` — logout revoked it immediately.

**Core sequence passed if every expectation above matched.**

---

## Branch A — Insufficient stock (independent; needs a logged-in user)

```bash
TOKEN=<any valid access token>
curl -s http://localhost:8002/catalog/products/P003   # note current stock
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: branch-a-$(date +%s)" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P003","quantity":99999}]}'
curl -s http://localhost:8002/catalog/products/P003   # confirm unchanged
```
Expect the order response: `status: "FAILED"`,
`failureReason: "INSUFFICIENT_STOCK"`; stock identical before/after.

---

## Branch B — Payment decline & compensation (restarts payment-service)

```bash
sed -i.bak 's/PAYMENT_FAILURE_RATE=0.0/PAYMENT_FAILURE_RATE=1.0/' .env
docker compose up -d payment-service && sleep 2

TOKEN=<any valid access token>
curl -s http://localhost:8002/catalog/products/P004   # note stock
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: branch-b-$(date +%s)" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P004","quantity":1}]}'
curl -s http://localhost:8002/catalog/products/P004   # confirm stock released back

mv .env.bak .env
docker compose up -d payment-service && sleep 2   # restore before continuing
```
Expect: `status: "FAILED"`, `failureReason: "PAYMENT_DECLINED"`; `P004`
stock unchanged (reserved, then compensated). **Always restore `.env`
before running anything else** — other flows assume the safe default.

---

## Branch C — Concurrency: no overselling (needs direct DB access)

```bash
docker compose exec mariadb mariadb -uroot -prootpass \
  -e "UPDATE catalog_db.products SET stock=1 WHERE id='P005';"

TOK=dev-internal-token-change-me
for i in 1 2 3 4 5; do
  curl -s -o /tmp/race_$i.json -w "%{http_code}\n" -X POST http://localhost:8002/catalog/stock/reserve \
    -H "X-Internal-Token: $TOK" -H "X-Internal-Caller: order-service" \
    -H 'Content-Type: application/json' \
    -d "{\"productId\":\"P005\",\"quantity\":1,\"orderId\":\"race-$i\"}" &
done; wait
cat /tmp/race_*.json
curl -s http://localhost:8002/catalog/products/P005
```
Expect exactly one `200` and four `409`s among the five status codes
printed; final `stock: 0`.

---

## Branch D — Concurrency: no double charge

```bash
TOK=dev-internal-token-change-me
KEY="branch-d-$(date +%s)"
for i in 1 2 3 4 5; do
  curl -s -o /tmp/charge_$i.json -X POST http://localhost:8005/payments/charge \
    -H "X-Internal-Token: $TOK" -H "X-Internal-Caller: order-service" \
    -H 'Content-Type: application/json' \
    -d "{\"orderId\":\"O-branch-d\",\"amount\":1000,\"currency\":\"INR\",\"idempotencyKey\":\"$KEY\"}" &
done; wait
cat /tmp/charge_*.json
```
Expect all 5 response bodies **byte-identical** (same `chargedAt`) — proof
no double charge occurred.

---

## Branch E — Poison pill → dead-letter queue

```bash
echo "not valid json {{{" | docker compose exec -T kafka \
  /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9092 \
  --topic order-events --property "parse.key=false"

sleep 8
docker compose logs notification-service --tail 10

docker compose exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic order-events-dlq --from-beginning --timeout-ms 5000
```
Expect 3× `processing_failed` then one `sending_to_dlq` in the logs, and
the malformed message printed by the DLQ consumer.

---

## Branch F — Business metrics sanity check

```bash
curl -s http://localhost:8004/metrics | grep -E "^orders_|^saga_|^order_amount_cents_count"
curl -s http://localhost:8005/metrics | grep -E "^payment_"
curl -s http://localhost:8002/metrics | grep -E "^stock_reservation|^catalog_cache"
curl -s http://localhost:8003/metrics | grep -E "^cart_"
```
Expect non-zero counters for anything exercised by the core sequence /
branches above (e.g. `orders_paid_total`, `catalog_cache_hits_total`).

---

## Branch G — Automated version of all of the above

```bash
./scripts/run_tests.sh                    # everything above, scripted + asserted
./scripts/test_unsafe_idempotency.sh      # proves the double-charge race is real without the safe NX claim
```

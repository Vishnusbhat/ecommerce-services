"""Business metrics for order-service (NEXT_STEP_REQUIREMENTS.md §1).

Colocated here rather than in gestalt_shared/metrics.py -- that module is
imported by all 7 services, and these counters are order-service's alone;
registering them there would make e.g. orders_paid_total show up (frozen at
0) on cart-service's /metrics too. Same pattern payment-service already
uses for PAYMENT_FAILURES_TOTAL.
"""
from prometheus_client import Counter, Histogram

ORDERS_CREATED_TOTAL = Counter("orders_created_total", "Orders persisted as PENDING")
ORDERS_PAID_TOTAL = Counter("orders_paid_total", "Orders that reached PAID")
ORDERS_FAILED_TOTAL = Counter("orders_failed_total", "Orders that reached FAILED", ["reason"])
SAGA_STOCK_RESERVATION_FAILURES_TOTAL = Counter(
    "saga_stock_reservation_failures_total", "Saga failures at the stock-reservation step"
)
SAGA_PAYMENT_FAILURES_TOTAL = Counter(
    "saga_payment_failures_total", "Saga failures at the payment-charge step"
)
ORDER_AMOUNT_CENTS = Histogram("order_amount_cents", "Amount of PAID orders, in cents")

# Closed label set required by NEXT_STEP_REQUIREMENTS.md §1.2. The raw
# reason strings clients.py returns (transport errors, upstream status
# codes) have unbounded cardinality, so this maps every raw value into one
# of the 5 canonical buckets -- used for both the Prometheus label and what
# actually gets stored in orders.failure_reason, so the two can never drift
# apart (per §1.2 row 3: the label must equal the stored value).
_EXACT_REASONS = {
    "insufficient_stock": "INSUFFICIENT_STOCK",
    "product_not_found": "PRODUCT_NOT_FOUND",
    "PAYMENT_DECLINED": "PAYMENT_DECLINED",
    "payment_declined": "PAYMENT_DECLINED",
    "reconciliation_timeout": "RECONCILIATION_TIMEOUT",
}


def normalize_failure_reason(raw: str) -> str:
    if raw in _EXACT_REASONS:
        return _EXACT_REASONS[raw]
    if raw.startswith("transport_error") or raw.startswith("upstream_error_"):
        return "TRANSPORT_ERROR"
    return "TRANSPORT_ERROR"

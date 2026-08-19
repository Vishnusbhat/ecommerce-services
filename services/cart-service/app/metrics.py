from prometheus_client import Counter

CART_ITEMS_ADDED_TOTAL = Counter("cart_items_added_total", "Successful POST /cart/items calls")
CART_ABANDONMENT_TOTAL = Counter(
    "cart_abandonment_total",
    "Carts with items left unmodified past the abandonment threshold with no checkout",
)

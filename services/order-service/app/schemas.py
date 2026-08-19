from pydantic import BaseModel, Field


class OrderItemIn(BaseModel):
    productId: str
    quantity: int = Field(gt=0)


class CreateOrderRequest(BaseModel):
    # Optional: if omitted, order-service fetches the caller's cart from
    # cart-service (matches the empty-body POST /orders in
    # docs/08-load-testing.md's K6 script). Explicit items lets order-service
    # be exercised/tested standalone before cart-service exists.
    items: list[OrderItemIn] | None = None


class OrderItemOut(BaseModel):
    productId: str
    quantity: int


class OrderOut(BaseModel):
    id: str
    userId: str
    status: str
    amountCents: int
    items: list[OrderItemOut]
    failureReason: str | None = None
    createdAt: str


class OrderListOut(BaseModel):
    items: list[OrderOut]

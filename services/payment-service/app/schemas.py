from pydantic import BaseModel, Field


class ChargeRequest(BaseModel):
    orderId: str
    amount: int = Field(gt=0)
    currency: str = "INR"
    idempotencyKey: str


class ChargeResponse(BaseModel):
    orderId: str
    amount: int
    currency: str
    idempotencyKey: str
    status: str
    chargedAt: str

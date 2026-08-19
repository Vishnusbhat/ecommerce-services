from pydantic import BaseModel, Field


class ReviewIn(BaseModel):
    productId: str
    orderId: str | None = None
    rating: int = Field(ge=1, le=5)
    comment: str = ""


class ReviewOut(BaseModel):
    id: str
    productId: str
    userId: str
    orderId: str
    rating: int
    comment: str
    createdAt: str


class ReviewListOut(BaseModel):
    items: list[ReviewOut]

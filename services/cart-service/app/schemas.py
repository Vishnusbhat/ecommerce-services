from pydantic import BaseModel, Field


class CartItemOut(BaseModel):
    productId: str
    quantity: int


class CartOut(BaseModel):
    items: list[CartItemOut]


class AddItemRequest(BaseModel):
    productId: str
    quantity: int = Field(gt=0)

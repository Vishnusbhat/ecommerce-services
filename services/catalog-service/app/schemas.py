from pydantic import BaseModel, Field


class ProductOut(BaseModel):
    id: str
    name: str
    price_cents: int
    stock: int


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


class PriceOut(BaseModel):
    id: str
    price_cents: int
    stock: int


class ReserveRequest(BaseModel):
    productId: str
    quantity: int = Field(gt=0)
    orderId: str


class ReleaseRequest(BaseModel):
    productId: str
    quantity: int = Field(gt=0)
    orderId: str


class ReserveResponse(BaseModel):
    productId: str
    orderId: str
    reserved: bool
    remainingStock: int

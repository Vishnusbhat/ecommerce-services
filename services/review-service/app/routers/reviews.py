from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status

from app.db import eligibility_collection, reviews_collection
from app.schemas import ReviewIn, ReviewListOut, ReviewOut
from app.security import current_user_dependency
from gestalt_shared.errors import AppError
from gestalt_shared.security import TokenClaims

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _to_out(doc: dict) -> ReviewOut:
    return ReviewOut(
        id=str(doc["_id"]),
        productId=doc["productId"],
        userId=doc["userId"],
        orderId=doc["orderId"],
        rating=doc["rating"],
        comment=doc.get("comment", ""),
        createdAt=doc["createdAt"].isoformat() if hasattr(doc["createdAt"], "isoformat") else doc["createdAt"],
    )


@router.get("/product/{product_id}", response_model=ReviewListOut)
def list_reviews(product_id: str):
    docs = reviews_collection.find({"productId": product_id}).sort("createdAt", -1)
    return ReviewListOut(items=[_to_out(d) for d in docs])


@router.post("", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def create_review(body: ReviewIn, user: TokenClaims = Depends(current_user_dependency)):
    eligibility_id = f"{user.user_id}:{body.productId}"
    eligibility = eligibility_collection.find_one({"_id": eligibility_id, "reviewed": False})
    if eligibility is None:
        raise AppError(
            "NOT_ELIGIBLE",
            "You can only review products from your own delivered orders",
            403,
        )

    now = datetime.now(timezone.utc)
    doc = {
        "productId": body.productId,
        "userId": user.user_id,
        "orderId": body.orderId or eligibility["orderId"],
        "rating": body.rating,
        "comment": body.comment,
        "createdAt": now,
    }
    result = reviews_collection.insert_one(doc)
    doc["_id"] = result.inserted_id

    eligibility_collection.update_one({"_id": eligibility_id}, {"$set": {"reviewed": True}})
    return _to_out(doc)

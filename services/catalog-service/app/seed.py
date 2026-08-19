"""Seed a handful of demo products on first boot (empty table only) so the
K6 checkout script and manual smoke tests have something to browse/reserve."""
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Product

DEMO_PRODUCTS = [
    Product(id="P001", name="Wireless Mouse", price_cents=1999, stock=100),
    Product(id="P002", name="Mechanical Keyboard", price_cents=6999, stock=50),
    Product(id="P003", name="USB-C Hub", price_cents=2999, stock=75),
    Product(id="P004", name="27in Monitor", price_cents=24999, stock=20),
    Product(id="P005", name="Laptop Stand", price_cents=3499, stock=60),
]


def seed_if_empty() -> None:
    db = SessionLocal()
    try:
        existing = db.execute(select(Product.id)).first()
        if existing is None:
            db.add_all(DEMO_PRODUCTS)
            db.commit()
    finally:
        db.close()

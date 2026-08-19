from pymongo import MongoClient

from app.config import settings

_client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
mongo_db = _client[settings.mongo_db]
reviews_collection = mongo_db["reviews"]
eligibility_collection = mongo_db["eligibility"]

reviews_collection.create_index("productId")


def mongo_is_ready() -> bool:
    try:
        _client.admin.command("ping")
        return True
    except Exception:
        return False

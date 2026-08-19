from app.config import settings
from gestalt_shared.security import JWKSClient, make_current_user_dependency

jwks_client = JWKSClient(settings.auth_jwks_url)
current_user_dependency = make_current_user_dependency(jwks_client)

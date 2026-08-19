from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mariadb_host: str = "mariadb"
    mariadb_port: int = 3306
    mariadb_db: str = "catalog_db"
    mariadb_user: str = "catalog_svc"
    mariadb_password: str = "catalog_pass"

    redis_host: str = "redis-catalog"
    redis_port: int = 6379
    product_cache_ttl_seconds: int = 60

    auth_jwks_url: str = "http://auth-service:8080/auth/.well-known/jwks.json"

    internal_service_token: str = "dev-internal-token-change-me"

    log_level: str = "info"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mariadb_user}:{self.mariadb_password}"
            f"@{self.mariadb_host}:{self.mariadb_port}/{self.mariadb_db}"
        )


settings = Settings()

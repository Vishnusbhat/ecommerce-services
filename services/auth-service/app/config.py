from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mariadb_host: str = "mariadb"
    mariadb_port: int = 3306
    mariadb_db: str = "auth_db"
    mariadb_user: str = "auth_svc"
    mariadb_password: str = "auth_pass"

    redis_host: str = "redis-auth"
    redis_port: int = 6379

    jwt_issuer: str = "gestalt-commerce-auth"
    jwt_access_token_ttl_seconds: int = 900
    jwt_refresh_token_ttl_seconds: int = 1_209_600

    keys_dir: str = "/app/keys"

    log_level: str = "info"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mariadb_user}:{self.mariadb_password}"
            f"@{self.mariadb_host}:{self.mariadb_port}/{self.mariadb_db}"
        )


settings = Settings()

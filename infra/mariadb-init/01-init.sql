-- Database-per-service, enforced here at the credential level: each service
-- gets its own database AND its own user with grants scoped to only that
-- database. docs/01-architecture-overview.md: "No service reaches into
-- another service's database." One physical MariaDB instance locally
-- (mirrors the single `mariadb` StatefulSet in docs/03), but no service's
-- credentials can even authenticate against another service's schema.
--
-- NOTE: usernames/passwords here must match .env.example. This file only
-- runs on a fresh volume (MariaDB skips /docker-entrypoint-initdb.d on
-- reruns against existing data).

CREATE DATABASE IF NOT EXISTS auth_db;
CREATE USER IF NOT EXISTS 'auth_svc'@'%' IDENTIFIED BY 'auth_pass';
GRANT ALL PRIVILEGES ON auth_db.* TO 'auth_svc'@'%';

CREATE DATABASE IF NOT EXISTS catalog_db;
CREATE USER IF NOT EXISTS 'catalog_svc'@'%' IDENTIFIED BY 'catalog_pass';
GRANT ALL PRIVILEGES ON catalog_db.* TO 'catalog_svc'@'%';

CREATE DATABASE IF NOT EXISTS orders_db;
CREATE USER IF NOT EXISTS 'order_svc'@'%' IDENTIFIED BY 'order_pass';
GRANT ALL PRIVILEGES ON orders_db.* TO 'order_svc'@'%';

FLUSH PRIVILEGES;

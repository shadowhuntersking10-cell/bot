-- VYRON MySQL / MariaDB setup. Run ONCE as an administrative user:
--   mysql -u root -p < deploy/mysql_setup.sql
-- Replace the password below with a long random one, then put the same
-- values into .env (DB_USER / DB_PASSWORD). VYRON never connects as root.

CREATE DATABASE IF NOT EXISTS vyron
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'vyron_app'@'localhost' IDENTIFIED BY 'CHANGE_ME_long_random_password';
CREATE USER IF NOT EXISTS 'vyron_app'@'127.0.0.1' IDENTIFIED BY 'CHANGE_ME_long_random_password';

-- Least privilege: only the VYRON schema. (ALTER/CREATE/INDEX are needed
-- because VYRON creates and upgrades its own tables on start.)
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON vyron.* TO 'vyron_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON vyron.* TO 'vyron_app'@'127.0.0.1';

FLUSH PRIVILEGES;

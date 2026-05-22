#!/bin/bash
# PostgreSQL multi-database init script
# Creates separate databases for each EVEZ service
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
$(echo "$POSTGRES_MULTIPLE_DATABASES" | tr ',' '\n' | while read -r db; do
    echo "CREATE DATABASE $db;"
    echo "GRANT ALL PRIVILEGES ON DATABASE $db TO $POSTGRES_USER;"
done)
EOSQL

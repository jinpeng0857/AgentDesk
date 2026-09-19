#!/bin/bash
set -e
service postgresql start
service redis-server start

su - postgres -c "psql -c \"CREATE USER agentdesk WITH SUPERUSER PASSWORD 'agentdesk';\"" 2>/dev/null || true
su - postgres -c "psql -c \"CREATE DATABASE agentdesk OWNER agentdesk;\"" 2>/dev/null || true
su - postgres -c "psql -c \"CREATE DATABASE agentdesk_test OWNER agentdesk;\"" 2>/dev/null || true

PG_CONF=$(find /etc/postgresql/ -name postgresql.conf | head -n 1)
PG_HBA=$(find /etc/postgresql/ -name pg_hba.conf | head -n 1)

echo "listen_addresses = '*'" >> "$PG_CONF"
echo "host all all 0.0.0.0/0 md5" >> "$PG_HBA"
echo "host all all ::0/0 md5" >> "$PG_HBA"
echo "host all all 127.0.0.1/32 trust" >> "$PG_HBA"
echo "host all all ::1/128 trust" >> "$PG_HBA"

service postgresql restart
service redis-server restart
echo "SERVICES_CONFIGURED_AND_RUNNING"

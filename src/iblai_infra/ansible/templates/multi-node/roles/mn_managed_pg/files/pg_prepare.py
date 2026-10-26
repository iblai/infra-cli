"""Prepare a managed Postgres for the DM. Idempotent; prints no secrets.

Runs inside the DM image (which ships a Postgres driver), configured by env:
PG_HOST PG_PORT PG_SSLMODE PG_ADMIN_USER PG_ADMIN_PW (the service's master
login), DM_USER DM_PW DM_DB LF_DB (from the hub's config), WANT_TIMESCALEDB.

Creates or syncs the DM's login role, gives the master user membership in it
(managed services make the master a non-superuser, and it must be able to
hand database ownership over), creates the DM and Langfuse databases owned
by it, and the extensions the DM needs. Then proves the DM login works.
"""
import os
import sys

try:
    import psycopg

    def connect(user, password, db):
        return psycopg.connect(host=E["PG_HOST"], port=E["PG_PORT"], user=user, password=password,
                               dbname=db, sslmode=E["PG_SSLMODE"], connect_timeout=15, autocommit=True)

    from psycopg import sql
except ImportError:  # older DM images ship psycopg2
    import psycopg2 as psycopg
    from psycopg2 import sql

    def connect(user, password, db):
        c = psycopg.connect(host=E["PG_HOST"], port=E["PG_PORT"], user=user, password=password,
                            dbname=db, sslmode=E["PG_SSLMODE"], connect_timeout=15)
        c.autocommit = True
        return c

E = os.environ


def run(conn, query, *params):
    with conn.cursor() as cur:
        cur.execute(query, params or None)
        return cur.fetchall() if cur.description else []


admin_user, user = E["PG_ADMIN_USER"], E["DM_USER"]
c = connect(admin_user, E["PG_ADMIN_PW"], "postgres")
role = sql.Identifier(user)
if run(c, "SELECT 1 FROM pg_roles WHERE rolname = %s", user):
    run(c, sql.SQL("ALTER ROLE {} WITH LOGIN CREATEROLE PASSWORD {}").format(role, sql.Literal(E["DM_PW"])))
    print(f"role {user}: exists, password synced")
else:
    run(c, sql.SQL("CREATE ROLE {} WITH LOGIN CREATEROLE PASSWORD {}").format(role, sql.Literal(E["DM_PW"])))
    print(f"role {user}: created")
if user != admin_user:
    run(c, sql.SQL("GRANT {} TO {}").format(role, sql.Identifier(admin_user)))
for name in (E["DM_DB"], E["LF_DB"]):
    ident = sql.Identifier(name)
    if run(c, "SELECT 1 FROM pg_database WHERE datname = %s", name):
        run(c, sql.SQL("ALTER DATABASE {} OWNER TO {}").format(ident, role))
        print(f"database {name}: exists, owner {user}")
    else:
        run(c, sql.SQL("CREATE DATABASE {} OWNER {}").format(ident, role))
        print(f"database {name}: created, owner {user}")
c.close()

c = connect(admin_user, E["PG_ADMIN_PW"], E["DM_DB"])
available = {r[0] for r in run(c, "SELECT name FROM pg_available_extensions")}
print("TIMESCALEDB_AVAILABLE" if "timescaledb" in available else "TIMESCALEDB_UNAVAILABLE")
wanted = ["vector", "pg_trgm"] + (["timescaledb"] if E.get("WANT_TIMESCALEDB") == "true" else [])
missing = [x for x in wanted if x not in available]
if missing:
    sys.exit(f"extensions not available on this Postgres: {missing}")
for ext in wanted:
    run(c, sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
print("extensions:", sorted(r[0] for r in run(c, "SELECT extname FROM pg_extension")))
c.close()

u = connect(user, E["DM_PW"], E["DM_DB"])
run(u, "CREATE TABLE ibl_infra_probe (id int)")
run(u, "DROP TABLE ibl_infra_probe")
print(f"{user} can log in and create tables in {E['DM_DB']}")
u.close()

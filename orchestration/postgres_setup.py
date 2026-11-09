"""Fixed local PostgreSQL setup actions for an explicitly enabled bridge.

The remote program creates only the four named development databases and adds
one pgAdmin connection through pgAdmin's own importer. It accepts no SQL, shell
commands, passwords, remote database hosts, or replacement configuration.
"""

SETUP_CAPABILITIES = frozenset({"bridge.postgres.prepare", "bridge.pgadmin.register"})


def validate_setup_parameters(capability, parameters):
    """Reject task-supplied options for these fixed local setup operations."""
    if capability not in SETUP_CAPABILITIES:
        raise ValueError("Unsupported local database setup action")
    if not isinstance(parameters, dict) or parameters:
        raise ValueError("Local database setup accepts no task parameters")
    return {}


REMOTE_POSTGRES_SETUP = r'''
from contextlib import closing
import fcntl
import getpass
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
import tempfile
from urllib.parse import quote

LOCAL_DATABASES = ("datalake", "accelerate", "eab_dev", "scoring")
LOCAL_SERVER_NAME = "Polaris Local PostgreSQL 18"
LOCAL_SERVER_GROUP = "Local development"
# The desktop runtime normally sets this before importing config. Running
# setup.py directly changes SERVER_MODE too late to recalculate its directories.
PGADMIN_DESKTOP_ENTRY = (
    "import builtins, runpy, sys; builtins.SERVER_MODE = False; "
    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
)

def postgres_guard(payload):
    """Require explicit operator configuration and the verified bridge identity."""
    if payload.get("postgres_enabled") is not True:
        raise ValueError("Local PostgreSQL setup is not enabled for this bridge")
    if payload.get("parameters", {}) != {}:
        raise ValueError("Local database setup accepts no task parameters")
    if (payload.get("hostname") != socket.gethostname()
            or payload.get("username") != getpass.getuser()):
        raise ValueError("Local database setup requires the configured remote identity")

def postgres_environment():
    """Prevent implicit service, password, startup, or connection overrides."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PG")}
    environment.update(PGPASSFILE="/dev/null", PGCONNECT_TIMEOUT="5")
    return environment

def postgres_query(sql):
    """Run application-owned SQL only against the local PostgreSQL 18 service."""
    psql = Path("/opt/homebrew/opt/postgresql@18/bin/psql")
    if not psql.is_file() or not os.access(psql, os.X_OK):
        raise ValueError("The local PostgreSQL 18 client is unavailable")
    completed = subprocess.run(
        [str(psql), "--no-password", "-X", "-h", "127.0.0.1", "-p", "5432",
         "-U", getpass.getuser(), "-d", "postgres", "-A", "-t",
         "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, timeout=20, env=postgres_environment(),
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode:
        raise ValueError("The local PostgreSQL action failed; existing databases were preserved")
    if len(completed.stdout) > 65536:
        raise ValueError("The local PostgreSQL response exceeded its limit")
    return completed.stdout.strip()

def postgres_inventory():
    """Read server version and database names without reading table contents."""
    value = json.loads(postgres_query(
        "SELECT json_build_object('version', current_setting('server_version'), "
        "'version_num', current_setting('server_version_num')::integer, "
        "'databases', (SELECT json_agg(datname) FROM pg_database "
        "WHERE NOT datistemplate))::text"
    ))
    if (not isinstance(value, dict)
            or not isinstance(value.get("version_num"), int)
            or not 180000 <= value["version_num"] < 190000
            or not isinstance(value.get("databases"), list)
            or not all(isinstance(name, str) for name in value["databases"])):
        raise ValueError("Local setup requires a PostgreSQL 18 server on 127.0.0.1:5432")
    return value

def postgres_prepare(payload):
    """Create only missing named databases and preserve every existing database."""
    postgres_guard(payload)
    inventory = postgres_inventory()
    present = set(inventory["databases"])
    created, existing = [], []
    for name in LOCAL_DATABASES:
        if name in present:
            existing.append(name)
            continue
        # Names are fixed program constants, never request-supplied SQL.
        try:
            postgres_query('CREATE DATABASE "' + name + '"')
        except ValueError:
            # A concurrent setup may have created this database in the meantime.
            if name not in postgres_inventory()["databases"]:
                raise
            existing.append(name)
        else:
            created.append(name)
    verified = postgres_inventory()
    if not set(LOCAL_DATABASES).issubset(verified["databases"]):
        raise ValueError("Local database setup could not verify all four databases")
    return {"host": "127.0.0.1", "port": 5432, "version": verified["version"],
            "created": created, "existing": existing,
            "databases": list(LOCAL_DATABASES)}

def pgadmin_registry():
    """Use only the desktop registry created by the user's first pgAdmin launch."""
    registry = Path.home() / ".pgadmin" / "pgadmin4.db"
    if not registry.is_file():
        raise ValueError("Open pgAdmin once to initialize its desktop settings, then retry registration")
    if registry.resolve() != registry or not stat.S_ISREG(registry.stat().st_mode):
        raise ValueError("The pgAdmin desktop registry must be a regular canonical file")
    return registry

def pgadmin_metadata(registry):
    """Read only the desktop username and non-secret saved connection metadata."""
    uri = "file:" + quote(str(registry), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as database:
        rows = database.execute(
            "SELECT email FROM user WHERE email = ?", ("pgadmin4@pgadmin.org",)
        ).fetchall()
        if rows != [("pgadmin4@pgadmin.org",)]:
            raise ValueError("The expected pgAdmin desktop user is not initialized")
        servers = database.execute(
            "SELECT name, host, port, username, maintenance_db FROM server "
            "WHERE user_id = (SELECT id FROM user WHERE email = ?) LIMIT 1000",
            (rows[0][0],),
        ).fetchall()
    return rows[0][0], servers

def pgadmin_matching(servers):
    """Find the local connection without overwriting any saved server entry."""
    for name, host, port, username, maintenance_db in servers:
        if (host in {"127.0.0.1", "localhost"} and port == 5432
                and username == getpass.getuser() and maintenance_db == "postgres"):
            return name
    if any(server[0] == LOCAL_SERVER_NAME for server in servers):
        raise ValueError("A different pgAdmin connection already uses the requested local server name")
    return None

def pgadmin_register(payload):
    """Add password-free localhost metadata using the official pgAdmin importer."""
    postgres_guard(payload)
    inventory = postgres_inventory()
    registry = pgadmin_registry()
    web = Path("/Applications/pgAdmin 4.app/Contents/Resources/web")
    python = Path("/Applications/pgAdmin 4.app/Contents/Frameworks/Python.framework/Versions/3.13/bin/python3")
    setup = web / "setup.py"
    if not setup.is_file() or not python.is_file():
        raise ValueError("The installed pgAdmin setup tool is unavailable")
    lock_path = registry.parent / ".agentic-local-setup.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        user, servers = pgadmin_metadata(registry)
        found = pgadmin_matching(servers)
        if found:
            return {"registered": True, "existing": True, "server_name": found,
                    "host": "127.0.0.1", "port": 5432, "version": inventory["version"]}
        document = {"Servers": {"1": {
            "Name": LOCAL_SERVER_NAME, "Group": LOCAL_SERVER_GROUP,
            "Host": "127.0.0.1", "Port": 5432,
            "Username": getpass.getuser(), "MaintenanceDB": "postgres",
            "ConnectionParameters": {"sslmode": "prefer"},
        }}}
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=".local-server-", suffix=".json", dir=registry.parent)
        try:
            with os.fdopen(temporary_fd, "w") as stream:
                json.dump(document, stream)
            completed = subprocess.run(
                [str(python), "-c", PGADMIN_DESKTOP_ENTRY, str(setup),
                 "load-servers", temporary_name,
                 "--sqlite-path", str(registry), "--user", user, "--no-replace"],
                cwd=web, capture_output=True, text=True, timeout=45,
                stdin=subprocess.DEVNULL, env=postgres_environment(),
            )
            if completed.returncode:
                raise ValueError("pgAdmin could not import the local connection; existing settings were preserved")
            _, servers = pgadmin_metadata(registry)
            if not pgadmin_matching(servers):
                raise ValueError("pgAdmin did not confirm the imported local connection")
        finally:
            os.unlink(temporary_name)
    return {"registered": True, "existing": False, "server_name": LOCAL_SERVER_NAME,
            "host": "127.0.0.1", "port": 5432, "version": inventory["version"]}
'''

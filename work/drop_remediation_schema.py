"""Drop one explicitly named isolated remediation schema without logging credentials."""
import os
import re
import sys

from sqlalchemy import create_engine

schema = sys.argv[1] if len(sys.argv) == 2 else ""
if not re.fullmatch(r"cauldra_f(?:02|03|04|05|08|09|10|11|12|13)_[a-f0-9]{12}", schema):
    raise SystemExit("Refusing unsafe remediation schema name")

engine = create_engine(os.environ["TEST_POSTGRES_ADMIN_URL"], pool_pre_ping=True)
with engine.begin() as connection:
    connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
engine.dispose()
print(f"DROPPED_REMEDIATION_SCHEMA={schema}")

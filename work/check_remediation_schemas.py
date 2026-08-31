"""List isolated remediation schemas without printing connection details."""
import os
from sqlalchemy import create_engine, text

url = os.environ["TEST_POSTGRES_ADMIN_URL"]
engine = create_engine(url, pool_pre_ping=True)
with engine.connect() as connection:
    names = [row[0] for row in connection.execute(text(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'cauldra_f03_%' OR schema_name LIKE 'cauldra_f04_%' "
        "OR schema_name LIKE 'cauldra_f05_%' OR schema_name LIKE 'cauldra_f02_%' "
        "OR schema_name LIKE 'cauldra_f09_%' OR schema_name LIKE 'cauldra_f08_%' "
        "OR schema_name LIKE 'cauldra_f10_%' OR schema_name LIKE 'cauldra_f11_%' "
        "OR schema_name LIKE 'cauldra_f12_%' OR schema_name LIKE 'cauldra_f13_%' "
        "ORDER BY schema_name"
    ))]
print(f"REMAINING_REMEDIATION_SCHEMAS={len(names)}")
for name in names:
    print(f"SCHEMA={name}")
engine.dispose()

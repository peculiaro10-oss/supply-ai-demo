from pathlib import Path
from datetime import datetime
import shutil

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "supply_ai.db"
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

if not DB_PATH.exists():
    raise SystemExit(f"Database not found: {DB_PATH}")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out = BACKUP_DIR / f"supply_ai_{stamp}.db"
shutil.copy2(DB_PATH, out)
print(f"Backup created: {out}")

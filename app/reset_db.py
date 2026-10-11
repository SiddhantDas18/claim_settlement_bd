"""Drop and recreate every table (there are no migrations yet). Deletes ALL data in the configured database.

    python -m app.reset_db --yes
"""

import sys

from app import models  # noqa: F401  (registers all tables)
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.services.seed import seed_defaults


def main() -> None:
    if "--yes" not in sys.argv:
        sys.exit(f"This deletes all data in {engine.url.render_as_string(hide_password=True)}. Run again with --yes.")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_defaults(db)
    print(f"Recreated tables in {engine.url.render_as_string(hide_password=True)} (admin {settings.admin_email} and default plan seeded).")


if __name__ == "__main__":
    main()

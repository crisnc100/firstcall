from alembic import context
from sqlalchemy import create_engine

from firstcall.db import Base
from firstcall.settings import Settings


def _run(connection: object) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


given = context.config.attributes.get("connection")
if given is not None:
    _run(given)
else:
    url = context.config.attributes.get("url") or Settings().database_url
    with create_engine(url).connect() as connection:
        _run(connection)

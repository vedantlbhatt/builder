"""Alembic environment.

Migrations run as the OWNER (DATABASE_URL), not as the API's role. The API connects as
builder_app, which is NOBYPASSRLS and deliberately cannot create roles or alter tables —
that separation is the point, and it is why boot.py can assert the API is not privileged.
"""

from alembic import context
from sqlalchemy import create_engine

from builder.settings import settings

config = context.config


def run_migrations_online() -> None:
    engine = create_engine(settings().database_url, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()

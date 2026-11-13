from alembic import context

from marketplace.infrastructure import operation_models  # noqa: F401
from marketplace.infrastructure.database import Base, Database
from marketplace.listings.infrastructure import models  # noqa: F401
from marketplace.settings import load_settings

target_metadata = Base.metadata
settings = load_settings("api")

if context.is_offline_mode():
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    database = Database(settings.database_url.get_secret_value())
    try:
        with database.engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        database.close()

from alembic import context
from sqlalchemy import engine_from_config, pool
from cronsentinel.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()

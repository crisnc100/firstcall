"""The schema: org on every domain table, and migrations that match the models."""

from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config

from firstcall.db import NOT_DOMAIN_TABLES, Base
from tests.conftest import create_database, drop_database, upgrade


def test_every_domain_table_has_a_non_null_org_id() -> None:
    tables = Base.metadata.tables
    domain = [name for name in tables if name not in NOT_DOMAIN_TABLES]
    assert len(domain) >= 10
    for name in domain:
        column = tables[name].columns.get("org_id")
        assert column is not None, f"{name} has no org_id"
        assert column.nullable is False, f"{name}.org_id is nullable"
        assert {fk.column.table.name for fk in column.foreign_keys} == {"orgs"}, name


def test_the_exempt_tables_are_exactly_the_root_and_process_heartbeats() -> None:
    assert {"orgs", "heartbeats"} == NOT_DOMAIN_TABLES
    assert "org_id" not in Base.metadata.tables["orgs"].columns
    assert "org_id" not in Base.metadata.tables["heartbeats"].columns


def test_upgrade_on_an_empty_database_then_no_drift() -> None:
    name = f"firstcall_migrate_{uuid.uuid4().hex[:12]}"
    url = create_database(name)
    try:
        upgrade(url)
        config = Config("alembic.ini")
        config.attributes["url"] = url
        # Raises if the models and the migrated schema differ.
        command.check(config)
    finally:
        drop_database(name)

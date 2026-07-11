from typing import Any

from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.migrations.schema_editor import BaseSchemaEditor
from tortoise.migrations.schema_generator.state_apps import StateApps


def _row_value(row: Any, *keys: str) -> Any:
    if isinstance(row, dict):
        for key in keys:
            if key in row:
                return row[key]
        return next(iter(row.values()))
    return row[0]


async def remove_readhistory_unique_constraint(
        apps: StateApps,
        schema_editor: BaseSchemaEditor,
) -> None:
    client = schema_editor.client
    dialect = client.capabilities.dialect

    if dialect == "mysql":
        _ignored, rows = await client.execute_query(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'readhistorychunk'
              AND NON_UNIQUE = 0
              AND INDEX_NAME != 'PRIMARY'
            GROUP BY INDEX_NAME
            HAVING SUM(COLUMN_NAME = 'user_id') > 0
               AND SUM(COLUMN_NAME = 'peer_id') > 0
            """,
        )
        index_names = {_row_value(row, "INDEX_NAME") for row in rows}
        index_names.discard(None)
        index_names.add("uid_readhistory_user_id_044953")
        for index_name in index_names:
            try:
                await client.execute_script(
                    f"ALTER TABLE `readhistorychunk` DROP INDEX `{index_name}`",
                )
            except Exception:
                pass
        return

    if dialect == "sqlite":
        _ignored, rows = await client.execute_query(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'readhistorychunk'",
        )
        for row in rows:
            index_name = _row_value(row, "name")
            if not index_name or index_name.startswith("sqlite_"):
                continue
            try:
                await client.execute_script(f'DROP INDEX "{index_name}"')
            except Exception:
                pass
        return

    from tortoise.migrations.schema_generator.state_editor import StateSchemaEditor

    state_editor = StateSchemaEditor(client, apps)
    model = apps.get_model("models", "ReadHistoryChunk")
    try:
        await state_editor.remove_constraint(model, fields=["user_id", "peer_id"])
    except Exception:
        pass


class Migration(migrations.Migration):
    dependencies = [('models', '0048_auto_20260524_1940')]

    initial = False

    operations = [
        ops.RunPython(remove_readhistory_unique_constraint),
    ]
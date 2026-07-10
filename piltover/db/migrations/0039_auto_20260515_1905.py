from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields


class AlterFieldStateOnly(ops.AlterField):
    async def database_forward(self, app_label, old_state, new_state, state_editor=None):
        return None

    async def database_backward(self, app_label, old_state, new_state, state_editor=None):
        return None


class Migration(migrations.Migration):
    dependencies = [('models', '0038_auto_20260515_1625')]

    initial = False

    operations = [
        ops.AddField(
            model_name='Channel',
            name='admins_count',
            field=fields.SmallIntField(default=0),
        ),
        ops.AddField(
            model_name='Channel',
            name='participants_count',
            field=fields.IntField(default=0),
        ),
        ops.RunSQL(
            "ALTER TABLE `chat` MODIFY COLUMN `participants_count` INT NOT NULL DEFAULT 0",
            reverse_sql="ALTER TABLE `chat` MODIFY COLUMN `participants_count` SMALLINT NOT NULL",
        ),
        AlterFieldStateOnly(
            model_name='Chat',
            name='participants_count',
            field=fields.IntField(default=0),
        ),
    ]

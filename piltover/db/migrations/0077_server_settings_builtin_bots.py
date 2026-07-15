from tortoise import fields
from tortoise import migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [('models', '0076_username_max_length')]

    initial = False

    operations = [
        ops.AddField(
            model_name='ServerSettings',
            name='verifybot_enabled',
            field=fields.BooleanField(default=True),
        ),
        ops.AddField(
            model_name='ServerSettings',
            name='stars_bot_enabled',
            field=fields.BooleanField(default=True),
        ),
    ]
from tortoise import fields
from tortoise import migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [('models', '0077_server_settings_builtin_bots')]

    initial = False

    operations = [
        ops.AddField(
            model_name='User',
            name='support',
            field=fields.BooleanField(default=False),
        ),
    ]
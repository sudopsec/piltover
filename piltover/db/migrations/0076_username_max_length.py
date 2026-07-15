from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [('models', '0075_server_settings')]

    initial = False

    operations = [
        ops.AlterField(
            model_name='Username',
            name='username',
            field=fields.CharField(max_length=32, unique=True),
        ),
    ]
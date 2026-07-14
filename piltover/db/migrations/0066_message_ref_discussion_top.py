from tortoise import fields
from tortoise import migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [('models', '0065_stars_bot_payments')]

    initial = False

    operations = [
        ops.AddField(
            model_name='MessageRef',
            name='discussion_top_message_id',
            field=fields.BigIntField(null=True, default=None),
        ),
    ]
from tortoise import fields
from tortoise import migrations
from tortoise.backends.base.schema_generator import BaseSchemaGenerator
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops

from piltover.db.enums import AdminBotState

_schema_generator = BaseSchemaGenerator(None)


class Migration(migrations.Migration):
    dependencies = [('models', '0068_user_admin')]

    initial = False

    operations = [
        ops.AddField(
            model_name='User',
            name='spam_blocked',
            field=fields.BooleanField(default=False),
        ),
        ops.CreateModel(
            name='AdminBotUserState',
            fields=[
                ('id', fields.BigIntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('user', fields.OneToOneField(
                    'models.User', source_field='user_id', db_constraint=True, to_field='id',
                    on_delete=OnDelete.CASCADE,
                )),
                ('state', fields.IntEnumField(description='', enum_type=AdminBotState, generated=False)),
                ('data', fields.BinaryField(null=True)),
                ('last_access', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'adminbotuserstate', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
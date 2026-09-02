# Generated manually — remove unused policy_type=component

from django.db import migrations, models


def delete_component_policies(apps, schema_editor):
    Policy = apps.get_model('cms_adp', 'Policy')
    Policy.objects.filter(policy_type='component').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cms_adp', '0061_alter_policy_policy_type'),
    ]

    operations = [
        migrations.RunPython(delete_component_policies, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='policy',
            name='policy_type',
            field=models.CharField(
                choices=[('url', 'Доступ к URL'), ('api', 'Доступ к API')],
                default='url',
                max_length=20,
                verbose_name='Тип политики',
            ),
        ),
    ]

from django.db import migrations, models


def rebuild_search_titles(apps, schema_editor):
    Photo = apps.get_model('photos', 'Photo')
    database = schema_editor.connection.alias
    for photo in Photo.objects.using(database).all().iterator():
        Photo.objects.using(database).filter(pk=photo.pk).update(search_title=photo.title.casefold())


class Migration(migrations.Migration):
    dependencies = [('photos', '0003_backfill_search_title')]
    operations = [migrations.AlterField(
        model_name='photo', name='search_title',
        field=models.CharField(max_length=360, db_index=True, editable=False, default=''),
    ), migrations.RunPython(rebuild_search_titles, migrations.RunPython.noop)]

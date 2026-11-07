from django.db import migrations


def backfill(apps, schema_editor):
    Photo = apps.get_model('photos', 'Photo')
    database = schema_editor.connection.alias
    for photo in Photo.objects.using(database).all().iterator():
        # This historical step still has varchar(120); migration 0004 widens
        # the column and rebuilds the complete value without truncation.
        Photo.objects.using(database).filter(pk=photo.pk).update(search_title=photo.title.casefold()[:120])


class Migration(migrations.Migration):
    dependencies = [('photos', '0002_photo_search_title')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]

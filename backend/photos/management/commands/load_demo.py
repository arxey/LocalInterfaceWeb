from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand

from photos.models import Photo
from photos.serializers import PhotoSerializer


class Command(BaseCommand):
    help = 'Добавляет учебную фотографию для демонстрации каталога.'

    def handle(self, *args, **options):
        title = 'Осеннее озеро в горах'
        if Photo.objects.filter(title=title).exists():
            self.stdout.write('Учебная фотография уже существует.')
            return
        path = Path(__file__).resolve().parents[4] / 'demo' / 'ural-lake.png'
        upload = SimpleUploadedFile(path.name, path.read_bytes(), content_type='image/png')
        serializer = PhotoSerializer(data={'title': title, 'image': upload})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.stdout.write(self.style.SUCCESS('Учебная фотография добавлена.'))

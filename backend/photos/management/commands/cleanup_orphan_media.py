import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from photos.models import Photo


class Command(BaseCommand):
    help = 'List unreferenced local photo files older than 24 hours; --delete removes the listed files.'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true')
        parser.add_argument('--min-age-hours', type=float, default=24)

    def handle(self, *args, **options):
        if options['min_age_hours'] < 0:
            raise CommandError('--min-age-hours must be nonnegative.')
        referenced = set()
        for image, thumbnail in Photo.objects.values_list('image', 'thumbnail').iterator():
            referenced.update((image, thumbnail))
        root = Path(settings.MEDIA_ROOT).resolve()
        cutoff = time.time() - options['min_age_hours'] * 3600
        count = 0
        for directory in ('photos', 'thumbnails'):
            folder = root / directory
            if not folder.is_dir() or folder.is_symlink():
                continue
            for path in folder.rglob('*'):
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
                    continue
                name = path.relative_to(root).as_posix()
                if name in referenced or path.stat().st_mtime > cutoff:
                    continue
                self.stdout.write(name)
                count += 1
                if options['delete']:
                    path.unlink()
        self.stdout.write(f'{count} orphan file(s) ' + ('deleted.' if options['delete'] else 'listed; no files deleted.'))

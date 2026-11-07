import logging

from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .images import prepare_upload


logger = logging.getLogger(__name__)


def delete_files(files, using=None, only_unreferenced=True):
    for storage, name in files:
        if name:
            if only_unreferenced and Photo.objects.using(using).filter(models.Q(image=name) | models.Q(thumbnail=name)).exists():
                continue
            try:
                storage.delete(name)
            except OSError:
                logger.exception('Could not remove obsolete photo file %s', name)


class Photo(models.Model):
    title = models.CharField('Название', max_length=120)
    search_title = models.CharField(max_length=360, db_index=True, editable=False, default='')
    image = models.ImageField('Изображение', upload_to='photos/')
    thumbnail = models.ImageField('Миниатюра', upload_to='thumbnails/', blank=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'фотография'
        verbose_name_plural = 'фотографии'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and not update_fields:
            return
        self.title = self.title.strip()
        if not self.title or len(self.title) > 120:
            raise ValidationError({'title': 'Введите название длиной от 1 до 120 символов.'})
        self.search_title = self.title.casefold()
        if update_fields is not None and 'title' in update_fields:
            kwargs['update_fields'] = set(update_fields) | {'search_title'}
        using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        replacing_image = (update_fields is None or 'image' in update_fields) and self.image and not self.image._committed
        if replacing_image:
            upload = self.image.file
            prepared = upload if hasattr(upload, '_photo_thumbnail') else prepare_upload(upload)
            self.image = prepared
            self.thumbnail = prepared._photo_thumbnail
            if update_fields is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'thumbnail'}
        if self._state.adding and not self.image:
            raise ValidationError({'image': 'Добавьте изображение.'})
        previous = None
        try:
            with transaction.atomic(using=using):
                if self.pk:
                    previous = type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
                if previous is not None and not replacing_image:
                    # A stale admin form or model instance may still hold paths
                    # removed by a concurrent replacement. Only fresh uploads
                    # replace files; preserve the current locked row otherwise.
                    self.image = previous.image
                    self.thumbnail = previous.thumbnail
                super().save(*args, **kwargs)
                if replacing_image and previous:
                    old_files = [(previous.image.storage, previous.image.name), (previous.thumbnail.storage, previous.thumbnail.name)]
                    transaction.on_commit(lambda: delete_files(old_files, using=using), using=using, robust=True)
        except Exception:
            # Django rolls back database rows, but storage writes need compensation.
            if replacing_image:
                files = [(field.storage, field.name) for field in (self.image, self.thumbnail) if field._committed]
                delete_files(files, using=using, only_unreferenced=False)
            raise


@receiver(post_delete, sender=Photo)
def remove_photo_files(sender, instance, using, **kwargs):
    files = [(instance.image.storage, instance.image.name), (instance.thumbnail.storage, instance.thumbnail.name)]
    transaction.on_commit(lambda: delete_files(files, using=using), using=using, robust=True)

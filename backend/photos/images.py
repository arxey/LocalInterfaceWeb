"""Decode bounded uploads before any files or database rows are written."""
import io
import uuid
import warnings

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


def prepare_upload(upload):
    if upload.size > settings.PHOTO_MAX_UPLOAD_BYTES:
        raise ValidationError('Файл не должен превышать 10 МБ.')
    try:
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload) as source:
                image_format = source.format
                if image_format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValidationError('Допустимы JPEG, PNG и WebP.')
                if source.width * source.height > settings.PHOTO_MAX_PIXELS:
                    raise ValidationError('Изображение не должно превышать 25 мегапикселей.')
                if getattr(source, 'n_frames', 1) != 1:
                    raise ValidationError('Анимированные изображения не поддерживаются.')
                source.load()  # verify() alone does not decode truncated pixel data.
                image = ImageOps.exif_transpose(source)
                mode = 'RGBA' if image_format != 'JPEG' and ('A' in image.mode or 'transparency' in image.info) else 'RGB'
                image = image.convert(mode)
                image.info.clear()  # Drop metadata and trailing/polyglot payloads.
                original = io.BytesIO()
                image.save(original, format=image_format)
                image.thumbnail((240, 160), Image.Resampling.LANCZOS)
                if image.mode == 'RGBA':
                    background = Image.new('RGB', image.size, 'white')
                    background.paste(image, mask=image.getchannel('A'))
                    image = background
                thumbnail = io.BytesIO()
                image.save(thumbnail, format='WEBP', quality=82)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError,
            OverflowError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Файл не является корректным изображением.') from exc
    finally:
        upload.seek(0)
    stem = uuid.uuid4().hex
    extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[image_format]
    result = ContentFile(original.getvalue(), name=f'{stem}.{extension}')
    result._photo_thumbnail = ContentFile(thumbnail.getvalue(), name=f'{stem}.webp')
    return result

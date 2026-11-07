from collections.abc import Mapping

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from .images import prepare_upload
from .models import Photo


class PhotoSerializer(serializers.ModelSerializer):
    # Bound the decoded image dimensions before Pillow does any pixel work.
    image = serializers.FileField(write_only=True)
    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Photo
        fields = ['id', 'title', 'image', 'image_url', 'thumbnail_url', 'created_at', 'updated_at']
        read_only_fields = ['id', 'image_url', 'thumbnail_url', 'created_at', 'updated_at']

    def get_image_url(self, obj):
        return obj.image.url if obj.image else None

    def get_thumbnail_url(self, obj):
        return obj.thumbnail.url if obj.thumbnail else None

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({'non_field_errors': ['Ожидается объект JSON.']})
        unknown = set(data) - {'title', 'image'}
        if unknown:
            raise serializers.ValidationError({field: 'Неизвестное поле.' for field in unknown})
        return super().to_internal_value(data)

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Введите название фотографии.')
        return value

    def validate_image(self, value):
        try:
            return prepare_upload(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def update(self, instance, validated_data):
        # Lock before applying partial updates, including concurrent image edits.
        with transaction.atomic():
            current = get_object_or_404(Photo.objects.select_for_update(), pk=instance.pk)
            return super().update(current, validated_data)

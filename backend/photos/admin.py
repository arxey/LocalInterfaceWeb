from django.contrib import admin

from .models import Photo
from .forms import PhotoAdminForm


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    form = PhotoAdminForm
    list_display = ('title', 'created_at')
    search_fields = ('title',)
    readonly_fields = ('thumbnail', 'created_at', 'updated_at')

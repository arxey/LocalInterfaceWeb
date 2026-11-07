from django import forms

from .images import prepare_upload
from .models import Photo


class PhotoAdminForm(forms.ModelForm):
    image = forms.FileField(label='Изображение')

    class Meta:
        model = Photo
        fields = '__all__'

    def clean_image(self):
        upload = self.cleaned_data['image']
        # An unchanged existing FieldFile must retain its original contents.
        if getattr(upload, '_committed', False):
            return upload
        return prepare_upload(upload)

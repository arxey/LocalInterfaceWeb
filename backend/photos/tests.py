import io
import tempfile
from pathlib import Path

from django.test import override_settings
from django.contrib.auth import get_user_model
from PIL import Image
from rest_framework.test import APITestCase
from django.core.files.uploadedfile import SimpleUploadedFile


def photo_file(name="sample.png", color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 800), color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@override_settings(ALLOWED_HOSTS=['testserver'])
class PhotoApiTests(APITestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings = override_settings(MEDIA_ROOT=self.media.name)
        self.settings.enable()
        self.user = get_user_model().objects.create_user('photographer', password='test-strong-password')
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.settings.disable()
        self.media.cleanup()

    def create_photo(self, title="Закат"):
        return self.client.post("/api/photos/", {"title": title, "image": photo_file()}, format="multipart")

    def test_upload_creates_record_and_thumbnail(self):
        response = self.create_photo()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["title"], "Закат")
        self.assertIn("/media/photos/", response.data["image_url"])
        self.assertIn("/media/thumbnails/", response.data["thumbnail_url"])
        from photos.models import Photo
        photo = Photo.objects.get(pk=response.data["id"])
        self.assertTrue(Path(photo.image.path).exists())
        self.assertTrue(Path(photo.thumbnail.path).exists())
        with Image.open(photo.thumbnail.path) as thumb:
            self.assertLessEqual(thumb.width, 240)
            self.assertLessEqual(thumb.height, 160)

    def test_search_matches_title_and_excludes_other_records(self):
        self.create_photo("Закат на реке")
        self.create_photo("Город")
        response = self.client.get("/api/photos/?q=Закат")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Закат на реке")

    def test_search_is_case_insensitive_for_cyrillic_on_any_database_locale(self):
        self.create_photo("Осеннее озеро")
        response = self.client.get("/api/photos/?q=осеннее")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_edit_title_keeps_image(self):
        created = self.create_photo().data
        response = self.client.patch(f"/api/photos/{created['id']}/", {"title": "Рассвет"}, format="multipart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Рассвет")
        self.assertEqual(response.data["image_url"], created["image_url"])

    def test_delete_removes_database_record_and_files(self):
        created = self.create_photo().data
        from photos.models import Photo
        photo = Photo.objects.get(pk=created["id"])
        image_path, thumb_path = Path(photo.image.path), Path(photo.thumbnail.path)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(f"/api/photos/{created['id']}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Photo.objects.filter(pk=created["id"]).exists())
        self.assertFalse(image_path.exists())
        self.assertFalse(thumb_path.exists())

    def test_rejects_non_image_upload(self):
        bad = SimpleUploadedFile("bad.txt", b"not an image", content_type="text/plain")
        response = self.client.post("/api/photos/", {"title": "Ошибка", "image": bad}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_requires_nonblank_title(self):
        response = self.client.post("/api/photos/", {"title": "   ", "image": photo_file()}, format="multipart")
        self.assertEqual(response.status_code, 400)

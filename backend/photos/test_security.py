import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from .models import Photo
from .serializers import PhotoSerializer
from .tests import photo_file


@override_settings(ALLOWED_HOSTS=['testserver'])
class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = get_user_model().objects.create_user('photographer', password='test-strong-password')

    def login(self):
        response = self.client.get('/api/auth/')
        self.assertEqual(response.status_code, 200)
        token = response.json()['csrfToken']
        return self.client.post('/api/auth/', {'username': 'photographer', 'password': 'test-strong-password'}, format='json', HTTP_X_CSRFTOKEN=token)

    def test_auth_status_is_public_and_issues_csrf_token(self):
        response = self.client.get('/api/auth/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['authenticated'], False)
        self.assertEqual(response.json()['username'], '')
        self.assertTrue(response.json()['csrfToken'])

    def test_anonymous_login_requires_csrf(self):
        response = self.client.post('/api/auth/', {'username': 'photographer', 'password': 'test-strong-password'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_login_and_logout_rotate_csrf_and_require_it_for_writes(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['authenticated'])
        self.assertEqual(data['username'], 'photographer')
        self.assertEqual(self.client.post('/api/photos/', {'title': 'Blocked'}).status_code, 403)
        self.assertEqual(self.client.delete('/api/auth/').status_code, 403)
        response = self.client.delete('/api/auth/', HTTP_X_CSRFTOKEN=data['csrfToken'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['authenticated'])
        self.assertNotEqual(response.json()['csrfToken'], data['csrfToken'])

    def test_bad_credentials_and_malformed_payloads_are_client_errors(self):
        token = self.client.get('/api/auth/').json()['csrfToken']
        for payload, expected in [({'username': 'photographer', 'password': 'wrong'}, 401), ([], 400), ({'username': [], 'password': 1}, 400), ({'username': 'invalid\u0000name', 'password': 'secret'}, 400)]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/auth/', payload, format='json', HTTP_X_CSRFTOKEN=token).status_code, expected)

    def test_public_reads_but_anonymous_mutations_are_forbidden(self):
        self.assertEqual(self.client.get('/api/photos/').status_code, 200)
        self.assertEqual(self.client.post('/api/photos/', {'title': 'Forbidden'}).status_code, 403)
        self.assertEqual(self.client.patch('/api/photos/123/', {'title': 'Forbidden'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete('/api/photos/123/').status_code, 403)

    def test_health_checks_database_without_exposing_error_details(self):
        response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'database': 'ok'})
        with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=DatabaseError('private database password')):
            response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('password', response.content.decode())


@override_settings(ALLOWED_HOSTS=['testserver'])
class UploadSecurityTests(APITestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings = override_settings(MEDIA_ROOT=self.media.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.user = get_user_model().objects.create_user('photographer')
        self.client.force_authenticate(self.user)

    def upload(self, image=None, title='Photo'):
        return self.client.post('/api/photos/', {'title': title, 'image': image or photo_file()}, format='multipart')

    def files(self):
        return [p for p in Path(self.media.name).rglob('*') if p.is_file()]

    def test_rejects_animated_png(self):
        buffer = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(buffer, 'PNG', save_all=True, append_images=[Image.new('RGB', (10, 10), 'blue')], duration=10)
        response = self.upload(SimpleUploadedFile('animated.png', buffer.getvalue(), 'image/png'))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Photo.objects.exists())
        self.assertEqual(self.files(), [])

    @override_settings(PHOTO_MAX_PIXELS=100)
    def test_rejects_excessive_decoded_dimensions(self):
        response = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.files(), [])

    def test_stores_canonical_image_with_safe_extension_and_no_trailing_payload(self):
        source = photo_file('injected.png')
        payload = source.read() + b'<script>secret-payload</script>'
        response = self.upload(SimpleUploadedFile('injected.png', payload, 'image/png'))
        self.assertEqual(response.status_code, 201)
        photo = Photo.objects.get(pk=response.data['id'])
        self.assertEqual(Path(photo.image.name).suffix, '.png')
        self.assertNotIn(b'secret-payload', Path(photo.image.path).read_bytes())

    def test_failed_thumbnail_write_rolls_back_row_and_removes_new_files(self):
        real_save = FileSystemStorage._save

        def failing_save(storage, name, content):
            if name.startswith('thumbnails/'):
                raise OSError('storage unavailable')
            return real_save(storage, name, content)

        serializer = PhotoSerializer(data={'title': 'Photo', 'image': photo_file()})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with patch.object(FileSystemStorage, '_save', new=failing_save):
            with self.assertRaises(OSError):
                serializer.save()
        self.assertFalse(Photo.objects.exists())
        self.assertEqual(self.files(), [])

    def test_failed_replacement_keeps_previous_record_and_files(self):
        self.upload()
        photo = Photo.objects.get()
        old_image, old_thumb = photo.image.name, photo.thumbnail.name
        real_save = FileSystemStorage._save

        def failing_save(storage, name, content):
            if name.startswith('thumbnails/'):
                raise OSError('storage unavailable')
            return real_save(storage, name, content)

        serializer = PhotoSerializer(photo, data={'image': photo_file(color='blue')}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with patch.object(FileSystemStorage, '_save', new=failing_save):
            with self.assertRaises(OSError):
                serializer.save()
        photo.refresh_from_db()
        self.assertEqual(photo.image.name, old_image)
        self.assertEqual(photo.thumbnail.name, old_thumb)
        self.assertEqual(len(self.files()), 2)

    def test_replacement_deletes_previous_files_only_after_commit(self):
        self.upload()
        photo = Photo.objects.get()
        old_paths = [Path(photo.image.path), Path(photo.thumbnail.path)]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(f'/api/photos/{photo.pk}/', {'image': photo_file(color='blue')}, format='multipart')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(p.exists() for p in old_paths))
        self.assertTrue(all(not p.exists() for p in old_paths))
        self.assertEqual(len(self.files()), 2)

    def test_stale_full_title_save_preserves_concurrently_replaced_files(self):
        self.upload()
        stale = Photo.objects.get()
        original_paths = [Path(stale.image.path), Path(stale.thumbnail.path)]
        serializer = PhotoSerializer(Photo.objects.get(pk=stale.pk), data={'image': photo_file(color='blue')}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with self.captureOnCommitCallbacks(execute=True):
            replacement = serializer.save()
        self.assertTrue(all(not path.exists() for path in original_paths))
        replacement_paths = [Path(replacement.image.path), Path(replacement.thumbnail.path)]
        stale.title = 'Title from stale administrator form'
        stale.save()
        stale.refresh_from_db()
        self.assertEqual(stale.title, 'Title from stale administrator form')
        self.assertEqual(stale.image.name, replacement.image.name)
        self.assertEqual(stale.thumbnail.name, replacement.thumbnail.name)
        self.assertTrue(all(path.exists() for path in replacement_paths))
        self.assertEqual(len(self.files()), 2)

    def test_queryset_delete_and_rolled_back_delete_preserve_lifecycle(self):
        self.upload()
        photo = Photo.objects.get()
        paths = [Path(photo.image.path), Path(photo.thumbnail.path)]
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Photo.objects.filter(pk=photo.pk).delete()
                raise RuntimeError('rollback')
        self.assertTrue(Photo.objects.filter(pk=photo.pk).exists())
        self.assertTrue(all(p.exists() for p in paths))
        with self.captureOnCommitCallbacks(execute=True):
            Photo.objects.filter(pk=photo.pk).delete()
        self.assertTrue(all(not p.exists() for p in paths))

    def test_deleting_a_record_keeps_files_referenced_by_another_record(self):
        self.upload()
        photo = Photo.objects.get()
        shared = Photo.objects.create(title='Shared legacy reference', image=photo.image.name, thumbnail=photo.thumbnail.name)
        paths = [Path(photo.image.path), Path(photo.thumbnail.path)]
        with self.captureOnCommitCallbacks(execute=True):
            photo.delete()
        self.assertTrue(all(p.exists() for p in paths))
        with self.captureOnCommitCallbacks(execute=True):
            shared.delete()
        self.assertTrue(all(not p.exists() for p in paths))

    def test_rejects_truncated_pixels_and_oversized_uploads(self):
        original = photo_file().read()
        for contents in (original[:len(original) // 2], b'x' * (10 * 1024 * 1024 + 1)):
            with self.subTest(size=len(contents)):
                response = self.upload(SimpleUploadedFile('invalid.png', contents, 'image/png'))
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Photo.objects.exists())
        self.assertEqual(self.files(), [])

    def test_upload_applies_exif_orientation_and_strips_metadata(self):
        buffer = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = 'private-location-marker'
        Image.new('RGB', (40, 20), 'red').save(buffer, 'JPEG', exif=exif)
        response = self.upload(SimpleUploadedFile('rotated.jpg', buffer.getvalue(), 'image/jpeg'))
        self.assertEqual(response.status_code, 201)
        photo = Photo.objects.get()
        with Image.open(photo.image.path) as stored:
            self.assertEqual(stored.size, (20, 40))
            self.assertFalse(stored.getexif())
        self.assertFalse(b'private-location-marker' in Path(photo.image.path).read_bytes())

    def test_casefold_expansion_and_partial_model_save_keep_search_index(self):
        title = '\u0390' * 120
        response = self.upload(title=title)
        self.assertEqual(response.status_code, 201)
        photo = Photo.objects.get()
        self.assertEqual(len(photo.search_title), 360)
        photo.title = 'Updated'
        photo.save(update_fields=['title'])
        photo.refresh_from_db()
        self.assertEqual(photo.search_title, 'updated')

    def test_search_limit_and_unknown_fields_are_rejected(self):
        self.assertEqual(self.client.get('/api/photos/', {'q': 'x' * 121}).status_code, 400)
        response = self.client.post('/api/photos/', {'title': 'Photo', 'image': photo_file(), 'typo': 'ignored'}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_invalid_json_shapes_and_unrepresentable_search_are_client_errors(self):
        for payload in ([], [{'title': 'invalid'}]):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/photos/', payload, format='json').status_code, 400)
        self.assertEqual(self.client.get('/api/photos/', {'q': '\u0000'}).status_code, 400)

    def test_cleanup_defaults_to_dry_run_and_preserves_all_referenced_files(self):
        self.upload()
        photo = Photo.objects.get()
        orphan = Path(self.media.name) / 'photos' / 'orphan.png'
        orphan.write_bytes(b'orphaned-file')
        output = io.StringIO()
        call_command('cleanup_orphan_media', min_age_hours=0, stdout=output)
        self.assertTrue(orphan.exists())
        self.assertIn('photos/orphan.png', output.getvalue())
        call_command('cleanup_orphan_media', delete=True, min_age_hours=0, stdout=io.StringIO())
        self.assertFalse(orphan.exists())
        self.assertTrue(Path(photo.image.path).exists())
        self.assertTrue(Path(photo.thumbnail.path).exists())

    def test_model_and_admin_uploads_enforce_decoded_dimension_limit(self):
        from django.core.exceptions import ValidationError
        from .forms import PhotoAdminForm
        with override_settings(PHOTO_MAX_PIXELS=100):
            with self.assertRaises(ValidationError):
                Photo.objects.create(title='Direct model', image=photo_file())
            form = PhotoAdminForm({'title': 'Admin'}, {'image': photo_file()})
            self.assertFalse(form.is_valid())
            self.assertIn('image', form.errors)
        self.assertFalse(Photo.objects.exists())
        self.assertEqual(self.files(), [])

    def test_json_title_patch_works(self):
        response = self.upload()
        response = self.client.patch(f"/api/photos/{response.data['id']}/", {'title': 'New title'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['title'], 'New title')


class BootstrapTests(TestCase):
    def test_bootstrap_does_not_promote_existing_non_admin(self):
        from django.core.management.base import CommandError
        user = get_user_model().objects.create_user('owner', password='existing-password')
        with patch.dict(os.environ, {'ADMIN_USERNAME': 'owner', 'ADMIN_PASSWORD': 'new-strong-password'}):
            with self.assertRaises(CommandError):
                call_command('bootstrap_admin', stdout=io.StringIO())
        user.refresh_from_db()
        self.assertFalse(user.is_superuser or user.is_staff)
        self.assertTrue(user.check_password('existing-password'))

    def test_bootstrap_requires_credentials_and_preserves_existing_password(self):
        from django.core.management.base import CommandError
        with patch.dict(os.environ, {'ADMIN_USERNAME': '', 'ADMIN_PASSWORD': ''}):
            with self.assertRaises(CommandError):
                call_command('bootstrap_admin', stdout=io.StringIO())
        with patch.dict(os.environ, {'ADMIN_USERNAME': 'owner', 'ADMIN_PASSWORD': 'first-strong-password'}):
            call_command('bootstrap_admin', stdout=io.StringIO())
        user = get_user_model().objects.get(username='owner')
        self.assertTrue(user.is_superuser and user.is_staff and user.check_password('first-strong-password'))
        with patch.dict(os.environ, {'ADMIN_USERNAME': 'owner', 'ADMIN_PASSWORD': 'second-strong-password'}):
            call_command('bootstrap_admin', stdout=io.StringIO())
            user.refresh_from_db()
            self.assertTrue(user.check_password('first-strong-password'))
            call_command('bootstrap_admin', reset_password=True, stdout=io.StringIO())
        user.refresh_from_db()
        self.assertTrue(user.check_password('second-strong-password'))


class SearchMigrationTests(TransactionTestCase):
    def test_upgrade_handles_casefold_expansion_before_old_backfill(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('photos', '0002_photo_search_title')])
        old_model = executor.loader.project_state([('photos', '0002_photo_search_title')]).apps.get_model('photos', 'Photo')
        legacy = old_model.objects.create(title='\u0390' * 120, image='photos/legacy.png', thumbnail='thumbnails/legacy.webp')
        try:
            MigrationExecutor(connection).migrate([('photos', '0004_expand_search_title')])
            photo = Photo.objects.get(pk=legacy.pk)
            self.assertEqual(photo.search_title, '\u03b9\u0308\u0301' * 120)
            self.assertEqual(photo.image.name, 'photos/legacy.png')
            self.assertEqual(photo.thumbnail.name, 'thumbnails/legacy.webp')
        finally:
            MigrationExecutor(connection).migrate([('photos', '0004_expand_search_title')])

    def test_upgrade_repairs_stale_search_index_and_preserves_photo_paths(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('photos', '0003_backfill_search_title')])
        old_model = executor.loader.project_state([('photos', '0003_backfill_search_title')]).apps.get_model('photos', 'Photo')
        legacy = old_model.objects.create(title='Straße', search_title='stale old title', image='photos/legacy.png', thumbnail='thumbnails/legacy.webp')
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([('photos', '0004_expand_search_title')])
            photo = Photo.objects.get(pk=legacy.pk)
            self.assertEqual(photo.search_title, 'strasse')
            self.assertEqual(photo.image.name, 'photos/legacy.png')
            self.assertEqual(photo.thumbnail.name, 'thumbnails/legacy.webp')
        finally:
            MigrationExecutor(connection).migrate([('photos', '0004_expand_search_title')])

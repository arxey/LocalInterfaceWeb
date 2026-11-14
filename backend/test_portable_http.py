import importlib
import os
import runpy
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from django.urls import clear_url_caches


@override_settings(ALLOWED_HOSTS=['testserver'], DEBUG=False)
class PortableHttpTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.frontend = self.root / 'dist'
        self.static = self.root / 'static'
        self.media = self.root / 'media'
        for directory in (self.frontend / 'assets', self.static / 'admin', self.media / 'photos', self.media / 'thumbnails'):
            directory.mkdir(parents=True)
        self.index = b'<!doctype html><script src="/assets/index-hash1234.js"></script><div id="root"></div>'
        (self.frontend / 'index.html').write_bytes(self.index)
        (self.frontend / 'assets' / 'index-hash1234.js').write_bytes(b'console.log("portable-app")')
        (self.frontend / 'assets' / 'index-hash1234.css').write_bytes(b'body { color: black; }')
        (self.static / 'admin' / 'base.css').write_bytes(b'body { color: navy; }')
        (self.media / 'photos' / 'example.PNG').write_bytes(b'photo-image-bytes')
        (self.media / 'thumbnails' / 'example.webp').write_bytes(b'thumbnail-image-bytes')
        self.environment = patch.dict(os.environ, {'DJANGO_PORTABLE': '1', 'PORTABLE_FRONTEND_ROOT': str(self.frontend)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.settings_context = override_settings(STATIC_ROOT=self.static, MEDIA_ROOT=self.media)
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)
        import config.urls
        self.url_module = config.urls
        self.original_patterns = config.urls.urlpatterns
        self.addCleanup(self.restore_urls)
        importlib.reload(config.urls)
        clear_url_caches()

    def restore_urls(self):
        self.url_module.urlpatterns = self.original_patterns
        clear_url_caches()

    def response_body(self, response):
        try:
            return b''.join(response.streaming_content) if response.streaming else response.content
        finally:
            response.close()

    def test_root_serves_built_frontend_with_safe_headers_and_no_cache(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertIn("script-src 'self'", response['Content-Security-Policy'])
        self.assertIn("img-src 'self' blob:", response['Content-Security-Policy'])
        self.assertEqual(self.response_body(response), self.index)

    def test_assets_collected_admin_static_and_media_have_explicit_content_types(self):
        for url, content_type, expected in [
            ('/assets/index-hash1234.js', 'text/javascript', b'portable-app'),
            ('/assets/index-hash1234.css', 'text/css', b'color: black'),
            ('/static/admin/base.css', 'text/css', b'color: navy'),
            ('/media/photos/example.PNG', 'image/png', b'photo-image-bytes'),
            ('/media/thumbnails/example.webp', 'image/webp', b'thumbnail-image-bytes'),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response['Content-Type'].startswith(content_type))
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                if url.startswith('/media/'):
                    self.assertIn('sandbox', response['Content-Security-Policy'])
                    self.assertIn("default-src 'none'", response['Content-Security-Policy'])
                self.assertIn(expected, self.response_body(response))

    def test_missing_assets_directories_and_unknown_api_never_fall_back_to_spa(self):
        for url in ('/assets/missing.js', '/assets/', '/static/', '/media/', '/api/unknown/', '/arbitrary-client-route', '/index.html'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertNotEqual(self.response_body(response), self.index)

    def test_files_outside_asset_roots_and_windows_special_paths_are_denied(self):
        (self.frontend / 'outside.js').write_bytes(b'private-outside-asset-root')
        (self.root / 'private.css').write_bytes(b'private-outside-static-root')
        for url in ('/assets/../outside.js', '/assets/%2e%2e/outside.js', '/assets/..%5coutside.js', '/static/../private.css', '/assets/C:/outside.js', '/media/photos/example.PNG:secret.png'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_hidden_files_secret_extensions_source_maps_and_active_media_are_denied(self):
        forbidden = [
            (self.frontend, '.env', '/.env'),
            (self.frontend / 'assets', '.private.js', '/assets/.private.js'),
            (self.frontend / 'assets', 'index.js.map', '/assets/index.js.map'),
            (self.static, 'settings.py', '/static/settings.py'),
            (self.static, 'private.key', '/static/private.key'),
            (self.media / 'photos', 'active.svg', '/media/photos/active.svg'),
            (self.media / 'photos', 'active.html', '/media/photos/active.html'),
        ]
        for directory, name, url in forbidden:
            with self.subTest(url=url):
                (directory / name).write_bytes(b'private-file-marker')
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_symlinks_cannot_escape_an_asset_root(self):
        outside = self.root / 'private'
        outside.mkdir()
        (outside / 'secret.js').write_bytes(b'private-file-marker')
        link = self.frontend / 'assets' / 'outside'
        if os.name == 'nt':
            # NTFS junctions need neither administrator rights nor Developer
            # Mode, unlike symbolic links on an ordinary Windows account.
            subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)],
                           capture_output=True, check=True)
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.client.get('/assets/outside/secret.js').status_code, 404)

    def test_head_preserves_headers_and_file_routes_reject_post(self):
        response = self.client.head('/assets/index-hash1234.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_body(response), b'')
        self.assertGreater(int(response['Content-Length']), 0)
        self.assertEqual(self.client.post('/assets/index-hash1234.js').status_code, 405)

    def test_native_routes_are_disabled_without_explicit_opt_in(self):
        with patch.dict(os.environ, {'DJANGO_PORTABLE': '0'}):
            importlib.reload(self.url_module)
            clear_url_caches()
            for url in ('/', '/assets/index-hash1234.js', '/static/admin/base.css', '/media/photos/example.PNG'):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get('/api/auth/').status_code, 200)

    def test_native_auth_and_admin_login_share_expiring_attempt_budget(self):
        with patch('portable_http.time.monotonic', return_value=0):
            for attempt in range(10):
                if attempt % 2:
                    response = self.client.post('/admin/login/', {'username': '', 'password': ''})
                    self.assertEqual(response.status_code, 200)
                else:
                    response = self.client.post('/api/auth/', {'username': 'invalid!', 'password': 'password'}, content_type='application/json')
                    self.assertEqual(response.status_code, 400)
            for url in ('/api/auth/', '/admin/login/'):
                response = self.client.post(url, {}, content_type='application/json')
                self.assertEqual(response.status_code, 429)
                self.assertGreater(int(response['Retry-After']), 0)
            self.assertEqual(self.client.get('/api/auth/').status_code, 200)
        with patch('portable_http.time.monotonic', return_value=61):
            response = self.client.post('/api/auth/', {'username': 'invalid!', 'password': 'password'}, content_type='application/json')
            self.assertEqual(response.status_code, 400)


class PortableConfigurationTests(SimpleTestCase):
    def test_portable_mode_never_loads_dotenv_from_bundle_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'backend' / 'config'
            config.mkdir(parents=True)
            settings_path = config / 'settings.py'
            original = Path(__file__).parent / 'config' / 'settings.py'
            settings_path.write_bytes(original.read_bytes())
            (root / '.env').write_text('DJANGO_SECRET_KEY=untrusted-file-secret\n', encoding='utf-8')
            with patch.dict(os.environ, {'DJANGO_PORTABLE': '1', 'USE_SQLITE_FOR_TESTS': '1'}, clear=True):
                with self.assertRaises(ImproperlyConfigured):
                    runpy.run_path(str(settings_path))

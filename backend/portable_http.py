"""Opt-in file routes for the loopback-only native Windows distribution.

This deliberately serves a small set of built/public files. It is not a generic
document root, a development server, or a fallback for unknown API routes.
"""
import math
import os
import threading
import time
from collections import deque
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.http import FileResponse, Http404, JsonResponse
from django.urls import path
from django.utils._os import safe_join
from django.views.decorators.http import require_safe


RASTER_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jpe': 'image/jpeg',
    '.jfif': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
}
STATIC_TYPES = {
    **RASTER_TYPES,
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.gif': 'image/gif', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
    '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
    '.otf': 'font/otf', '.eot': 'application/vnd.ms-fontobject',
}
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
MEDIA_CSP = "default-src 'none'; sandbox; frame-ancestors 'none'"


def _public_file(root, relative, content_types):
    # Reject Windows drive letters, alternate data streams, backslashes and
    # hidden components even when the tests/server run on a different OS.
    if (not relative or len(relative) > 1024 or any(char in relative for char in ('\\', ':', '\x00'))
            or any(not part or part.startswith('.') or part.endswith((' ', '.')) for part in relative.split('/'))):
        raise Http404
    content_type = content_types.get(Path(relative).suffix.casefold())
    if content_type is None:
        raise Http404
    try:
        resolved_root = Path(root).resolve(strict=True)
        candidate = Path(safe_join(str(resolved_root), relative)).resolve(strict=True)
        if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
            raise Http404
        stream = candidate.open('rb')
    except (OSError, ValueError, SuspiciousFileOperation) as exc:
        raise Http404 from exc
    return stream, content_type


def _file_response(root, relative, content_types, *, media=False, index=False):
    stream, content_type = _public_file(root, relative, content_types)
    response = FileResponse(stream, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Frame-Options'] = 'DENY'
    response['Content-Security-Policy'] = MEDIA_CSP if media else APP_CSP
    response['Cache-Control'] = 'no-cache' if index else 'public, max-age=3600'
    return response


def file_urlpatterns():
    configured_frontend = os.getenv('PORTABLE_FRONTEND_ROOT', '')
    if not configured_frontend or not Path(configured_frontend).is_absolute():
        raise ImproperlyConfigured('Portable mode requires an absolute PORTABLE_FRONTEND_ROOT.')
    frontend = Path(configured_frontend)

    @require_safe
    def index(request):
        return _file_response(frontend, 'index.html', {'.html': 'text/html; charset=utf-8'}, index=True)

    @require_safe
    def assets(request, relative):
        return _file_response(frontend / 'assets', relative, STATIC_TYPES)

    @require_safe
    def static(request, relative):
        return _file_response(settings.STATIC_ROOT, relative, STATIC_TYPES)

    @require_safe
    def media(request, relative):
        if relative.split('/', 1)[0] not in {'photos', 'thumbnails'}:
            raise Http404
        return _file_response(settings.MEDIA_ROOT, relative, RASTER_TYPES, media=True)

    return [
        path('', index, name='portable-index'),
        path('assets/<path:relative>', assets, name='portable-assets'),
        path('static/<path:relative>', static, name='portable-static'),
        path('media/<path:relative>', media, name='portable-media'),
    ]


class LoginAttemptLimiter:
    """One shared budget for native auth/admin login in one Waitress process."""

    def __init__(self, attempts=10, window_seconds=60):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._sources = {}
        self._lock = threading.Lock()

    def wrap(self, view):
        @wraps(view)
        def limited(request, *args, **kwargs):
            if request.method == 'POST':
                # Never trust client-controlled forwarding headers on loopback.
                source = request.META.get('REMOTE_ADDR', '')
                now = time.monotonic()
                with self._lock:
                    recent = self._sources.setdefault(source, deque())
                    while recent and recent[0] <= now - self.window_seconds:
                        recent.popleft()
                    if len(recent) >= self.attempts:
                        response = JsonResponse({'detail': 'Слишком много попыток входа. Повторите позже.'}, status=429)
                        response['Retry-After'] = str(max(1, math.ceil(recent[0] + self.window_seconds - now)))
                        response['Cache-Control'] = 'no-store'
                        return response
                    recent.append(now)
            return view(request, *args, **kwargs)
        return limited

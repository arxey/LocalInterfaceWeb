import json

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from rest_framework import serializers, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from .models import Photo
from .serializers import PhotoSerializer


def csrf_failure(request, reason=''):
    return JsonResponse({'detail': 'Проверка CSRF не пройдена. Обновите страницу и повторите действие.'}, status=403)


@never_cache
@ensure_csrf_cookie
@csrf_protect
@require_http_methods(['GET', 'POST', 'DELETE'])
def auth_view(request):
    if request.method == 'POST':
        if request.content_type != 'application/json':
            return JsonResponse({'detail': 'Ожидается JSON.'}, status=415)
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'detail': 'Некорректный JSON.'}, status=400)
        if (not isinstance(data, dict) or set(data) != {'username', 'password'}
                or not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str)
                or not 0 < len(data['username']) <= 150 or not 0 < len(data['password']) <= 4096):
            return JsonResponse({'detail': 'Введите имя пользователя и пароль.'}, status=400)
        try:
            get_user_model()._meta.get_field('username').run_validators(data['username'])
        except ValidationError:
            return JsonResponse({'detail': 'Некорректное имя пользователя.'}, status=400)
        user = authenticate(request, username=data['username'], password=data['password'])
        if user is None:
            return JsonResponse({'detail': 'Неверное имя пользователя или пароль.'}, status=401)
        login(request, user)
    elif request.method == 'DELETE':
        logout(request)
    return JsonResponse({'authenticated': request.user.is_authenticated,
                         'username': request.user.get_username() if request.user.is_authenticated else '',
                         'csrfToken': get_token(request)})


@never_cache
@require_http_methods(['GET'])
def health_view(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'status': 'unavailable', 'database': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok', 'database': 'ok'})


class PhotoViewSet(viewsets.ModelViewSet):
    queryset = Photo.objects.all()
    serializer_class = PhotoSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        raw_query = self.request.query_params.get('q', '')
        if len(raw_query) > 120:
            raise serializers.ValidationError({'q': 'Поиск ограничен 120 символами.'})
        try:
            query = serializers.CharField(allow_blank=True).run_validation(raw_query).casefold()
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'q': exc.detail}) from exc
        return queryset.filter(search_title__contains=query) if query else queryset

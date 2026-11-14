"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from photos.views import PhotoViewSet, auth_view, health_view

router = DefaultRouter()
router.register('photos', PhotoViewSet, basename='photo')

portable = os.getenv('DJANGO_PORTABLE') == '1'
auth_endpoint = auth_view
native_patterns = []
if portable:
    from portable_http import LoginAttemptLimiter, file_urlpatterns

    limiter = LoginAttemptLimiter()
    auth_endpoint = limiter.wrap(auth_view)
    native_patterns = [path('admin/login/', limiter.wrap(admin.site.login))]

urlpatterns = native_patterns + [
    path('admin/', admin.site.urls),
    path('api/auth/', auth_endpoint, name='auth'),
    path('api/health/', health_view, name='health'),
    path('api/', include(router.urls)),
]

if portable:
    urlpatterns += file_urlpatterns()
elif settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

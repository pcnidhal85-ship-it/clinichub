from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage
from django.conf import settings
from django.conf.urls.static import static

from ClinicHub.health import health_check, healthz_liveness

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=staticfiles_storage.url('images/favicon.svg')), name='favicon'),
    path('health/', health_check, name='health-check'),
    path('healthz/', healthz_liveness, name='healthz-liveness'),
    path('admin/', admin.site.urls),
    path('', include('users.urls')),
    path('clinic/', include('clinic.urls')),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )

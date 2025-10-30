from django.conf import settings

from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static


urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("steeloweb.urls")),
]

if settings.DEBUG:
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar  # type: ignore[import]

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
        ] + urlpatterns

    # Add a media URL pattern for development
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

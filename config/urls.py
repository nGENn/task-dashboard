from django.conf import settings
from django.conf.urls.static import static
from django.http import Http404
from django.urls import include
from django.urls import path
from django.views import defaults as default_views
from django.views.generic import RedirectView
from django.views.generic import TemplateView

from task_dashboard.users.admin_site import admin_site
from task_dashboard.users.views import DashboardView


def _password_reset_disabled(request, **kwargs):
    raise Http404


urlpatterns = [
    path("", DashboardView.as_view(perspective="home"), name="home"),
    path("my", DashboardView.as_view(perspective="my"), name="my_tasks"),
    path("open", DashboardView.as_view(perspective="open"), name="open_tasks"),
    path("all", DashboardView.as_view(perspective="all"), name="all_tasks"),
    path(
        "unassigned",
        DashboardView.as_view(perspective="unassigned"),
        name="unassigned_tasks",
    ),
    path(
        "about/",
        TemplateView.as_view(template_name="pages/about.html"),
        name="about",
    ),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin_site.urls),
    # User management
    path("users/", include("task_dashboard.users.urls", namespace="users")),
    path(
        "accounts/signup/",
        RedirectView.as_view(pattern_name="account_login", permanent=True),
    ),
    path("accounts/password/reset/", _password_reset_disabled),
    path("accounts/password/reset/done/", _password_reset_disabled),
    path("accounts/password/reset/key/<uidb36>-<key>/", _password_reset_disabled),
    path("accounts/password/reset/key/done/", _password_reset_disabled),
    path("accounts/", include("allauth.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    # Your stuff: custom urls includes go here
    # ...
    # Media files
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]


if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
            *urlpatterns,
        ]

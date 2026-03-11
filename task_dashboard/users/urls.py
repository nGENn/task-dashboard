from django.urls import path

from .views import DashboardView
from .views import delete_saved_view
from .views import force_refresh_view
from .views import refresh_single_task_view
from .views import save_view
from .views import user_detail_view
from .views import user_redirect_view
from .views import user_update_view

app_name = "users"
urlpatterns = [
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("~update/", view=user_update_view, name="update"),
    path("<int:pk>/", view=user_detail_view, name="detail"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("force-refresh/", force_refresh_view, name="force-refresh"),
    path("tasks/<int:pk>/refresh/", refresh_single_task_view, name="refresh-single-task"),
    path("saved-views/save/", save_view, name="save_view"),
    path(
        "saved-views/<int:pk>/delete/",
        delete_saved_view,
        name="delete_saved_view",
    ),
    path("", DashboardView.as_view(), name="home"),
]

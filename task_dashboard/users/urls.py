from django.urls import path

from .views import DashboardView
from .views import delete_saved_view
from .views import force_refresh_view
from .views import refresh_single_task_view
from .views import save_view
from .views import stats_view
from .views import user_update_view

app_name = "users"
urlpatterns = [
    path("~update/", view=user_update_view, name="update"),
    path("force-refresh/", force_refresh_view, name="force-refresh"),
    path(
        "tasks/<int:pk>/refresh/", refresh_single_task_view, name="refresh-single-task"
    ),
    path("saved-views/save/", save_view, name="save_view"),
    path(
        "saved-views/<int:pk>/delete/",
        delete_saved_view,
        name="delete_saved_view",
    ),
    path("stats/", stats_view, name="stats"),
    path("", DashboardView.as_view(), name="home"),
]

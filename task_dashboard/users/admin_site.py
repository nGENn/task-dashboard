from typing import TYPE_CHECKING

from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

if TYPE_CHECKING:
    from django.contrib.admin import ModelAdmin

    _Base = ModelAdmin
else:
    _Base = object


class TaskDashboardAdminSite(UnfoldAdminSite):
    site_header = "Task Dashboard"
    site_title = "Task Dashboard"
    index_title = _("Administration")


admin_site = TaskDashboardAdminSite(name="admin")


class SingletonModelAdmin(_Base):
    """Mixin for singleton (pk=1) models.

    Skips the changelist — there is only ever one row — and redirects
    straight to its change page, creating the row if it does not exist.
    Use on a model that exposes a ``load()`` classmethod.
    """

    def changelist_view(self, request, extra_context=None):
        obj = self.model.load()
        meta = self.model._meta  # noqa: SLF001
        url = reverse(
            f"{self.admin_site.name}:{meta.app_label}_{meta.model_name}_change",
            args=[obj.pk],
        )
        return redirect(url)

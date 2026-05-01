from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite


class TaskDashboardAdminSite(UnfoldAdminSite):
    site_header = "Task Dashboard"
    site_title = "Task Dashboard"
    index_title = _("Administration")


admin_site = TaskDashboardAdminSite(name="admin")

from http import HTTPStatus

import pytest
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import Group
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.http import HttpResponseRedirect
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from task_dashboard.users.forms import UserAdminChangeForm
from task_dashboard.users.models import ExternalGroup
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import ServicePermission
from task_dashboard.users.models import Task
from task_dashboard.users.models import TaskPermission
from task_dashboard.users.models import User
from task_dashboard.users.tests.factories import UserFactory
from task_dashboard.users.views import DashboardView
from task_dashboard.users.views import UserRedirectView
from task_dashboard.users.views import UserUpdateView
from task_dashboard.users.views import user_detail_view

pytestmark = pytest.mark.django_db


class TestUserUpdateView:
    """
    TODO:
        extracting view initialization code as class-scoped fixture
        would be great if only pytest-django supported non-function-scoped
        fixture db access -- this is a work-in-progress for now:
        https://github.com/pytest-dev/pytest-django/pull/258
    """

    def dummy_get_response(self, request: HttpRequest):
        return None

    def test_get_success_url(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")
        request.user = user

        view.request = request
        assert view.get_success_url() == f"/users/{user.pk}/"

    def test_get_object(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")
        request.user = user

        view.request = request

        assert view.get_object() == user

    def test_form_valid(self, user: User, rf: RequestFactory):
        view = UserUpdateView()
        request = rf.get("/fake-url/")

        # Add the session/message middleware to the request
        SessionMiddleware(self.dummy_get_response).process_request(request)
        MessageMiddleware(self.dummy_get_response).process_request(request)
        request.user = user

        view.request = request

        # Initialize the form
        form = UserAdminChangeForm()
        form.cleaned_data = {}
        form.instance = user
        view.form_valid(form)

        messages_sent = [m.message for m in messages.get_messages(request)]
        assert messages_sent == [_("Information successfully updated")]


class TestUserRedirectView:
    def test_get_redirect_url(self, user: User, rf: RequestFactory):
        view = UserRedirectView()
        request = rf.get("/fake-url")
        request.user = user

        view.request = request
        assert view.get_redirect_url() == f"/users/{user.pk}/"


class TestUserDetailView:
    def test_authenticated(self, user: User, rf: RequestFactory):
        request = rf.get("/fake-url/")
        request.user = UserFactory()
        response = user_detail_view(request, pk=user.pk)

        assert response.status_code == HTTPStatus.OK

    def test_not_authenticated(self, user: User, rf: RequestFactory):
        request = rf.get("/fake-url/")
        request.user = AnonymousUser()
        response = user_detail_view(request, pk=user.pk)
        login_url = reverse(settings.LOGIN_URL)

        assert isinstance(response, HttpResponseRedirect)
        assert response.status_code == HTTPStatus.FOUND
        assert response.url == f"{login_url}?next=/fake-url/"


class TestDashboardView:
    def test_adjustable_default_states(self, user: User, rf: RequestFactory):
        service = ServiceConfiguration.objects.create(
            name="Test Service",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        # Create tasks with different states
        Task.objects.create(
            external_id="T1", title="Open", status="open", service=service
        )
        Task.objects.create(
            external_id="T2", title="Pending", status="pending", service=service
        )
        Task.objects.create(
            external_id="T3", title="Closed", status="closed", service=service
        )

        # 1. Default settings (open,pending)
        settings = GlobalSetting.load()
        settings.default_task_states = "open,pending"
        settings.save()

        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]
        statuses = {t.status for t in tasks}
        assert "open" in statuses
        assert "pending" in statuses
        assert "closed" not in statuses

        # 2. Change settings to only show 'open'
        settings.default_task_states = "open"
        settings.save()

        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]
        statuses = {t.status for t in tasks}
        assert "open" in statuses
        assert "pending" not in statuses
        assert "closed" not in statuses

        # 3. Verify explicit filter overrides default
        request = rf.get("/?view=all&state=closed")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]
        statuses = {t.status for t in tasks}
        assert "closed" in statuses
        assert "open" not in statuses

    def test_priority_sorting(self, user: User, rf: RequestFactory):
        service = ServiceConfiguration.objects.create(
            name="Test Service",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        priorities = ["Low", "Medium", "High", "Critical"]
        for i, p in enumerate(priorities):
            Task.objects.create(
                external_id=f"TASK-{i}",
                title=f"Task {p}",
                status="open",
                service=service,
                priority=p,
                updated_at=timezone.now(),
            )

        # ASC: Critical, High, Medium, Low
        request = rf.get("/?sort=priority&direction=asc&view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]

        task_priorities = [t.priority for t in tasks]
        assert task_priorities == ["Critical", "High", "Medium", "Low"]

        # DESC: Low, Medium, High, Critical
        request = rf.get("/?sort=priority&direction=desc&view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]

        task_priorities = [t.priority for t in tasks]
        assert task_priorities == ["Low", "Medium", "High", "Critical"]

    def test_default_access_level_permission(self, user: User, rf: RequestFactory):
        # Service with default access level FULL
        service_config = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )

        # 2. Create Tasks in Database
        test_datetime = timezone.now()
        Task.objects.create(
            external_id="ZAM-1",
            title="My Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email=user.email,
            owner=user.name,
            priority="medium",
            updated_at=test_datetime,
        )
        Task.objects.create(
            external_id="ZAM-2",
            title="Other Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email="other@example.com",
            owner="Other User",
            priority="medium",
            updated_at=test_datetime,
        )

        # 3. Request (no groups assigned to user)
        request = rf.get("/?view=all&owner=Other User&owner=" + user.name)
        request.user = user

        # 4. Execute View
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        # 5. Verify Results
        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        # Because default access level is FULL, the user should see both tasks
        assert "ZAM-1" in task_ids
        assert "ZAM-2" in task_ids
        assert len(tasks) == 2  # noqa: PLR2004

        # Now test that a group can override it to NONE
        group = Group.objects.create(name="Restricted Group")
        user.groups.add(group)

        ext_group = ExternalGroup.objects.create(
            origin="Zammad",
            name="Support",
        )
        TaskPermission.objects.create(
            django_group=group,
            allowed_external_group=ext_group,
            access_level="NONE",
        )

        context = view.get_context_data()
        tasks = context["tasks"].object_list
        assert len(tasks) == 0

    def test_name_based_ownership_mapping_failed(self, user: User, rf: RequestFactory):
        # 1. Setup Data
        user.name = "Test User"
        user.save()

        service_config = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
            default_access_level="OWN",
        )

        # 2. Create Task with matching name but NO email (failed mapping)
        test_datetime = timezone.now()
        Task.objects.create(
            external_id="ZAM-1",
            title="My Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email="",  # FAILED MAPPING
            owner="Test User",
            priority="medium",
            updated_at=test_datetime,
        )

        # 3. Request
        request = rf.get("/")
        request.user = user

        # 4. Execute View
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        # 5. Verify Results
        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        # Should match by name even if email is empty
        assert "ZAM-1" in task_ids
        assert len(tasks) == 1

    def test_unassigned_logic_requires_both_empty(self, user: User, rf: RequestFactory):
        # Service with FULL access
        service_config = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )

        # 1. Task with name but no email (NOT unassigned)
        Task.objects.create(
            external_id="ZAM-1",
            title="Name Only",
            status="open",
            service=service_config,
            owner="Other Person",
            owner_email="",
            updated_at=timezone.now(),
        )
        # 2. Task with email but no name (NOT unassigned)
        Task.objects.create(
            external_id="ZAM-2",
            title="Email Only",
            status="open",
            service=service_config,
            owner="",
            owner_email="test@example.com",
            updated_at=timezone.now(),
        )
        # 3. Task with neither (UNASSIGNED)
        Task.objects.create(
            external_id="ZAM-3",
            title="True Unassigned",
            status="open",
            service=service_config,
            owner="",
            owner_email="",
            updated_at=timezone.now(),
        )

        # Execute View
        request = rf.get("/?view=unassigned")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        assert "ZAM-3" in task_ids
        assert "ZAM-1" not in task_ids
        assert "ZAM-2" not in task_ids
        assert len(tasks) == 1

        # Check filter options too
        assert "Unassigned" in context["filter_options"]["owners"]
        assert "Other Person" in context["filter_options"]["owners"]
        assert "test@example.com" in context["filter_options"]["owners"]

    def test_reversed_name_mapping(self, user: User, rf: RequestFactory):
        """Tests that 'Lastname Firstname' correctly maps to 'lastname@example.com'."""
        service_config = ServiceConfiguration.objects.create(
            name="OpenProject",
            service_type="openproject",
            is_active=True,
            default_access_level="FULL",
        )

        # Task 1: Correct email (establishes the 'landefeld' canonical identity)
        Task.objects.create(
            external_id="OP-1",
            title="Task with Email",
            status="open",
            service=service_config,
            owner="",
            owner_email="landefeld@ngenn.net",
            updated_at=timezone.now(),
        )

        # Task 2: Reversed name 'Landefeld Klaus' (should map to 'landefeld')
        Task.objects.create(
            external_id="OP-2",
            title="Task with Reversed Name",
            status="open",
            service=service_config,
            owner="Landefeld Klaus",
            owner_email="",
            updated_at=timezone.now(),
        )

        # Request
        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        # Check filter options: should only have ONE owner (landefeld@ngenn.net)
        # instead of two (landefeld@... and Landefeld Klaus or Klaus)
        owners = context["filter_options"]["owners"]
        assert "landefeld@ngenn.net" in owners
        assert "Landefeld Klaus" not in owners
        assert "Klaus" not in owners

        # Verify both tasks show up under the same owner in the table (unified display)
        # The view logic unifies owner_email for display if they share canonical ID
        tasks = context["tasks"].object_list
        for t in tasks:
            assert t.owner_email == "landefeld@ngenn.net"
            assert t.owner == ""

    def test_own_only_permission(self, user: User, rf: RequestFactory):
        # 1. Setup Data
        group = Group.objects.create(name="Support Group")
        user.groups.add(group)

        ext_group = ExternalGroup.objects.create(
            origin="Zammad",
            name="Support",
        )
        TaskPermission.objects.create(
            django_group=group,
            allowed_external_group=ext_group,
            access_level="OWN",
        )

        service_config = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
        )

        # 2. Create Tasks in Database
        test_datetime = timezone.now()
        Task.objects.create(
            external_id="ZAM-1",
            title="My Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email=user.email,
            owner=user.name,
            priority="medium",
            updated_at=test_datetime,
        )
        Task.objects.create(
            external_id="ZAM-2",
            title="Other Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email="other@example.com",
            owner="Other User",
            priority="medium",
            updated_at=test_datetime,
        )
        Task.objects.create(
            external_id="ZAM-3",
            title="Unassigned Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email="",
            owner="Unassigned",
            priority="medium",
            updated_at=test_datetime,
        )

        # 3. Request
        request = rf.get("/")
        request.user = user

        # 4. Execute View
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        # 5. Verify Results
        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        assert "ZAM-1" in task_ids
        assert "ZAM-2" not in task_ids
        assert "ZAM-3" not in task_ids
        assert len(tasks) == 1

    def test_advanced_ownership_mapping(self, user: User, rf: RequestFactory):
        # Setup: User "First Last" with email "last@example.com"
        user.name = "First Last"
        user.email = "last@example.com"
        user.save()

        service_config = ServiceConfiguration.objects.create(
            name="GitLab",
            service_type="gitlab",
            is_active=True,
            default_access_level="FULL",
        )

        # 1. Task with Gitlab username "flast" (Matches by "last" suffix)
        Task.objects.create(
            external_id="GL-1",
            title="Gitlab Task",
            status="open",
            service=service_config,
            owner="flast",
            owner_email="",
            updated_at=timezone.now(),
        )

        # 2. Task with "Jane Smithers"
        # (Matches "smithers@example.com" if that was the filter)
        Task.objects.create(
            external_id="GL-2",
            title="Jane Task",
            status="open",
            service=service_config,
            owner="Jane Smithers",
            owner_email="",
            updated_at=timezone.now(),
        )

        # Request as test user
        request = rf.get("/?view=my")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        # User should see GL-1 because "flast" matches his last name/email prefix
        assert "GL-1" in task_ids

        # Test Filtering by "smithers@example.com" should find "Jane Smithers"
        # We use view=all so we don't only see "my" tasks
        request = rf.get("/?view=all&owner=smithers@example.com")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]
        assert "GL-2" in task_ids

        # Check UI Dropdown consolidation
        owners = context["filter_options"]["owners"]
        # Both "First Last" and "flast" should be represented by one email
        assert "last@example.com" in owners
        assert "First Last" not in owners
        assert "flast" not in owners

    def test_owner_unification_truncated_email(self, user: User, rf: RequestFactory):
        """Verify that 'boeckmann@ngenn.net' overrides 'boeckmann@ngenn.'"""
        service = ServiceConfiguration.objects.create(
            name="Test Service",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )

        # Use two tasks for the same person with different email formats
        Task.objects.create(
            external_id="T1",
            title="Task 1",
            status="open",
            service=service,
            owner_email="boeckmann@ngenn.net",
        )
        Task.objects.create(
            external_id="T2",
            title="Task 2",
            status="open",
            service=service,
            owner_email="boeckmann@ngenn.",
        )

        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        # Check filter options: only the full email should remain
        owners = context["filter_options"]["owners"]
        assert "boeckmann@ngenn.net" in owners
        assert "boeckmann@ngenn." not in owners

        # Check task display: both should show the full email
        tasks = context["tasks"].object_list
        for t in tasks:
            assert t.owner_email == "boeckmann@ngenn.net"

    def test_service_permission_overrides_default_access_level(
        self, user: User, rf: RequestFactory
    ):
        # 1. Setup Service with Global NONE
        service_config = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
            default_access_level="NONE",
        )

        Task.objects.create(
            external_id="ZAM-1",
            title="My Task",
            status="open",
            service=service_config,
            group="Support",
            owner_email=user.email,
        )

        # 2. Assign Group with Service-Level LIMITED
        group = Group.objects.create(name="Support Team")
        user.groups.add(group)
        ServicePermission.objects.create(
            django_group=group,
            service=service_config,
            access_level="LIMITED",
        )

        # 3. Verify user sees their own task (LIMITED) instead of nothing (NONE)
        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]
        assert "ZAM-1" in task_ids

    def test_task_permission_overrides_service_permission(
        self, user: User, rf: RequestFactory
    ):
        # 1. Setup Service with Service-Level NONE via Group
        service_config = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )

        group = Group.objects.create(name="Support Team")
        user.groups.add(group)

        # Service-level NONE
        ServicePermission.objects.create(
            django_group=group,
            service=service_config,
            access_level="NONE",
        )

        ext_group = ExternalGroup.objects.create(
            origin="Zammad",
            name="Support",
        )

        # 2. Task-level FULL for specific group
        TaskPermission.objects.create(
            django_group=group,
            allowed_external_group=ext_group,
            access_level="FULL",
        )

        Task.objects.create(
            external_id="ZAM-1",
            title="Group Task",
            status="open",
            service=service_config,
            group="Support",
        )

        # 3. Verify user sees the task because TaskPermission
        # overrides ServicePermission
        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]
        assert "ZAM-1" in task_ids

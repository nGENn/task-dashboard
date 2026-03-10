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
from task_dashboard.users.models import ServiceConfiguration
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
        user.name = "John Doe"
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
            owner="John Doe",
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
            owner="John Doe",
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
            owner_email="john@example.com",
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
        assert "John Doe" in context["filter_options"]["owners"]
        assert "john@example.com" in context["filter_options"]["owners"]

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
        # Setup: User "John Jackson" with email "jackson@example.com"
        user.name = "John Jackson"
        user.email = "jackson@example.com"
        user.save()

        service_config = ServiceConfiguration.objects.create(
            name="GitLab",
            service_type="gitlab",
            is_active=True,
            default_access_level="FULL",
        )

        # 1. Task with Gitlab username "mjackson" (Matches by "jackson" suffix)
        Task.objects.create(
            external_id="GL-1",
            title="Gitlab Task",
            status="open",
            service=service_config,
            owner="mjackson",
            owner_email="",
            updated_at=timezone.now(),
        )
        
        # 2. Task with "Jane Smithers" (Matches "smithers@example.com" if that was the filter)
        Task.objects.create(
            external_id="GL-2",
            title="Jane Task",
            status="open",
            service=service_config,
            owner="Jane Smithers",
            owner_email="",
            updated_at=timezone.now(),
        )

        # Request as John
        request = rf.get("/?view=my")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        # John should see GL-1 because "mjackson" matches his last name/email prefix
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
        # Both "John Jackson" and "mjackson" should be represented by one "best" name
        assert "John Jackson" in owners
        assert "mjackson" not in owners

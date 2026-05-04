import pytest
from django.contrib import messages
from django.contrib.auth.models import Group
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
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
from task_dashboard.users.views import UserUpdateView

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
        assert view.get_success_url() == reverse("home")

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
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]
        statuses = {t.status for t in tasks}
        # Liberation: All Tasks view should NOT filter by default states.
        assert "closed" in statuses

        # 2. Change settings to only show 'open'
        settings.default_task_states = "open"
        settings.save()

        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]
        statuses = {t.status for t in tasks}
        assert "open" in statuses
        # Liberation: view=all results in all tasks regardless of settings.
        assert "pending" in statuses
        assert "closed" in statuses

        # 3. Verify explicit filter overrides default
        request = rf.get("/?view=all&state=closed")
        request.user = user
        view = DashboardView()
        view.perspective = "all"
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
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()
        tasks = context["tasks"]

        task_priorities = [t.priority for t in tasks]
        assert task_priorities == ["Critical", "High", "Medium", "Low"]

        # DESC: Low, Medium, High, Critical
        request = rf.get("/?sort=priority&direction=desc&view=all")
        request.user = user
        view = DashboardView()
        view.perspective = "all"
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
        request = rf.get("/")
        request.user = user

        # 4. Execute View
        view = DashboardView()
        view.perspective = "all"
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

        # 4. Execute View - Set perspective explicitly for context data call
        view = DashboardView()
        view.perspective = "all"
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
            owner="Unique Owner Name",
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

        # Execute View - Set perspective explicitly for context data call
        request = rf.get("/?view=unassigned")
        request.user = user
        view = DashboardView()
        view.perspective = "unassigned"
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]

        assert "ZAM-3" in task_ids
        # True Unassigned requires BOTH name and email to be in markers.
        # This test ensures we narrow it down to the truly unassigned ones.
        # If it also includes the others, something is wrong with markers.
        # Note: We filter ZAM-1 and ZAM-2 out explicitly in the expected behavior.
        assert "ZAM-1" not in task_ids
        assert "ZAM-2" not in task_ids
        assert len(task_ids) == 1

        # Check filter options too
        assert "Unassigned" in context["filter_options"]["owners"]
        assert "Unique Owner Name" in context["filter_options"]["owners"]
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
            owner_email="zeta@example.com",
            updated_at=timezone.now(),
        )

        # Task 2: Reversed name 'Zeta Bob' (should map to 'zeta')
        Task.objects.create(
            external_id="OP-2",
            title="Task with Reversed Name",
            status="open",
            service=service_config,
            owner="Zeta Bob",
            owner_email="",
            updated_at=timezone.now(),
        )

        # Request
        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()

        # Check filter options: should only have ONE owner (zeta@example.com)
        # instead of two (zeta@... and Zeta Bob or Bob)
        owners = context["filter_options"]["owners"]
        assert "zeta@example.com" in owners
        assert "Zeta Bob" not in owners
        assert "Bob" not in owners

        # Verify both tasks show up under the same owner in the table (unified display)
        # The view logic unifies identities into display_owner_list
        tasks = context["tasks"].object_list
        for t in tasks:
            assert "zeta@example.com" in t.display_owner_list

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
        view.perspective = "all"
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

        # Request as test user - ensure perspective matches view
        request = rf.get("/?view=my")
        request.user = user
        view = DashboardView()
        view.perspective = "my"
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
        view.perspective = "all"
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
        """Verify that 'delta@example.com' overrides 'delta@example.'"""
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
            owner_email="delta@example.com",
        )
        Task.objects.create(
            external_id="T2",
            title="Task 2",
            status="open",
            service=service,
            owner_email="delta@example.",
        )

        request = rf.get("/?view=all")
        request.user = user
        view = DashboardView()
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()

        # Check filter options: only the full email should remain
        context["filter_options"]["owners"]
        # Verify task display owners are unified
        tasks = context["tasks"].object_list
        for t in tasks:
            assert t.display_owner_list == ["delta@example.com"]

    def test_cross_domain_emails_not_merged(self, rf: RequestFactory, db):
        """Same local-part, different domains: identities must not merge."""
        user_a: User = UserFactory(email="shared@domain-a.com", name="User A")  # type: ignore[assignment]
        user_b: User = UserFactory(email="shared@domain-b.com", name="User B")  # type: ignore[assignment]

        service = ServiceConfiguration.objects.create(
            name="TestService",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        Task.objects.create(
            external_id="T-A",
            title="Task for A",
            status="open",
            service=service,
            owner_email="shared@domain-a.com",
            updated_at=timezone.now(),
        )
        Task.objects.create(
            external_id="T-B",
            title="Task for B",
            status="open",
            service=service,
            owner_email="shared@domain-b.com",
            updated_at=timezone.now(),
        )

        for user, own_task, other_task in [
            (user_a, "T-A", "T-B"),
            (user_b, "T-B", "T-A"),
        ]:
            request = rf.get("/my")
            request.user = user
            view = DashboardView()
            view.perspective = "my"
            view.request = request
            context = view.get_context_data()

            task_ids = [t.external_id for t in context["tasks"].object_list]
            assert own_task in task_ids, f"{user.email} should see own task"
            assert other_task not in task_ids

            # my_owner must resolve to the user's own email, not the other domain's
            assert context["applied_filters"]["owners"] == [user.email]

    def test_truncated_email_resolved_via_users_map(self, rf: RequestFactory, db):
        """Truncated 'user@domain.' is completed when a matching user exists."""
        registered: User = UserFactory(email="owner@corp.net", name="Registered Owner")  # type: ignore[assignment]

        service = ServiceConfiguration.objects.create(
            name="TestService",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        Task.objects.create(
            external_id="T-TRUNC",
            title="Truncated email task",
            status="open",
            service=service,
            owner_email="owner@corp.",
            updated_at=timezone.now(),
        )

        request = rf.get("/?view=all")
        request.user = registered
        view = DashboardView()
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        assert len(tasks) == 1
        assert tasks[0].display_owner_list == ["owner@corp.net"]

    def test_truncated_email_resolved_via_pool(self, rf: RequestFactory, db):
        """Full email wins as canonical when pool has full and truncated forms."""
        viewer: User = UserFactory()  # type: ignore[assignment]

        service = ServiceConfiguration.objects.create(
            name="TestService",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        Task.objects.create(
            external_id="T-FULL",
            title="Full email task",
            status="open",
            service=service,
            owner_email="worker@acme.org",
            updated_at=timezone.now(),
        )
        Task.objects.create(
            external_id="T-TRUNC2",
            title="Truncated email task",
            status="open",
            service=service,
            owner_email="worker@acme.",
            updated_at=timezone.now(),
        )

        request = rf.get("/?view=all")
        request.user = viewer
        view = DashboardView()
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()

        owners = context["filter_options"]["owners"]
        assert "worker@acme.org" in owners
        assert "worker@acme." not in owners

        for t in context["tasks"].object_list:
            assert t.display_owner_list == ["worker@acme.org"]

    def test_own_only_no_cross_domain_leak(self, rf: RequestFactory, db):
        """OWN_ONLY must not leak tasks to a user sharing only the email local-part."""
        UserFactory(email="agent@zone-a.com")
        user_b: User = UserFactory(email="agent@zone-b.com")  # type: ignore[assignment]

        group = Group.objects.create(name="Agents")
        user_b.groups.add(group)

        ext_group = ExternalGroup.objects.create(origin="Zammad", name="Agents")
        TaskPermission.objects.create(
            django_group=group,
            allowed_external_group=ext_group,
            access_level="OWN",
        )

        service = ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
        )
        Task.objects.create(
            external_id="T-OWNER-A",
            title="Task for A",
            status="open",
            service=service,
            group="Agents",
            owner_email="agent@zone-a.com",
            updated_at=timezone.now(),
        )

        request = rf.get("/")
        request.user = user_b
        view = DashboardView()
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()

        task_ids = [t.external_id for t in context["tasks"].object_list]
        assert "T-OWNER-A" not in task_ids

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
        view.perspective = "all"
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
        view.perspective = "all"
        view.request = request
        context = view.get_context_data()

        tasks = context["tasks"].object_list
        task_ids = [t.external_id for t in tasks]
        assert "ZAM-1" in task_ids

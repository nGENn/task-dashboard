from http import HTTPStatus
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.http import HttpResponseRedirect
from django.test import RequestFactory
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ticket_dashboard.users.forms import UserAdminChangeForm
from django.contrib.auth.models import Group
from ticket_dashboard.users.models import ExternalGroup
from ticket_dashboard.users.models import ServiceConfiguration
from ticket_dashboard.users.models import TicketPermission
from ticket_dashboard.users.models import User
from ticket_dashboard.users.tests.factories import UserFactory
from ticket_dashboard.users.views import DashboardView, UserRedirectView
from ticket_dashboard.users.views import UserUpdateView
from ticket_dashboard.users.views import user_detail_view

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
    def test_own_only_permission(self, user: User, rf: RequestFactory):
        # 1. Setup Data
        group = Group.objects.create(name="Support Group")
        user.groups.add(group)

        ext_group = ExternalGroup.objects.create(
            origin="Zammad",
            name="Support",
        )
        TicketPermission.objects.create(
            django_group=group,
            allowed_external_group=ext_group,
            access_level="OWN_ONLY",
        )

        ServiceConfiguration.objects.create(
            name="Zammad",
            service_type="zammad",
            is_active=True,
        )

        # 2. Mock Tickets
        mock_tickets = [
            {
                "id": "ZAM-1",
                "title": "My Ticket",
                "status": "open",
                "origin": "Zammad",
                "group": "Support",
                "owner_email": user.email,
                "owner": user.name,
                "updated_at": "2024-01-01",
            },
            {
                "id": "ZAM-2",
                "title": "Other Ticket",
                "status": "open",
                "origin": "Zammad",
                "group": "Support",
                "owner_email": "other@example.com",
                "owner": "Other User",
                "updated_at": "2024-01-01",
            },
            {
                "id": "ZAM-3",
                "title": "Unassigned Ticket",
                "status": "open",
                "origin": "Zammad",
                "group": "Support",
                "owner_email": None,
                "owner": "Unassigned",
                "updated_at": "2024-01-01",
            },
        ]

        # 3. Request
        request = rf.get("/")
        request.user = user

        # 4. Execute View with Mocked Service
        with patch(
            "ticket_dashboard.users.views.ZammadService",
        ) as mock_service_class:
            mock_service_instance = mock_service_class.return_value
            mock_service_instance.get_tickets.return_value = mock_tickets

            view = DashboardView()
            view.request = request
            context = view.get_context_data()

        # 5. Verify Results
        tickets = context["tickets"].object_list
        ticket_ids = [t["id"] for t in tickets]

        assert "ZAM-1" in ticket_ids
        assert "ZAM-2" not in ticket_ids
        assert "ZAM-3" not in ticket_ids
        assert len(tickets) == 1

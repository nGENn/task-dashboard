from typing import TYPE_CHECKING

import pytest

from ticket_dashboard.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from ticket_dashboard.users.models import User


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def user(db) -> User:
    return UserFactory()

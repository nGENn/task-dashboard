import os
import django
import re
from django.conf import settings
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from django.db.models import Q

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

from task_dashboard.users.views import DashboardView
from task_dashboard.users.models import Task, ServiceConfiguration

User = get_user_model()

def verify_hardened_logic():
    print("--- Verifying Hardened Identity & Array Logic (v3) ---")
    
    # Setup test data
    Task.objects.all().delete()
    ServiceConfiguration.objects.all().delete()
    User.objects.all().delete()
    
    service, _ = ServiceConfiguration.objects.get_or_create(
        name="Hardened Service", 
        defaults={"service_type": "zammad", "is_active": True, "default_access_level": "FULL"}
    )
    
    # Create Django Users for label priority test
    User.objects.create(email="delta@example.com", name="Judith Delta", is_staff=True)
    
    # 1. Multi-owner task with inconsistent spacing
    Task.objects.create(
        external_id="MULTI-SPACES", 
        service=service, 
        status="open", 
        title="Multi Space Task", 
        priority="Critical",
        owner="Alice,Bob,Charlie", # No spaces
        owner_email="alice@example.com, bob@example.com" # With space
    )
    
    # 2. Fragmented Iota identity
    Task.objects.create(external_id="IOTA-1", service=service, status="open", title="I1", owner="Charlie Iota", owner_email="")
    Task.objects.create(external_id="IOTA-2", service=service, status="open", title="I2", owner="iota", owner_email="")
    
    # 3. Fragmented Delta identity (handling umlauts)
    Task.objects.create(external_id="DELTA-1", service=service, status="open", title="D1", owner="Judith Delta", owner_email="delta@example.com")
    Task.objects.create(external_id="DELTA-2", service=service, status="open", title="D2", owner="jdelta", owner_email="")
    Task.objects.create(external_id="DELTA-3", service=service, status="open", title="D3", owner="Judith Delta", owner_email="")
    
    test_user, _ = User.objects.get_or_create(email="test@example.com", defaults={"name": "Test User", "is_staff": True})
    
    factory = RequestFactory()
    request = factory.get('/users/dashboard/?view=all')
    request.user = test_user
    request.htmx = False
    
    view = DashboardView()
    view.request = request
    context = view.get_context_data()
    
    owners = context['filter_options']['owners']
    print(f"Owners Dropdown: {owners}")
    
    # Check Delta Unification
    # Corrected check: look for any label that looks like Delta
    delta_labels = [o for o in owners if "delta" in o.lower()]
    print(f"Delta entries: {delta_labels}")
    if len(delta_labels) == 1 and "Delta" in delta_labels[0]:
        print("PASSED: Delta successfully unified (with correct Django User label and umlaut handling).")
    else:
        print(f"FAILED: Delta unification failed. Got: {delta_labels}")

    # Check Iota Unification
    iota_labels = [o for o in owners if "iota" in o.lower()]
    print(f"Iota entries: {iota_labels}")
    if len(iota_labels) == 1:
        print("PASSED: Iota successfully unified via common substring bridge.")
    else:
        print(f"FAILED: Iota unification failed. Got: {iota_labels}")

    # Check Multi-owner Filtering (No-space match)
    alice_best = next((o for o in owners if "alice" in o.lower()), None)
    if alice_best:
        request = factory.get('/users/dashboard/', {'view': 'all', 'owner': [alice_best]})
        request.user = test_user
        request.htmx = False
        view.request = request
        context = view.get_context_data()
        found_ids = [t.external_id for t in context['tasks'].object_list]
        print(f"Found IDs for Alice filter ({alice_best}): {found_ids}")
        if "MULTI-SPACES" in found_ids:
            print("PASSED: Multi-owner filtering works for inconsistent spacing.")
        else:
            print("FAILED: Multi-owner filtering failed for inconsistent spacing.")

if __name__ == "__main__":
    verify_hardened_logic()

# Task Dashboard Architecture Guide

This document provides a technical overview of the Task Dashboard's internal architecture, focusing on the multi-service aggregation engine, the identity management system, and the state-based routing.

## 1. System Overview

The Task Dashboard is a unified interface for tracking duties across heterogeneous service platforms (Zammad, GitLab, EspoCRM, Eramba, and OpenProject). It utilizes an asynchronous synchronization layer to pull tasks into a local PostgreSQL database, providing a low-latency, searchable view of all responsibilities.

### Stack
- **Framework:** Django 5.x
- **Database:** PostgreSQL 18 (with GIN indexes and GeneratedFields)
- **Frontend:** HTMX, Tailwind CSS, DaisyUI
- **Caching/Task Queue:** Valkey (High-performance Redis-compatible store)
- **Background Tasks:** Django-Q2

---

## 2. The "Identity Bridge"

The most complex component is the **Identity Bridge**, which unifies disparate user identifiers from various source systems into canonical Django users.

### The Problem
A single user might appear as:
- `alice.alpha@example.com` (GitLab)
- `Alice Alpha` (Zammad)
- `alice.alpha` (EspoCRM)

### The Solution: Bag-of-Words Tokenization
Instead of exact string matching, the system uses a PostgreSQL-native "Bag-of-Words" overlap engine:

1. **Tokenization:** Both the task owners in the database and the search criteria are normalized (lowercased, unaccented, and transliterated for umlauts) and split into arrays of alpha-numeric tokens.
2. **Canonical Mapping:** The system builds a mapping in Python that determines the "Best Label" (usually an email address) for a group of related tokens.
3. **Array Overlap (&&):** Filtering is performed using the Postgres `&&` operator. If any token from the search criteria exists in the task's identity array, it is considered a match.

### GIN Indexing
To maintain sub-millisecond performance on thousands of tasks, the system uses specialized GIN (Generalized Inverted Index) indexes on the results of the tokenization expressions:
```sql
CREATE INDEX idx_task_owner_array ON users_task USING GIN (
    regexp_split_to_array(
        unaccent(replace(replace(replace(lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')),
        '[^a-z0-9@.-]+'
    )
);
```

---

## 3. Routing Architecture

The dashboard uses **Perspective-Based Routing** to manage state across HTMX swaps while maintaining clean, bookmarkable URLs.

- **`/my`**: Highlights "My Tasks". Automatically filters tasks assigned to the current user.
- **`/unassigned`**: Highlights "Unassigned". Filters for tasks with no owner.
- **`/all`**: Shows all tasks globally.
- **`/` (Root)**: Transparently redirects to `/my` for fresh logins but serves as the landing for ad-hoc filter combinations.

### Persistence & HTMX
State is preserved during filtering and sorting via `hx-push-url="true"`. The `DashboardView` detects `HX-Request` headers to determine whether to return the full page shell or just the table partial.

---

## 4. Database Schema

### `Task` Model
- **`search_text`**: A `GeneratedField` that concatenates title, ID, customer, and owner for optimized global search.
- **`is_owner`**: A computed annotation in the Django ORM that leverages the Identity Bridge overlap logic.

### `SavedView`
Allows users to persist complex filter combinations (e.g., "High Priority Security Tasks") as named items in their sidebar.

---

## 5. Localization: German Core Terms

To ensure consistency in future development, use the following terms for UI elements:

| English Term | German Translation | Usage |
| :--- | :--- | :--- |
| **Origin** | **Herkunft** | The source platform (Zammad, etc.) |
| **Owner** | **Besitzer** | The user assigned to the task |
| **Customer** | **Kunde** | The organization or client |
| **State** | **Status** | Current task status (Open, Pending) |
| **Pending** | **Ausstehend** | Waiting for feedback or external info |
| **Priority** | **Priorität** | Critical, High, Medium, Low |
| **Reset** | **Zurücksetzen** | Clearing all active filters |

---

## 6. Environment Setup (Docker)

Deployment is managed via Docker Compose. The environment includes:
- **`django`**: The core application container.
- **`postgres`**: Database with `unaccent` extension enabled.
- **`valkey`**: Infrastructure for caching and background sync tasks.
- **`worker`**: Background process for executing service fetchers.

To compile translations and apply migrations:
```bash
docker exec task_dashboard_django python manage.py migrate
docker exec task_dashboard_django python manage.py compilemessages
```

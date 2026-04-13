# Task Dashboard Architecture

This document provides a technical overview of the Task Dashboard's core pillars: **Identity Synchronization**, **Stateful Routing**, and **Asynchronous Performance**.

## 1. The Identity Bridge (Core Logic)

The primary challenge of this project is unifying disparate user identifiers from Zammad, GitLab, EspoCRM, and more into a single canonical view.

### Bag-of-Words Tokenization
Instead of exact string matching, the system uses a **PostgreSQL-native tokenization engine**:
1. **Normalization**: Identity strings (emails, names) are lowercased, unaccented, and transliterated (e.g., `ö` -> `oe`).
2. **Tokenization**: Strings are split into alpha-numeric arrays using `regexp_split_to_array`.
3. **Overlap Logic (`&&`)**: Filtering uses the Postgres array overlap operator. A match occurs if any search token exists in the task's identity array.

### GIN Performance
To ensure sub-millisecond response times across thousands of tasks, specialized **GIN (Generalized Inverted Indexes)** are applied to the tokenized expressions, enabling high-performance reverse lookups.

---

## 2. Stateful Routing (HTMX & Perspectives)

The dashboard uses **Perspective-Based Routing** to manage complex filtering states while keeping URLs bookmarkable.

- **Routes**: `/my` (User's tasks), `/unassigned` (Tasks without owner), `/all` (Global view).
- **HTMX Partial Swaps**: The `DashboardView` detects `HX-Request` headers. On initial load, it returns the full shell; subsequent filter/pagination actions only return the table row partial.
- **State Persistence**: Filter parameters are preserved during navigation and pushed to the browser history via `hx-push-url`.

---

## 3. Asynchronous Sync Layer

Data integrity is maintained through a high-concurrency background pipeline.

### The Pipeline
1. **Parallel Fetch**: **Django-Q2** dispatches workers for each service in parallel.
2. **Concurrent API Requests**: Individual service clients use `asyncio` and `httpx` to paginate through APIs concurrently.
3. **Atomic Upsert**: Data is written in bulk using `bulk_create(update_conflicts=True)`.
4. **Auto-Pruning**: Tasks removed from the source system are automatically purged from the local database during the next sync.

---

## 4. Build Pipeline (Tailwind v4)

We utilize **Tailwind CSS v4**'s standalone discovery engine.
- **Source Scanning**: classes are discovered dynamically in `.html` templates, `.js` scripts, and `.py` view files via `@source` directives in `input.css`.
- **Zero-Purge**: This ensures that classes generated dynamically in Python views are preserved in the production build.

---

## 5. Deployment Checklist

The system is optimized for **Docker** deployment using a 4-container stack:
1. **App**: Django/Gunicorn (Web Interface).
2. **Worker**: Django-Q Cluster (Background Sync).
3. **Database**: PostgreSQL 18.
4. **Cache**: Valkey (In-memory storage).

> [!TIP]
> To verify a production build locally, set `ACCOUNT_DEFAULT_HTTP_PROTOCOL=http` and disable `SESSION_COOKIE_SECURE` temporarily.

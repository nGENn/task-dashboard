# SPEC.md — Kimai Integration

## §G
Unified Kimai integration: per-user reminder banner on dashboard if behind on time tracking; service tasks → Kimai activities sync; group-scoped activity visibility via Kimai teams.

---

## §C

- C1: follow BaseService pattern → KimaiService(BaseService); register in SERVICE_CLASSES & service_map
- C2: ServiceConfiguration gets service_type="kimai"; reuses api_url + api_token (EncryptedCharField); ! no new secret column
- C3: ∀ Kimai API calls async (httpx); auth = Bearer api_token
- C4: bg tasks via Django-Q2 async_task only; ⊥ celery
- C5: reminder state = Valkey only; ⊥ per-user DB rows
- C6: holidays → nager.at GET /api/v3/PublicHolidays/{year}/{country}; fail-open (cache last good; unavailable → 0 holidays)
- C7: email→kimai_user_id mapping via Valkey; key=kimai_uid:{email}; TTL=24h; miss → skip+log, ! no crash
- C8: working days per user from Kimai user.accountNumber field; format "Mo,Di,Mi,Do,Fr" (German abbrevs); empty/missing → skip reminder
- C9: German weekday map: Mo=0,Di=1,Mi=2,Do=3,Fr=4,Sa=5,So=6 (Python weekday()); invalid token → skip day
- C10: SSO sso-* filter enforced before Kimai team sync; non-sso-* groups → ⊥ Kimai team
- C11: ExternalGroup.origin = ServiceConfiguration.name (not service_type); team name = "{origin}::{external_group_name}"; unique
- C12: activity.comment = "{service_config_id}:{external_task_id}"; unique ∀ services
- C13: hide activity when source task status=closed; unhide if reopened; idempotent
- C14: team membership reconciled on SSO login + bg task; both idempotent
- C15: exempt_emails (KimaiSettings) → skip reminder + sync for that user

---

## §I

### Kimai API (base = ServiceConfiguration.api_url)
```
GET  /api/users                       → [{id, title(=email), accountNumber}]
GET  /api/timesheets?user={id}&size=1 → [{end}]
GET  /api/projects                    → [{id, name, comment, customer}]
POST /api/projects                    → {id}
PATCH /api/projects/{id}
GET  /api/activities?project={id}&visible=3 → [{id, name, comment, visible}]
POST /api/activities
PATCH /api/activities/{id}
GET  /api/customers                   → [{id, name}]
GET  /api/teams                       → [{id, name, users:[{id}], projects:[{id}]}]
POST /api/teams
PATCH /api/teams/{id}
```

### Holiday API
```
GET https://date.nager.at/api/v3/PublicHolidays/{year}/{countryCode}
→ [{date:"YYYY-MM-DD"}]
```

### New model: KimaiSettings (singleton, pk=1)
```python
grace_period_days  IntegerField(default=3)
holiday_country    CharField(max_length=5, default="DE")
exempt_emails      TextField(blank=True)   # newline-separated
sync_enabled       BooleanField(default=True)
team_sync_enabled  BooleanField(default=True)
reminder_enabled   BooleanField(default=True)
```

### Valkey cache keys
```
kimai_email_map              TTL=24h  → {email: kimai_user_id}
kimai_reminder:{user_pk}     TTL=1h   → {days_behind: int, ts: ISO}
kimai_holidays:{cc}:{year}   TTL=24h  → [date strings]
kimai_team_map               TTL=1h   → {django_group_name: kimai_team_id}
```

### Django-Q2 tasks (task_dashboard/kimai/tasks.py)
```
refresh_kimai_user_cache()
sync_kimai_teams()
sync_kimai_activities()
sync_kimai_activities_for_service(config_id)
run_reminder_evaluation()
```

### Context processor: kimai_reminder(request)
```
→ {"kimai_reminder": {"days_behind": int, "kimai_url": str} | None}
```
Reads Valkey only; ⊥ Kimai API call in request path.

### SSO hook (adapters.py SocialAccountAdapter._sync_groups)
After group sync → async_task("task_dashboard.kimai.tasks.sync_kimai_teams")

### New file layout
```
task_dashboard/kimai/__init__.py
task_dashboard/kimai/client.py      # KimaiClient async httpx
task_dashboard/kimai/service.py     # KimaiService(BaseService)
task_dashboard/kimai/tasks.py       # Django-Q2 fns
task_dashboard/kimai/reminder.py    # pure evaluator, no I/O
task_dashboard/kimai/holidays.py    # nager.at + Valkey cache
task_dashboard/kimai/models.py      # KimaiSettings singleton
task_dashboard/kimai/admin.py
task_dashboard/kimai/apps.py
task_dashboard/kimai/migrations/
```

---

## §V

- V1: KimaiService registered in SERVICE_CLASSES + service_map; health check pings /api/ping or /api/version
- V2: api_token stored only via EncryptedCharField; ⊥ plaintext in DB | logs
- V3: activity.comment = "{service_config_id}:{external_task_id}"; ∀ sync ops parse this; ⊥ collision across services
- V4: reminder evaluator pure fn (days_behind, working_days_set, holidays_set, last_entry_dt) → int; ⊥ I/O; unit-testable
- V5: banner context processor reads Valkey only; ⊥ Kimai API call in request path
- V6: kimai_reminder:{user_pk} TTL≥1h; populated by bg task, ⊥ by request
- V7: ∀ user where accountNumber empty | null → skip reminder; ⊥ error raised
- V8: ∀ user.email ∉ kimai_email_map → skip activity sync + reminder; log warning; ⊥ exception propagated
- V9: exempt_emails check before any Kimai call for that user
- V10: sso-* filter enforced before Kimai team sync; non-sso-* → ⊥ Kimai team created
- V11: Kimai team name = "{ExternalGroup.origin}::{ExternalGroup.name}"; unique
- V12: activity hide/show idempotent; PATCH only if visible state changes
- V13: team reconciler idempotent; PATCH team only if membership diff non-empty
- V14: holiday fetch fail-open; nager.at error → return cached | []; log warning
- V15: German weekday abbrev map hardcoded Mo→0,Di→1,Mi→2,Do→3,Fr→4,Sa→5,So→6; invalid token → log + skip
- V16: kimai_email_map refreshed by scheduled bg task + on-demand; TTL=24h
- V17: sync_kimai_teams triggered on SSO login (adapter) & bg schedule; same idempotent fn both paths
- V18: KimaiService.get_tasks_async → []; check_health pings /api/ping
- V19: migration adds "kimai" to ServiceConfiguration.SERVICE_TYPES; ⊥ data migration
- V20: KimaiSettings singleton pk=1; .load() classmethod (mirrors GlobalSetting pattern)

---

## §T

| id  | status | task                                                                                        | cites                   |
|-----|--------|---------------------------------------------------------------------------------------------|-------------------------|
| T01 | x      | migration: add "kimai" to ServiceConfiguration.SERVICE_TYPES                               | C2,V19                  |
| T02 | x      | KimaiSettings model + migration                                                             | §I,V20                  |
| T03 | x      | KimaiClient: async httpx; Bearer auth; users/timesheets/projects/activities/teams/customers | §I,V2                   |
| T04 | x      | holidays.py: nager.at fetcher; Valkey cache; fail-open                                     | C6,V14                  |
| T05 | x      | reminder.py: pure evaluator; German weekday parser; business-days-behind calc              | C8,C9,V4,V7,V15         |
| T06 | x      | refresh_kimai_user_cache task: GET /api/users → kimai_email_map Valkey                     | §I,V8,V16               |
| T07 | x      | KimaiService(BaseService): get_tasks_async→[], check_health; register                      | C1,V1,V18               |
| T08 | x      | sync_kimai_teams task: ExternalGroup→Kimai teams; sso-* filter; idempotent                 | C10,C11,V10,V11,V13,V17 |
| T09 | x      | extend SocialAccountAdapter._sync_groups: dispatch sync_kimai_teams on login               | §I,V17                  |
| T10 | x      | sync_kimai_activities_for_service: upsert activities, hide closed, unhide reopened         | C12,C13,V3,V12          |
| T11 | x      | sync_kimai_activities fan-out: iterate active non-kimai configs → T10                      | C4                      |
| T12 | x      | run_reminder_evaluation task: per-user days_behind → kimai_reminder:{pk} cache            | V5,V6,V9                |
| T13 | x      | kimai_reminder context processor: read cache → inject banner data; register in settings    | §I,V5                   |
| T14 | x      | template banner partial: show if days_behind > 0; link to Kimai URL                        | §I                      |
| T15 | x      | Django-Q2 schedules: sync_kimai_activities, sync_kimai_teams, refresh cache, reminder eval | C4                      |
| T16 | x      | admin: KimaiSettings singleton; ExternalGroup→team mapping visibility                      | §I,V20                  |
| T17 | x      | tests: reminder evaluator (working days, holidays, grace period, exemptions)               | V4                      |
| T18 | x      | tests: activity sync (upsert, hide, unhide, comment format)                                | V3,V12                  |
| T19 | x      | tests: team sync (sso filter, idempotency, origin::name format)                            | V10,V11,V13             |
| T20 | x      | tests: context processor + banner (cache hit/miss, exempt, empty accountNumber)            | V5,V7,V9                |

---

## §B

| id | date | cause | fix |
|----|------|-------|-----|

# Database Migrations

This project uses [Alembic](https://alembic.sqlalchemy.org/) for database migrations.

## Generating Migrations
Whenever you change `models.py`, generate a new migration script:
```bash
alembic revision --autogenerate -m "description of changes"
```

## Applying Migrations
To upgrade the database to the latest schema:
```bash
alembic upgrade head
```

## Guardrail Note for SQLite & Alembic
لو Alembic فشل بـ SQLite بسبب `batch_alter_table`، الحل هو `render_as_batch=True` بإعدادات `env.py` — مو تعديل يدوي مباشر على القاعدة. لو حصل تعديل يدوي لأي سبب طارئ، استخدم `alembic stamp head` فوراً بعده لتصفير التعارض.

# Known Technical Debt

## Flutter — Visit detail navigation passes appointmentId instead of visitId
- **File:** `pdos_app/lib/features/rep/screens/my_day_tab.dart:207`
- **Issue:** When tapping a completed appointment in MyDay tab, the code navigates with `appt.id` (which is the appointment ID) instead of the actual visit ID.
- **Current workaround:** `visit_detail_screen.dart:visitDetailProvider` has a fallback lookup by `appointmentId` that masks the root cause.
- **Proper fix needed:** Add `visitId` field to `AppointmentModel`, populate it when `startVisit()` creates a visit, and navigate using `appt.visitId`.

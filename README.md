# careslot

AI-powered clinic appointment management system built with FastAPI, SQLite, and real-time updates.

## Development seed data

Populate the local SQLite database (`careslot.db`) with fictional demo data for
all CareSlot tables:

```
python -m app.db.seed
```

The seed is idempotent: running it repeatedly will not duplicate records or
overwrite existing user passwords. Seeded accounts are identified by the demo
email addresses below.

### Demo login credentials

> **Local development only.** These accounts and passwords are fictional and
> must never be used in production or with real patient data.

| Role         | Email                   | Password        |
| ------------ | ----------------------- | --------------- |
| admin        | admin@careslot.dev      | CareslotDev123! |
| receptionist | reception@careslot.dev  | CareslotDev123! |
| doctor       | dr.smith@careslot.dev   | CareslotDev123! |
| doctor       | dr.lee@careslot.dev     | CareslotDev123! |

## Tests

```
python -m pytest
```

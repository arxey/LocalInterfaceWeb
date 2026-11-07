# Portable photo service implementation plan

**Goal:** publish a reproducible PostgreSQL photo service after security and correctness review.

**Architecture:** React static build and media behind Nginx, Django/Gunicorn API, PostgreSQL persistent storage. Compose startup creates secrets, migrates, bootstraps admin and demo image. pgAdmin optional.

**Spec:** ../specs/2026-11-07-portable-security.md

## Tasks

- [x] Backend: reproduce anonymous-write, upload/transaction and Unicode defects; add regression tests; implement session/CSRF, bounds, safe processing and bootstrap; verify PostgreSQL and isolated tests.
- [x] Frontend: regression tests for stale data, session expiry and dialog behavior; integrate login/CSRF; verify unit tests, production build and dependency audit.
- [x] Runtime: Windows/POSIX launchers, generated secrets, Compose healthy dependencies, persistent volumes, loopback networking, Nginx headers and upload limits; CI clean-start CRUD and persistence smoke.
- [x] Review: inspect all source and configuration; dependency/static/secret checks; independent final review and fix findings; capture verification evidence.
- [x] Documentation: requirements matrix, schema/API, quick start, pgAdmin/development/backups, security review and refreshed course report.
- [x] Publish: check staged files contain no secrets or user data, commit/push, inspect remote CI and fix failures, provide repository and release archive.

## Review focus

Unauthenticated cross-site mutations; malformed/large/animated images; failed database commit during file replacement; title Unicode expansion; stale or aborted frontend requests; first boot without preinstalled Python/Node/PostgreSQL; restart without data loss.

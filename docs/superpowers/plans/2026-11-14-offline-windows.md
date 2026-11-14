# Offline Windows distribution

User confirmed fully offline Windows10/11x64 launch and GitHub update. No Docker, WSL, virtualization, admin installation, services or global PATH changes. PostgreSQL is retained to satisfy the course specification.

- [x] Bundle pinned Python3.14.10 embeddable, PostgreSQL18.7 bin/lib/share and matching Microsoft CRT app-local; retain licenses and SHA256 provenance.
- [x] Bundle compiled React assets and Python wheels at build time; no runtime network downloads or package installation.
- [x] Native Waitress single-origin HTTP with explicit safe frontend/static/media routes, security headers and native login throttling. Regression tests for traversal/junctions, secrets, missing assets and CSRF.
- [x] Relative-path launcher, SCRAM database/app credentials, persisted data/config, no registry/services, occupied ports, safe stop, child process ownership and crash cleanup. Tests for relocation, lock and persistence.
- [x] Real ordinary-user clean-path startup, HTTP CRUD and browser smoke, stop/restart/relocation persistence, module-path check that CRT loads app-local.
- [x] Windows CI builds and tests complete bundle; publish source and downloadable ZIP release with hash. Update docs and leave Docker as optional alternative.

Published v1.1.5-portable; Windows release run43573370152 and main run43573370107 passed. See docs/portable-review.md for evidence.

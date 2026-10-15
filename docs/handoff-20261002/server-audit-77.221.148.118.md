# Server inventory · 77.221.148.118

Read-only inspection on 2026-10-01. No server files or services were changed.

- OS: Ubuntu 22.04; root filesystem 119 GB, 4.1 GB used; RAM 8 GB.
- Existing directories: `/opt/aba` (about 508 KB), `/opt/checktopdd` (about 52 KB), `/var/www`.
- Active services include `nginx` and `checktopdd-mock.service`; ports 80, 443, 8080, and 8085 are in use.
- Existing Nginx site: `/etc/nginx/sites-enabled/aba-bank.cyou`.
- `/opt/aba/data/victims.db` and five matching backups exist. Their contents were not opened or downloaded.
- `/opt/aba/data/.secret` exists. Its contents were not opened or downloaded.

This document is an inventory, not a data backup. A full local export would include potentially sensitive third-party data. No deletion or deployment was performed during the inventory.

# Self-hosted n8n for FACAI (private operator instance)

This runs the official n8n Community Edition image on the FACAI VPS, without an
n8n Cloud subscription. The n8n editor and webhook listener bind to `127.0.0.1`
only. The existing FACAI service can relay events to it locally; administer the
editor through an SSH tunnel. Persistent workflows and credentials live in the
Docker volume `facai_n8n_data`.

## License and product boundary

n8n is source-available under its Sustainable Use License, not a conventional
OSI open-source license. Its main-branch core license permits internal business
use, subject to the license terms; `.ee` source files require an Enterprise
license. Do not vendor the full n8n source tree into FACAI or expose n8n itself
as a workflow-builder service to FACAI customers without written confirmation
from n8n that the planned commercial use is covered. FACAI's native mission and
automation UI remains the customer-facing product. This private instance is for
the FACAI operator's internal integrations.

References: [n8n license](https://github.com/n8n-io/n8n/blob/master/LICENSE.md),
[n8n repository](https://github.com/n8n-io/n8n).

## Install on the VPS

Requirements: Docker Engine with the Compose plugin. From the deployed FACAI
checkout on the VPS:

```sh
cd /opt/facai-cloud/deploy/n8n
umask 077
printf 'N8N_ENCRYPTION_KEY=%s\nGENERIC_TIMEZONE=Asia/Shanghai\n' "$(openssl rand -hex 32)" > .env
docker compose up -d
docker compose ps
```

Open a tunnel from the admin computer:

```sh
ssh -N -L 5678:127.0.0.1:5678 root@77.221.148.118
```

Then open `http://127.0.0.1:5678` locally and create the n8n owner account.
Never publish port 5678 directly to the internet. Keep `.env` out of Git and
back up both the `.env` encryption key and the Docker volume; without the key,
stored n8n credentials cannot be decrypted.

## Connect a workflow to FACAI

1. In the n8n editor, create the FACAI event receiver workflow described in
   [`../../docs/N8N.md`](../../docs/N8N.md) and activate it.
2. Set `N8N_WEBHOOK_URL` in `/etc/facai-cloud/providers/n8n.env` to that
   workflow's **production** webhook URL. Since the relay is local, use
   `http://127.0.0.1:5678/webhook/<your-path>`.
3. Put a newly generated random value in `N8N_SIGNING_SECRET` in the same FACAI
   provider file and in the n8n service environment. Recreate the n8n container
   after updating its `.env` (`docker compose up -d`).
4. Restart `facai-cloud.service`, check the FACAI n8n panel says `CONFIGURED`,
   and use its relay test. The secret and webhook path must not be committed or
   pasted into logs.

The browser-based Cloud trial is not required for this self-hosted setup. The
image tag is pinned to n8n 2.41.5; upgrade deliberately after reviewing release
notes and taking a backup.

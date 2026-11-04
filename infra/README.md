# infra

| File | What it is |
|---|---|
| `docker-compose.yml` | The local and server stack: Postgres 16, Mailpit, ingest and Metabase. There's also a fake feed poller (profile `feeds`) and a tools runner (profile `tools`). |
| `Dockerfile` | One image for ingest and every Python tool. |
| `systemd/` | Service and timer templates for health (15 min), nightly restart (1-min tick) and waves (5 min). Render and install them with `systemd/install.sh`. |

## Host setup notes (server not provisioned yet)

Target: Hetzner dedicated server, Ubuntu 24.04, KVM capable. Sizing is still open, and
`fleet/fleet.yaml` holds the RAM/CPU budget.

1. **Kernel.** ReDroid needs binder:
   ```bash
   sudo apt install linux-modules-extra-$(uname -r)
   sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
   ```
   Persist it in `/etc/modules-load.d/redroid.conf`. Ashmem isn't needed on Android 12+.
   Validate the Android 16 image's kernel requirements before ordering the box.
2. **Docker.** Install Docker Engine with compose v2. Keep `/var/lib/docker` and
   `REDROID_DATA_ROOT` on NVMe; 40 instances at about 6–10 GB of `/data` each adds up.
3. **Image.** `REDROID_IMAGE` is a custom Android 16 + GApps build. It's out of scope until legal
   sign-off, as is any Google account or Play Store sign-in.
4. **VPN.** Put one provider env file per market in `/etc/study/vpn/vpn-<market>.env`
   (root-only, never in git). Read `fleet/vpn/README.md`; the routing still needs validating there.
5. **Fleet.** `make fleet` writes `fleet/out/fleet.sh`. Review it, then run it on the server.
6. **Python for the timers.** Run this in the repo checkout:
   ```bash
   uv venv -p 3.12 .venv
   uv pip install -p .venv -r requirements.txt
   ```
   Then run `sudo infra/systemd/install.sh /opt/scores_research /etc/study/study.env study`.
7. **Backups.** Take nightly `pg_dump` of `study`, and `python -m ops.snapshot take <id> --label onboarded`
   after each onboarding.

## Behind a TLS-intercepting proxy

If `docker build` fails on pip with certificate errors, copy the proxy's CA into
`infra/.extra-ca.pem` (gitignored) and set `EXTRA_CA_FILE=./.extra-ca.pem` in `.env`. It's
mounted only as a build secret and never stored in the image.

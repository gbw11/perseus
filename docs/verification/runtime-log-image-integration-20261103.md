# dom0 runtime log image integration — 2026-11-03

Base: `origin/platform` at `eaac929`. Work branch:
`feat/platform/S15P31E110-251-runtime-log-image`.
No board, VM, external server or Jira changes were made for this work.

## Problem and resulting behavior

The existing image recipe installs the runtime-log Python modules, but did not
install/enable a collector unit or give Manager a runtime-log DB path. The observed
external log API consequently returned 503. Existing standalone deployment
templates were not an image installation hook.

The new Manager bbappend installs/enables a single journal collector, a shared
private DB-path environment file, and a vendor Manager service drop-in. The base
recipe, Manager/Recovery unit files, token bootstrap, image package list and
application modules are unchanged. The existing metadata assertion is extended to
include the newly installed collector and its selected source files/dependencies.

Fresh image boot is configured to collect the seven explicit dom0 system units
in the installed collector ExecStart. The collector does not start those units.
Both collector and existing Manager read
`/etc/xenova/runtime-logs/collector.env` and use
`/var/lib/xenova-runtime-logs/logs.sqlite3`. The directory/environment/DB permissions
and systemd StateDirectory are tested. The drop-in adds only DB configuration and
Wants/After ordering, not Requires/BindsTo/PartOf failure propagation.

No new HTTP listener, port, MQTT subscription, VM polling, PTY console, keyboard
capture, credentials or source UUID configuration is introduced. Ingest/shipper
services are not installed/enabled by this append; mobile access port 8084 is not
claimed. The collector reuses existing retention, checkpoint and redaction logic,
with low CPU/I/O scheduling priority. Actual ARM CPU/RAM/disk performance is not
claimed from local tests.

## Local validation

All tests used the existing WSL Python 3.14 environment, with no package installation.

- **16 passed:** new image tests (3) plus existing runtime-log tests (13).
  The actual append install shell body runs against an isolated temporary root;
  it is not a substitute for evaluating BitBake metadata.
- Installed collector plus the original Manager unit and installed drop-in pass
  the actual `systemd-analyze verify --root=...` loader. Services are not started
  on the host by that verification.
- An actual collector subprocess consumes a synthetic journalctl pipe, writes
  SQLite, and is queried through the existing authenticated FastAPI app. Verified:
  unconfigured 503 without read-created DB; unauthenticated 401; allowed-source
  filtering; secret redaction; liveness; graceful stop; API access after collector
  stop; cursor reuse, duplicate suppression and stable DB epoch across restart.
- **30 passed, 12 subtests passed:** existing default-deployment, deployment-payload,
  image-rebuild and Gateway-packaging tests.
- One existing FastAPI TestClient deprecation warning; no failing tests.

Package naming was checked against the project's locked official OE commit
[`0f03c732...` systemd 259.5 recipe](https://github.com/openembedded/openembedded-core/blob/0f03c732a0a2d0506b41b1abe60b3f368b2300dc/meta/recipes-core/systemd/systemd_259.5.bb)
and its
[Python manifest](https://github.com/openembedded/openembedded-core/blob/0f03c732a0a2d0506b41b1abe60b3f368b2300dc/meta/recipes-devtools/python/python3/python3-manifest.json).
The added runtime dependency is `systemd`, not a nonexistent
`systemd-journalctl` split package; selectors/subprocess are in Python core and
sqlite3 is already explicit in the base recipe.

## Remaining deployment verification / handoff

This workstation has no configured BitBake/layer workspace. Full `bitbake -e`,
rootfs/package QA, ARM image compilation and real boot were **not run**. Image
build ownership and versions in layers.lock are unchanged.

In the existing configured build workspace:

1. Apply this branch's changes and confirm the bbappend with
   `bitbake-layers show-appends` and `bitbake -e xenova-dom0-manager`.
2. Run `bash platform/scripts/check-xenova-platform.sh --metadata` for the updated
   real metadata/source-selection assertion, then build the normal
   `xenova-xen-bringup-image` target. Confirm collector enablement and all installed
   paths in the rootfs/package manifest; do not inject files into WIC manually.
3. When the board is available after image deployment, verify collector active,
   Manager drop-in loaded, journalctl/sqlite3 present and both services use the same
   DB. Use the existing protected authentication mechanism to check log API and
   the external web's dom0 source records. Do not publish the service token in commands.
4. Stopping only the collector must leave Manager/VM/HDMI running; reopening the
   web must resume queries without duplicates. Service liveness is not proof that
   each collected service, or any drive control function, is healthy.

Guest/Jetson/Zephyr logs and systemd user-unit/Docker/other SSH-session output
still need their real producer/journal integration. This change cannot recover
already vacuumed journal data, and does not claim all project logs are available.
It also does not mark Jira 251 complete or change its remaining console checks.

Operator documentation is installed as
`/usr/share/xenova/runtime-logs/runtime-logs-image.README.md`.

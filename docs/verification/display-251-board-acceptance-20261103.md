# 251 external web console acceptance — 2026-11-03

Status: partially verified. Zephyr connection/lifecycle checks passed; fresh Zephyr output and Debian browser regression remain unverified. Jira completion is not claimed.

## Baseline

- Source base: platform `f97e7c8`.
- External Spring/admin-console/vm-screens images: `19`.
- External Spring container -> authenticated board `/api/v1/vms`: HTTP 200, three VMs.
- Authenticated external web shows Zephyr drive A, Zephyr drive B and Debian Interaction running.
- Board Manager active; no existing `xl console` processes or console lock files observed.
- Installed `/usr/lib/python3.14/site-packages/dom0_manager/api/console.py` matches the source base after newline normalization: SHA-256 `f154ebd80b993131b5d0575c7bde595838b50fc318b29c20f59817719e945fb2`.
- Installed code includes the bounded busy recheck. No source patch is needed for that fix.
- `XENOVA_CONSOLE_ENABLED` is absent from the running Manager environment. The default is disabled.
- First external VM1 connection attempt: connecting -> connection error. No guest input sent.

## Separate runtime-log issue

`/api/v1/logs?limit=1` and `/api/v1/logs/sources` return HTTP 503 because the runtime log collector is not configured/started. This is separate from the terminal WebSocket. Restoring the terminal does not prove execution-log collection works.

## Approved board change

The user explicitly approved persistent console enablement and a Manager API restart. Added only `/etc/systemd/system/xenova-dom0-manager.service.d/90-console-enabled.conf`:

```ini
[Service]
Environment=XENOVA_CONSOLE_ENABLED=1
```

Ran `systemctl daemon-reload` and `systemctl restart xenova-dom0-manager.service`. Authenticated VM inventory returned HTTP 200 after restart. Xen domain IDs remained 0, 1, 3 and 6. Existing `XENOVA_*` Manager values were compared in memory and retained, excluding the intentionally added console flag. Mosquitto/Tailscale MainPIDs and installed console source hash were unchanged. No VM command, firmware change, Spring DB write or network setting change was made.

The added setting persists across reboot; this session did not test another reboot. It is a board-local override, not an image recipe change. Reimaging can remove it. Rollback, if separately requested, is removal of this exact override followed by daemon-reload and a Manager API restart; no rollback was requested/performed after successful application.

## External browser checks

The user logged into the external management console. All actions below used the existing UI and existing Spring -> dom0 WebSocket route. No Zephyr terminal input was sent.

| VM | UUID | Current Xen domain | First connection | Disconnect cleanup | Reconnect |
| --- | --- | --- | --- | --- | --- |
| Zephyr drive A | `124d8a66-0527-4501-98c5-5793fbbf1cbb` | 6 | Connected; xenconsole PID 65775 | No console process; no flock holder | Connected; new xenconsole PID 67323 |
| Zephyr drive B | `ea476628-620d-4f43-a3ab-4390dab5e154` | 3 | Connected; xenconsole PID 67594 | No console process; no flock holder | Connected; new xenconsole PID 68573 |

Each live console held only its UUID lock under `/run/xenova/console-locks`, owned by the Manager. Switching from A to B left A unlocked. Final disconnect left no xenconsole process and no holder for either lock. Empty lock files persist intentionally; file existence alone is not an occupied session.

Both Zephyr terminals remained blank while connected. This confirms authenticated connection establishment, UUID-specific Xen attachment and cleanup/reconnect, **not** successful delivery of new guest text. No synthetic guest output was injected, no input was sent and no VM was restarted to manufacture output.

## Debian boundary and remaining acceptance

- Debian is reachable in inventory and shown in the external UI. Existing strict, noninteractive guest SSH could not verify hvc0 login-wait because the current guest key was not trusted; no known_hosts entry was changed.
- The user confirmed Debian is currently logged in and being used. Its browser console was **not connected**: the UI sends one automatic Enter for Linux guests, which could affect an existing shell.
- After that work is finished and the user confirms serial-console login-wait, verify Debian new output, disconnect, cleanup and reconnect.
- Confirm new Zephyr output when its real producer emits it; do not treat silence as a transport failure or a successful text-delivery check.
- This session did not rerun missing/unauthenticated/old-incarnation rejection tests, nor a live external-network outage with HDMI running. Display-feed/HDMI services were inactive at baseline and were not started for console testing. Previous evidence must be assessed separately before checking all Jira acceptance boxes.
- Jira status was not changed. Repository changes for this session consist only of this verification record; no application or teammate source files were edited.

## Board handoff

The user is handing the board to a teammate. Both test WebSocket connections were disconnected, no test Xen console process/lock holder remained, and the inspection SSH sessions were closed. No additional board access is required to commit/push this record. The approved persistent console enablement remains in place; Debian's existing work session was left untouched.

No tokens, passwords, private key material or terminal payloads are recorded here.

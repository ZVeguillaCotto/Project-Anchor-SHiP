# Security, PII & Vulnerability Audit — SHiP Anchor v0.1

**When:** v0.1.8 (covers the full v0.1.0 → v0.1.7 history)
**Scope:** every tracked file in the repository.

## What this document is (ELI5)

Before anyone installs this into a real home, we went through the entire code
base with a security checklist. This page lists what we looked at, what we
found, and how each finding was fixed or why it is safe. The headline: **no
secrets, no personal data, and no network endpoints live in the repository**,
and the two places where a hostile value *could* have reached a real device
command are now blocked by strict validators.

## 1. What was scanned

Every tracked file was reviewed, grouped by risk class:

| Area | Files reviewed |
|---|---|
| C core | `src/ship_core/ship_protocol.c`, `ship_protocol.h`, `ship_radio_hal.c` |
| Python integration | `custom_components/ship_anchor/*.py` and all `protocols/*.py` handlers |
| ESPHome component | `esphome/components/ship_fastpath/*` incl. the example YAML |
| MCP server | `mcp/ship_anchor_mcp.py` |
| Tests & simulations | `sim/test_ship_core.c`, `sim/run_anchor_benchmarks.py`, `sim/check_ship_fastpath_compile.py` |
| Repo metadata & docs | `manifest.json`, `hacs.json`, `strings.json`, `*.md`, `.gitignore` |
| Accidental commits | `.history/` backup files and any compiled `.pyc` |

## 2. Findings and resolutions

### 2.1 No secrets or credentials are stored — PASS

- No API keys, tokens, private keys, passwords, or connection strings in any
  tracked file. (The only passwords in the repo are the ESPHome *example*
  Wi-Fi/OTA placeholders, see 2.2.)
- The MCP server is **stdio-only** — it listens on a pipe to the local editor
  process, never on a network port.

### 2.2 No network endpoints or PII — PASS

- No URLs, domains, or phone/home network addresses in any executable code.
- All IP addresses that appear (`192.168.1.x`, `192.0.2.x`) are **documentation
  examples or RFC 5737 TEST-NET fixtures** inside tests and sample YAML. They
  are not dialled from the compiler — the compiler only uses values the user
  types into their own Home Assistant configuration.
- No user names, e-mail addresses, or other personal data. The `deadbeef…`
  string is a **mock test vector** for the frame-signature tests, not a real key.
- The accidentally-committed `.history/` backup files were checked and contain
  only documentation text (the same example IPs), not secrets. Compiled
  `__pycache__`/`.pyc` files are not tracked and are git-ignored.

### 2.3 Command-injection hardening — FIXED in v0.1.8

**Risk:** The Tasmota and Shelly protocol handlers build device commands as
*strings* (a `topic`/`command` plus a `value`). If a user-supplied field such
as `group_name` or `target_command` contained characters like `;` or `"`, it
could be used to smuggle extra instructions into the command that is sent to a
live device.

**Fix:** Both handlers now validate their string fields against a strict
allow-list before any payload is built:

- `group_name` (Tasmota): `^[A-Za-z0-9._-]{1,64}$` — semicolons, quotes,
  spaces and shell metacharacters are rejected.
- `target_command` (Shelly): `^[A-Za-z0-9_-]{1,32}$` — no quotes or statement
  separators.
- `script_name` (Shelly) and `friendly_name` (Z2M): `^[A-Za-z0-9_-]{1,64}$`.
- Every IPv4 address is validated as a well-formed dotted quad with each octet
  0–255 before it is embedded in a URL or MQTT topic.

The protocol dispatcher now also **catches** `ValueError`/`TypeError` from a
handler and records it as a per-binding error instead of crashing the whole
compile — so one bad binding reports a clean message while the others proceed.

### 2.4 Radio / C core — PASS

- The frame encoder always enforces the SipHash tag and the anti-replay
  counter on decode; there is no "auth-off" path.
- All buffer copies in `ship_protocol.c` and `ship_radio_hal.c` are bounded
  `memcpy`/`memset` against fixed-size arrays — no unbounded `strcpy`/`sprintf`
  on user input, no `gets`.
- Radio transmit paths follow the documented safe HAL patterns (OpenThread
  pcap-callback RX; SoC `transmit` dispatch), and never call the forbidden
  `otLinkRaw*` with IPv6 enabled.

### 2.5 Subprocess / shell safety — PASS

- No `os.system`, no `shell=True`, no dynamic `eval`/`exec` in the
  integration, MCP server, or simulation scripts.
- The MCP server enforces `dry_run` semantics in its tools so a mis-configured
  caller cannot accidentally actuate devices.

### 2.6 Erase robustness (v0.1.9) — FIXED

**Risk:** `ship_anchor.erase_all` aborted the moment any one binding could
not be undone (unsupported protocol, offline device, or malformed record),
leaving `.storage/ship_anchor.bindings` untouched and re-raising into the
service handler — surfacing as a long traceback (perceived as a recursion
stack overflow) instead of a clean result.

**Fix:** `async_erase_all()` now treats the device-side undo as best-effort
per binding (failures logged as warnings) and *always* clears the binding
list, saves the store, and logs a clean success warning. The service
handler no longer re-raises inside its `except` block (the classic
RecursionError amplifier); it logs full details and stops. `Drill 2` now
asserts the new clean-completion contract.

## 3. Verification evidence

| Check | Command | Result |
|---|---|---|
| C protocol suite | `gcc … sim/test_ship_core.c … && /tmp/test_ship_core` | **11/11 pass** |
| Six disaster drills | `python3 sim/run_anchor_benchmarks.py` | **ALL 6/6 pass** |
| Injection guards | ad-hoc test: `group_name="grp;Reboot"`, `target_command='on";x()'` | **both rejected** with clean per-binding errors |
| Compile/link seam | `sim/check_ship_fastpath_compile.py` (run inside Drill 6) | pass |
| MCP server | all five tools, run inside Drill 6 | pass |

## 4. Honest caveats

- The Python handlers are exercised with mocked Home Assistant objects (the
  integration can only run inside a real HA instance), so the validator paths
  are verified by unit-level tests and the drills, not a live UI session.
- "No secrets in the repo" says nothing about the *user's* Home Assistant
  config — users should still use strong, unique passwords on their own
  devices and rotate any key they ever shared.
- The 16-bit SipHash tag is a deliberate space trade-off for 802.15.4, not a
  general-purpose MAC; its security depends on the 16-byte secret being kept
  confidential and unique per deployment.

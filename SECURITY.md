# Security Policy — SHiP Anchor (Project Anchor)

## LAYER 1 — The Kitchen-Table Explanation (ELI5)

SHiP Anchor programs *real devices in your home* — lights, valves, locks. That is
powerful, so it is also worth protecting. This page is your "front door to the
security team": if you find a way to break it, this tells you the **safe way to
tell us** — quietly, before a bad actor finds it first.

Two big ideas:

- **Report a hole, don't post it.** If you find a way to make SHiP Anchor do
  something dangerous, please *tell us privately* rather than opening a public
  issue with the exploit in it. That gives us time to fix it before anyone else
  can use it.
- **The full audit lives in [SECURITY_AUDIT.md](SECURITY_AUDIT.md).** That
  document is the "security diary" — every area we checked, what we found, and
  how each finding was fixed or why it is safe.

## LAYER 2 — Vulnerability Reporting

### Supported versions

We patch security issues on the **latest release** and, where the fix is cheap
and safe, on the immediately preceding minor line. Older, abandoned releases are
not patched — move to the latest.

### How to report a vulnerability

1. **Go private first.** Do **not** open a public GitHub issue, pull request, or
    discussion describing a working exploit.
2. **Use GitHub Security Advisories.** On this repository, open the **Security**
    tab and click **Report a vulnerability**. This notifies the maintainer
    without exposing the details to the public.
3. **What to include:** the affected version (e.g. `v0.1.24`), the steps to
    reach the problem, and — if you have it — a fix or a pointer to one.
4. **What to expect:** an acknowledgment within a few days, a fix as soon as we
    can verify it, and a credit in the release notes unless you ask for anonymity.

If GitHub Security Advisories is not available for this repository, open a
**private** issue titled `[SECURITY] ...` and the maintainer will respond. As a
last resort, reach the maintainer through the project's GitHub profile
(`ZVeguillaCotto`), keeping any exploit details out of public view.

### Scope

We take reports on the following **in scope**:

- Authentication bypass, a broken or forgeable SipHash-2-4 tag, or a bypass of
  the 16-bit anti-replay counter.
- A path where a user-controlled value (for example `group_name`,
  `target_command`, an IP address, or a script name) can reach a live device or
  a shell — the injection classes already guarded by the strict allow-lists
  described in [SECURITY_AUDIT.md](SECURITY_AUDIT.md).
- A memory-safety bug in the C core (`src/ship_core/ship_protocol.c`,
  `ship_radio_hal.c`) such as an out-of-bounds read or write.
- A leak of a secret, key, or personal data into the repository.

**Out of scope** (please do not report): the deliberate space-for-security
trade-offs of the 19-byte frame (a 16-bit SipHash tag, a 16-bit replay
counter), and behavior that only works because a user configured their *own*
Home Assistant insecurely.

## LAYER 3 — The Security Model, in brief

For the full reasoning and evidence, read
[SECURITY_AUDIT.md](SECURITY_AUDIT.md). The headline guarantees:

- **SipHash-2-4** (128-bit keyed) truncated to **16 bits** — a fast,
  forgery-resistant stamp; security depends on the 16-byte group key staying
  confidential and unique per deployment.
- **16-bit monotonic replay counter** with a **rollover resync window**
  (counters 0–8 after `0xFFFF`) — blocks recorded-and-replayed frames without
  getting confused when the counter wraps.
- **Strict input allow-lists** — `group_name`, `target_command`, `script_name`,
  and IPv4 addresses are validated before any payload is built, so a hostile
  value cannot smuggle extra instructions into a device command.
- **Bounded C memory** — only fixed-size `memcpy`/`memset` in the C core; no
  unbounded `strcpy`/`sprintf`/`gets`, and no forbidden `otLinkRaw*` calls
  with IPv6 enabled.
- **No secrets in the repository** — no API keys, tokens, or personal data; the
  MCP server is **stdio-only** (never a network port); the `deadbeef…` string
  is a test vector, not a real key.

## Honest caveats

- SHiP Anchor is a **proof-of-concept**. It is *not* ready for safety-critical
  controls (life-safety systems, medical devices, or anything where failure
  causes harm). Use strong, unique keys on your own devices, and rotate any key
  you ever shared.
- "No secrets in the repository" says nothing about *your* Home Assistant
  configuration — secure your own devices and network.
- The Python integration is tested with **mocked** Home Assistant objects, so
  some paths are verified by the Testing Pyramid and the disaster drills rather
  than a live UI session.

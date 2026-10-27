# Contributing to Project Anchor

## LAYER 1 — The Kitchen-Table Explanation (ELI5)

Project Anchor is building a hub-less, local-first survival protocol for smart
homes. The goal is simple: essential device-to-device actions should keep
working even when the hub, router, internet connection, or cloud is unavailable.
Software developers and hardware tinkerers are welcome to help test, explain,
and improve that goal.

Our documentation rule is **ELI5 First, Engineering Second**. When you add or
change a source file or documentation file, begin with a plain-language
explanation of what it does and why it matters. Then add the technical details
needed by someone implementing, testing, or maintaining it.

## LAYER 2 — Developer Workflow

### The one rule for every change

SHiP Anchor is verified by an industry-standard **3-tier Testing Pyramid**.
Before you open a pull request, run the whole pyramid locally and make sure all
three tiers are green:

```sh
# Tier 1 + 2 — unit and integration (needs `pip install pytest`)
python3 -m pytest tests/ -v          # expect: 143 passed

# Tier 3 — C protocol core + fuzz airbag (needs a C compiler, e.g. gcc/clang)
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
    -I src/ship_core \
   sim/test_ship_core.c \
   src/ship_core/ship_protocol.c \
   src/ship_core/ship_radio_hal.c \
    -o /tmp/test_ship_core && /tmp/test_ship_core     # expect: 13/13

# Tier 3 — disaster + MCP drills
python3 sim/run_anchor_benchmarks.py                   # expect: 6/6, < 1,500 tokens
```

The **invariant to protect:** the LEAT rows produced at Tier 1 must be
field-for-field identical to the `ship_leat_entry_t` struct asserted by the C
suite at Tier 3 — a mismatch is a release blocker. The `pytest` tier stubs
Home Assistant and `voluptuous` (see `tests/conftest.py`), so no Home Assistant
instance is needed. GitHub Actions runs all three tiers on every push and pull
request (`.github/workflows/ci.yml`).

### Fork and branch

1. Fork the repository on GitHub and clone your fork.
2. Create a focused branch for your change:

    ```sh
   git switch -c your-change-name
     ```

3. Make your changes and include tests or documentation where appropriate.

### Keep the code tidy (optional but appreciated)

The project ships a `pyproject.toml` with [Ruff](https://docs.astral.sh/ruff/),
[Black](https://black.readthedocs.io/), and isort configuration:

```sh
pip install ruff black
ruff check .          # lint + import ordering
ruff format .         # or: black .
```

### Run the C core tests

```sh
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
    -I src/ship_core \
   sim/test_ship_core.c \
   src/ship_core/ship_protocol.c \
   src/ship_core/ship_radio_hal.c \
    -o /tmp/test_ship_core && /tmp/test_ship_core
```

Write the binary to a scratch location such as `/tmp` so you do not commit it.
Then run the Python disaster drills and integration checks:

```sh
python3 sim/run_anchor_benchmarks.py
```

The C suite should report **13/13 tests passed** (including the fuzz airbag),
and the Python runner should report **6/6 drills passed**. Please investigate and
resolve failures before opening a pull request.

### Submit a pull request

Push your branch to your fork and open a pull request against `main`. Describe
the problem, summarize the change, and include the test commands and results.
For hardware-dependent behavior, include the device/platform details and explain
what you were able to verify safely.

### Security

If your change touches authentication, the replay counter, the SipHash tag, or
any device-actuating path, read [SECURITY.md](SECURITY.md) first. To *report* a
vulnerability you believe is unpatched, follow the safe-reporting steps there
instead of opening a public pull request.

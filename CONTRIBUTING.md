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

### Fork and branch

1. Fork the repository on GitHub and clone your fork.
2. Create a focused branch for your change:

   ```sh
   git switch -c your-change-name
   ```

3. Make your changes and include tests or documentation where appropriate.

### Run the checks

From the repository root, compile and run the C core tests:

```sh
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
  -Isrc/ship_core \
  sim/test_ship_core.c \
  src/ship_core/ship_protocol.c \
  src/ship_core/ship_radio_hal.c \
  -o sim/test_ship_core
./sim/test_ship_core
```

The generated binary is ignored by Git. Then run the Python disaster drills and
integration checks:

```sh
python3 sim/run_anchor_benchmarks.py
```

The C suite should report **10/10 tests passed**, and the Python runner should
report **6/6 drills passed**. Please investigate and resolve failures before
opening a pull request.

### Submit a pull request

Push your branch to your fork and open a pull request against `main`. Describe
the problem, summarize the change, and include the test commands and results.
For hardware-dependent behavior, include the device/platform details and explain
what you were able to verify safely.

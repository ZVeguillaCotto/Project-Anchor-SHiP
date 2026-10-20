#!/usr/bin/env python3
"""
SHiP Fast-Path compile/link smoke test.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
Compile the ESPHome component on a normal development computer with tiny
stand-in ESPHome headers. This catches broken C++ syntax and missing SHiP
protocol/HAL symbols before the component is built for a real microcontroller.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Python 3.9+ stdlib runner that compiles and links ship_fastpath.cpp in native
mode using an installed clang++ or g++. Temporary stand-ins are removed on exit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENT_CPP = os.path.join(
    REPO_ROOT, "esphome", "components", "ship_fastpath", "ship_fastpath.cpp"
)


def main() -> int:
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        print("FAIL: neither clang++ nor g++ is available")
        return 1

    with tempfile.TemporaryDirectory(prefix="ship-fastpath-compile-") as temp_dir:
        core_dir = os.path.join(temp_dir, "esphome", "core")
        os.makedirs(core_dir)
        stubs = {
            os.path.join(core_dir, "component.h"): """\
#pragma once
#include <cstdint>
namespace esphome {
namespace setup_priority { static const float HARDWARE = 1000.0f; }
class Component {
 public:
  virtual ~Component() = default;
  virtual void setup() {}
  virtual void loop() {}
  virtual float get_setup_priority() const { return 0.0f; }
};
inline uint32_t millis() { return 0; }
}
""",
            os.path.join(core_dir, "log.h"): """\
#pragma once
namespace esphome {
inline void ship_test_log(const char *, const char *, ...) {}
}
#define ESP_LOGI(tag, ...) ::esphome::ship_test_log(tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) ::esphome::ship_test_log(tag, __VA_ARGS__)
#define ESP_LOGE(tag, ...) ::esphome::ship_test_log(tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) ::esphome::ship_test_log(tag, __VA_ARGS__)
#define ESP_LOGV(tag, ...) ::esphome::ship_test_log(tag, __VA_ARGS__)
""",
            os.path.join(core_dir, "application.h"): "#pragma once\n",
            os.path.join(temp_dir, "main.cpp"): "int main() { return 0; }\n",
        }
        for path, contents in stubs.items():
            with open(path, "w", encoding="utf-8") as stub:
                stub.write(contents)

        binary = os.path.join(temp_dir, "ship_fastpath_link_check")
        command = [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DSHIP_PLATFORM_NATIVE",
            "-I",
            temp_dir,
            COMPONENT_CPP,
            os.path.join(temp_dir, "main.cpp"),
            "-o",
            binary,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=60
        )
        if result.returncode:
            print(result.stdout, end="")
            print(result.stderr, end="")
            print("FAIL: ship_fastpath.cpp compile/link check")
            return result.returncode
        subprocess.run([binary], check=True, timeout=10)
        print("PASS: ship_fastpath.cpp compiled and linked with {}.".format(
            os.path.basename(compiler)
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Parent process for the v0.4 layout benchmark."""

from __future__ import annotations

import sys

from openparkcad.layout_benchmark import runner_main

if __name__ == "__main__":
    raise SystemExit(runner_main(sys.argv[1:]))

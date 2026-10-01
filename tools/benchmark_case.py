#!/usr/bin/env python3
"""Isolated worker for a single layout-benchmark case/variant/repeat."""

from __future__ import annotations

import sys

from openparkcad.layout_benchmark import worker_main

if __name__ == "__main__":
    raise SystemExit(worker_main(sys.argv[1:]))

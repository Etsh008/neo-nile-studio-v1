#!/usr/bin/env python3
"""Offline structural test. No GPU or ACE-Step model required."""
from pathlib import Path
import py_compile

root = Path(__file__).resolve().parent
py_compile.compile(str(root / "app" / "main.py"), doraise=True)

required = [
    "Dockerfile",
    "start.sh",
    "app/main.py",
    "app/static/index.html",
    "app/static/app.js",
    "app/static/styles.css",
    ".github/workflows/build-image.yml",
]
missing = [item for item in required if not (root / item).exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

print("Neo Nile Studio V1 structural test: PASS")

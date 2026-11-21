# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import os
import sys
from pathlib import Path

# Ensure `app` is importable when running pytest from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Provide minimal env so `load_config()` can be exercised in tests.
os.environ.setdefault("COC_API_KEYS", "testkey1,testkey2")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://root@localhost:26257/defaultdb")
os.environ.setdefault("API_CLIENT_KEYS", "test:testsecret")
os.environ.setdefault("CONFIG_PATH", str(ROOT / "config" / "config.example.yaml"))
"""Test bootstrap.

bran reads BRAN_HOME at import time and immediately creates the directory +
SQLite DB (config.py / persistence.py both have import-time side effects). We
point BRAN_HOME at a throwaway temp dir *before* any `bran` import so tests
never touch the real ./.bran, and provide a token so api auth tests have one.

conftest.py is imported by pytest before any test module, so setting the env
here is early enough.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="bran-tests-")
os.environ.setdefault("BRAN_HOME", _TMP)
os.environ.setdefault("BRAN_API_TOKEN", "test-token")
# TestClient sends Host: testserver; the TrustedHost allowlist must admit it.
os.environ.setdefault("BRAN_ALLOWED_HOSTS", "testserver")

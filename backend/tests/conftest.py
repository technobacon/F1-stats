"""Shared test configuration: suite speed + cross-test isolation.

Speed:
  * seed_all is deterministic (synthetic staging + validation pipeline) but
    costs ~2 seconds, and the per-test fixtures build one fresh database per
    test — that setup dominated the whole suite (~2s x ~100 tests). Seeding a
    FRESH path now copies a template file seeded once per session instead.
    Reseeding an EXISTING database still runs the real pipeline, because its
    contract (drop only the data tables, preserve accounts/flags) is itself
    under test.
  * PBKDF2 rounds are a production security parameter no test verifies; at the
    production 200k rounds every register/login burned ~0.1s of pure hashing.
    The round count is embedded in each stored hash, so verification always
    matches whatever the hash was created with.

Isolation: the app keeps process-global in-memory state (the practice throttle,
the cached slider salt). Each test gets a fresh database, so that state must be
cleared between tests or one test's throttle hits / salt would leak into the
next.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app import auth, main, seed, service

auth._PBKDF2_ROUNDS = 1_000  # test-only; production strength is set in auth.py

_real_seed_all = seed.seed_all
_template_db: Path | None = None
_template_summary: dict | None = None


def _templated_seed_all(db_path=None):
    global _template_db, _template_summary
    if db_path is None or Path(db_path).exists():
        return _real_seed_all(db_path)
    if _template_db is None:
        _template_db = Path(tempfile.mkdtemp(prefix="f1stats-seed-template-")) / "template.db"
        _template_summary = _real_seed_all(_template_db)
    shutil.copyfile(_template_db, db_path)
    return dict(_template_summary)


seed.seed_all = _templated_seed_all


@pytest.fixture(autouse=True)
def _reset_process_state():
    main.reset_practice_limits()
    service.reset_slider_salt_cache()
    yield
    main.reset_practice_limits()
    service.reset_slider_salt_cache()

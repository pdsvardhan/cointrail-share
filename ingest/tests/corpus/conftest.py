"""The corpus tests use the same throw-away database fixture as the unit tests (tests/unit/conftest.py):
a brand-new SQLite file with the full production schema per test, never the live database."""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "_unit_conftest", os.path.join(os.path.dirname(__file__), os.pardir, "unit", "conftest.py"))
_unit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_unit)

conn = _unit.conn
make_card = _unit.make_card

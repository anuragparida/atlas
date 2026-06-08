"""
Top-level conftest: makes the parent directory (where the notifier modules
live) importable as ``import idempotency``, ``import backoff``, etc.

The notifier service ships its modules at the top of the package:
``docker/notifier/idempotency.py`` etc., not under a sub-package. Tests run
with cwd = docker/notifier/ (per the task body), so sys.path already
contains the modules. This conftest is a belt-and-suspenders for the case
where pytest is invoked from elsewhere.
"""

import os
import sys

# Add the parent of this file (docker/notifier/) to sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

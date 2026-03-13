"""Extended query helpers specific to the API layer.

All queries use ClickHouse parameterized syntax (``{name:Type}``)
to prevent SQL injection.  The ``ch_query`` helper in ``deps.py``
translates these to the HTTP ``param_`` prefix convention.
"""

from __future__ import annotations

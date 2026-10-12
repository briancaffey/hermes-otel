"""The plugin's attribute names and the underscored spellings backends store them in.

OpenObserve, Uptrace and Loki flatten dotted attribute keys with underscores
(``llm.model_name`` -> ``llm_model_name``); the only way to give such a column
its real name back is a table of every attribute the plugin emits (#158).
Shared here so no adapter imports another adapter for it.
"""

from __future__ import annotations

from typing import Dict, Tuple

from ...semconv import ATTRIBUTE_NAMES


def oo_col(dotted_name: str) -> str:
    """The underscored column name a dotted attribute key is stored under."""
    return dotted_name.replace(".", "_")


# Every attribute name the plugin emits: the registry in ``hermes_otel.semconv``
# (#404). OpenObserve flattens ``a.b_c`` and ``a.b.c`` to the same ``a_b_c``
# column, so the only way to give a column its real name back is a table (#158).
KNOWN_ATTRIBUTES: Tuple[str, ...] = ATTRIBUTE_NAMES


def _build_column_table() -> Dict[str, str]:
    table: Dict[str, str] = {}
    # Two names can flatten to one column (``gen_ai.usage.cache_read.input_tokens``
    # and its legacy alias ``gen_ai.usage.cache_read_input_tokens``; ``session.id``
    # and ``session_id``): keep the current dotted spelling, i.e. the one with
    # more dots, and on a tie the shorter name.
    for attr in sorted(KNOWN_ATTRIBUTES, key=lambda a: (-a.count("."), len(a), a)):
        table.setdefault(oo_col(attr), attr)
    return table


_COLUMN_TO_ATTRIBUTE = _build_column_table()


def dotted(underscored: str) -> str:
    """The attribute name behind an OpenObserve column, or the column name itself.

    Not a rule: ``llm_model_name`` is ``llm.model_name`` and
    ``gen_ai_usage_input_tokens`` is ``gen_ai.usage.input_tokens``, which no
    underscore-to-dot rewrite can recover (the old one produced
    ``llm.model.name``). Unknown columns keep OpenObserve's own name rather
    than an invented one.
    """
    return _COLUMN_TO_ATTRIBUTE.get(underscored, underscored)

"""Which skill a tool call loaded, from the ``skill_view`` argument or a
``/skills/`` path, and where that skill lives on disk (evidence only, #147).
Pure functions; ``tools.py`` opens the skill execution-window span from them.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# Matches /skills/<rest...> and captures everything after the skills/ segment.
# Deliberately does NOT match /optional-skills/<name>/references/ or similar
# overlapping directory layouts ("skills" must be a whole path segment).
_SKILL_PATH_RE = re.compile(r"(?:^|/)skills/([A-Za-z0-9_./-]+)(?:/|$)")

# Hermes lays skills out either flat (``skills/<name>/SKILL.md``) or under a
# category (``skills/<category>/<name>/SKILL.md``). Hermes identifies a skill by
# its bare ``<name>`` — that is what ``skill_view`` takes and what the
# ``<available_skills>`` catalog lists — so every inference path below resolves
# to that bare name, never the category.
_SKILL_MANIFEST = "skill.md"


def infer_skill_name(args: Optional[Dict[str, Any]]) -> Optional[str]:
    """Infer a skill name from tool arguments (path-based).

    Matches ``/skills/…`` anywhere in the ``path`` / ``file_path`` / ``target``
    argument and returns the bare skill name (the directory that holds the
    skill's ``SKILL.md``). Returns None if no path present or no match.
    """
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "target"):
        v = args.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        name = infer_skill_name_from_text(v)
        if name:
            return name
    return None


def _skill_dir_on_disk(text: str, parts: List[str]) -> Optional[str]:
    """Walk up from the referenced file to the nearest directory holding SKILL.md.

    Only consulted for files *inside* a skill that are not the manifest itself
    (``references/x.md``, ``scripts/run.sh``…), where the path alone cannot say
    whether the layout is flat or categorized. Bounded to the ``/skills/``
    subtree, so at most ``len(parts)`` ``stat`` calls, and only on tool calls
    that reference a skills path at all. Returns the directory as written in
    the argument (absolute when the argument was), or None.
    """
    idx = text.lower().rfind("/skills/")
    if idx < 0:
        return None
    root = text[: idx + len("/skills/")]
    # Candidate directories from deepest (the path itself, in case it names the
    # skill directory) to shallowest.
    for depth in range(len(parts), 0, -1):
        candidate = root + "/".join(parts[:depth])
        try:
            if os.path.isfile(os.path.join(candidate, "SKILL.md")):
                return candidate
        except OSError:  # pragma: no cover — permission oddities: fall through
            continue
    return None


def _skill_dir_from_filesystem(text: str, parts: List[str]) -> Optional[str]:
    """Bare skill name of the on-disk skill directory containing ``text``, or None."""
    directory = _skill_dir_on_disk(text, parts)
    return os.path.basename(directory.rstrip("/")) if directory else None


def resolve_skill_dir(args: Optional[Dict[str, Any]]) -> Optional[str]:
    """The directory holding the ``SKILL.md`` a tool call touched, or None.

    Evidence only, never built from the skill name (#147): the parent directory
    when the argument names the manifest itself, or the nearest ancestor of the
    referenced file that holds a ``SKILL.md`` on disk. Categorized skills
    (``skills/<category>/<name>/``), plugin-bundled skills and skills outside
    ``HERMES_HOME`` all resolve to their real directory; anything that cannot be
    verified returns None so the caller omits the attribute.
    """
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "target"):
        v = args.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        normalized = v.replace("\\", "/")
        match = _SKILL_PATH_RE.search(normalized)
        if not match:
            continue
        parts = [p for p in match.group(1).strip("/").split("/") if p]
        if not parts:
            continue
        if parts[-1].lower() == _SKILL_MANIFEST and len(parts) > 1:
            return normalized[: normalized.lower().rfind("/" + _SKILL_MANIFEST)]
        directory = _skill_dir_on_disk(normalized, parts)
        if directory:
            return directory
    return None


def infer_skill_name_from_text(text: str) -> Optional[str]:
    """Extract the bare skill name from a free-form string containing a /skills/ path."""
    if not isinstance(text, str):
        return None
    normalized = text.replace("\\", "/")
    match = _SKILL_PATH_RE.search(normalized)
    if not match:
        return None
    parts = [p for p in match.group(1).strip("/").split("/") if p]
    if not parts:
        return None
    # Canonical case: the manifest itself — its parent directory is the skill.
    lowered = [p.lower() for p in parts]
    if _SKILL_MANIFEST in lowered:
        i = lowered.index(_SKILL_MANIFEST)
        return parts[i - 1] if i > 0 else None
    if len(parts) == 1:
        return parts[0]
    # Anything else (``<cat>/<name>``, ``<name>/references/x.md``, …) is
    # ambiguous from the text alone: only the filesystem can say which
    # directory holds SKILL.md. When it cannot, there is no name to report —
    # the first segment used to be emitted, which named the *category* for
    # categorized skills (#157).
    return _skill_dir_from_filesystem(normalized, parts)


# Argument keys the ``skill_view`` tool may carry the skill name under.
_SKILL_VIEW_ARG_KEYS = ("name", "skill", "skill_name")


def detect_skill(tool_name: Optional[str], args: Optional[Dict[str, Any]]):
    """Detect an activated skill from a tool call.

    Two sources, in priority order:

    * ``skill_view`` — the canonical way Hermes loads a skill. The skill name
      arrives as an *argument* (e.g. ``{"name": "axolotl"}``), which the
      path-based :func:`infer_skill_name` heuristic does not catch. A
      plugin-namespaced name (``"plugin:axolotl"``) is reduced to the bare
      skill name.
    * path match — any tool whose ``path`` / ``file_path`` / ``target`` points
      into ``/skills/<name>/`` (e.g. ``read`` of a SKILL.md).

    Returns ``(skill_name, source)`` where ``source`` is ``"skill_view"`` or
    ``"path_match"``, or ``(None, None)`` when no skill is involved.
    """
    if not isinstance(args, dict):
        return None, None
    if tool_name == "skill_view":
        for key in _SKILL_VIEW_ARG_KEYS:
            v = args.get(key)
            if not isinstance(v, str) or not v.strip():
                continue
            # The value may be a bare name, a namespaced ``plugin:name`` /
            # ``category:name``, or a ``category/name`` form — Hermes' own
            # qualified-name syntax, whose last segment is the skill name. A
            # ``/skills/`` path is only accepted when it resolves to a manifest
            # (on disk or by naming SKILL.md); a path that does not is not
            # guessed at (#157).
            if "/skills/" in v.replace("\\", "/").lower():
                name = infer_skill_name_from_text(v)
            else:
                name = v.split(":")[-1].strip("/").split("/")[-1].strip()
            if name:
                return name, "skill_view"
    name = infer_skill_name(args)
    if name:
        return name, "path_match"
    return None, None

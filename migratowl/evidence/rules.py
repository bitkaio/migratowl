# Copyright bitkaio LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ast-grep rules that find code a major upgrade breaks.

Two sources: curated rules for well-known majors (from their migration guides), and call patterns
taken from the code snippets of a changelog excerpt (``res.json(status, obj)`` → ``$R.json($A1, $A2)``).
Rules are plain dicts so they can be sent to ``sandbox_scan`` inside the sandbox.
"""

from __future__ import annotations

import re
from typing import Any

_HTTP_METHODS = "^(get|post|put|delete|patch|all|use|route|head|options)$"
_NUMBER = {"kind": "number"}

# (ecosystem, package) → {major that introduced the break: rules}
_CURATED: dict[tuple[str, str], dict[int, list[dict[str, Any]]]] = {
    ("nodejs", "express"): {5: [
        {"id": "express5-route-path-syntax", "language": "javascript",
         "note": "Express 5 (path-to-regexp 8) no longer accepts ?, *, +, ( ) or regular expressions in route "
                 "paths; use {optional} segments, named wildcards (/*name) and req.query",
         "rule": {"pattern": "$APP.$VERB($PATH, $$$REST)"},
         "constraints": {"VERB": {"regex": _HTTP_METHODS}, "PATH": {"regex": "^['\"`].*[?*()+].*['\"`]$"}}},
        {"id": "express5-res-json-status", "language": "javascript",
         "note": "res.json(status, body) was removed in Express 5; use res.status(status).json(body)",
         "rule": {"pattern": "$RES.json($STATUS, $BODY)"}, "constraints": {"STATUS": _NUMBER}},
        {"id": "express5-res-jsonp-status", "language": "javascript",
         "note": "res.jsonp(status, body) was removed in Express 5; use res.status(status).jsonp(body)",
         "rule": {"pattern": "$RES.jsonp($STATUS, $BODY)"}, "constraints": {"STATUS": _NUMBER}},
        {"id": "express5-res-send-status", "language": "javascript",
         "note": "res.send(status) and res.send(status, body) were removed in Express 5; use res.sendStatus or "
                 "res.status(status).send(body)",
         "rule": {"any": [{"pattern": "$RES.send($STATUS)"}, {"pattern": "$RES.send($STATUS, $BODY)"}]},
         "constraints": {"STATUS": _NUMBER}},
        {"id": "express5-redirect-url-status", "language": "javascript",
         "note": "res.redirect(url, status) was removed in Express 5; use res.redirect(status, url)",
         "rule": {"pattern": "$RES.redirect($URL, $STATUS)"}, "constraints": {"STATUS": _NUMBER}},
        {"id": "express5-app-del", "language": "javascript",
         "note": "app.del() was removed in Express 5; use app.delete()",
         "rule": {"pattern": "$APP.del($$$ARGS)"}},
        {"id": "express5-req-param", "language": "javascript",
         "note": "req.param(name) was removed in Express 5; use req.params, req.body or req.query",
         "rule": {"pattern": "$REQ.param($NAME)"}},
        {"id": "express5-res-sendfile", "language": "javascript",
         "note": "res.sendfile() was removed in Express 5; use res.sendFile()",
         "rule": {"pattern": "$RES.sendfile($$$ARGS)"}},
        {"id": "express5-accepts-singular", "language": "javascript",
         "note": "req.acceptsCharset/Encoding/Language were renamed to the plural forms in Express 5",
         "rule": {"pattern": "$REQ.$M($$$ARGS)"},
         "constraints": {"M": {"regex": "^accepts(Charset|Encoding|Language)$"}}},
    ]},
    ("python", "pydantic"): {2: [
        {"id": "pydantic2-validator", "language": "python",
         "note": "@validator / @root_validator are deprecated in pydantic 2; use @field_validator / @model_validator",
         "rule": {"any": [{"pattern": "@validator($$$ARGS)"}, {"pattern": "@root_validator($$$ARGS)"},
                          {"pattern": "@root_validator"}]}},
        {"id": "pydantic2-basesettings", "language": "python",
         "note": "BaseSettings moved to the pydantic-settings package in pydantic 2",
         "rule": {"kind": "import_from_statement", "regex": "^from\\s+pydantic\\s+import\\b.*\\bBaseSettings\\b"}},
        {"id": "pydantic2-class-config", "language": "python",
         "note": "class Config is replaced by model_config = ConfigDict(...) in pydantic 2 "
                 "(orm_mode → from_attributes)",
         "rule": {"pattern": "class Config: $$$BODY"}},
        {"id": "pydantic2-parse-obj", "language": "python",
         "note": "parse_obj / parse_raw / parse_file are deprecated in pydantic 2; use model_validate / "
                 "model_validate_json",
         "rule": {"pattern": "$M.$F($$$ARGS)"}, "constraints": {"F": {"regex": "^parse_(obj|raw|file)$"}}},
        {"id": "pydantic2-dict", "language": "python",
         "note": ".dict() / .json() / .copy() on models are deprecated in pydantic 2; use model_dump / "
                 "model_dump_json / model_copy",
         "rule": {"pattern": "$M.dict($$$ARGS)"}},
    ]},
    ("python", "numpy"): {2: [
        {"id": "numpy2-removed-aliases", "language": "python",
         "note": "NumPy 2 removed aliases such as np.float_, np.complex_, np.NaN, np.Inf, np.unicode_",
         "rule": {"pattern": "$NP.$A"},
         "constraints": {"A": {"regex": "^(float_|complex_|NaN|Inf|PINF|NINF|NZERO|PZERO|unicode_|string_|cfloat|"
                                         "longfloat|singlecomplex|clongfloat|longcomplex|product|cumproduct|"
                                         "alltrue|sometrue|round_|in1d|row_stack|trapz)$"},
                         "NP": {"regex": "^(np|numpy)$"}}},
    ]},
}

_ECOSYSTEM_LANGUAGE = {"nodejs": "javascript", "python": "python", "go": "go", "java": "java"}
# Call names too common to search for on the strength of a changelog mention alone.
_GENERIC_CALLS = {
    "get", "set", "run", "call", "apply", "bind", "then", "catch", "map", "filter", "push", "pop", "log",
    "print", "len", "str", "int", "toString", "valueOf", "init", "new", "close", "open", "start", "stop",
}
_CODE_SPAN = re.compile(r"`([^`\n]{3,120})`")
_CALL = re.compile(r"^(?:(?P<receiver>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.)?(?P<name>[A-Za-z_$][\w$]*)"
                   r"\((?P<args>[^()]*)\)$")
_MAX_CHANGELOG_RULES = 8


def _major(version: str) -> int | None:
    m = re.search(r"\d+", re.sub(r"^[^\d]*", "", version or ""))
    return int(m.group(0)) if m else None


def curated_rules(ecosystem: str, package: str, current_version: str, latest_version: str) -> list[dict[str, Any]]:
    """Curated rules for each major crossed by the bump (current, latest]."""
    by_major = _CURATED.get((ecosystem, package.lower()), {})
    cur, new = _major(current_version), _major(latest_version)
    if cur is None or new is None:
        return []
    return [rule for major, rules in sorted(by_major.items()) if cur < major <= new for rule in rules]


def changelog_rules(ecosystem: str, excerpt: str) -> list[dict[str, Any]]:
    """Call patterns from the code snippets of a changelog excerpt, matched by call shape and arity."""
    language = _ECOSYSTEM_LANGUAGE.get(ecosystem)
    if language is None or not excerpt:
        return []
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for span in _CODE_SPAN.findall(excerpt):
        call = _CALL.match(span.strip())
        if not call or call.group("name") in _GENERIC_CALLS or len(call.group("name")) < 3:
            continue
        args = [a for a in call.group("args").split(",") if a.strip()]
        if "..." in call.group("args"):
            arg_pattern = "$$$ARGS"
        else:
            arg_pattern = ", ".join(f"$A{i}" for i in range(1, len(args) + 1))
        receiver = "$R." if call.group("receiver") else ""
        pattern = f"{receiver}{call.group('name')}({arg_pattern})"
        if pattern in seen:
            continue
        seen.add(pattern)
        rules.append({
            "id": f"changelog:{span.strip()}",
            "note": f"the changelog mentions `{span.strip()}`",
            "language": language,
            "rule": {"pattern": pattern},
        })
        if len(rules) >= _MAX_CHANGELOG_RULES:
            break
    return rules


def rules_for(
    ecosystem: str, package: str, current_version: str, latest_version: str, excerpt: str = ""
) -> list[dict[str, Any]]:
    """Curated rules for the majors crossed, then patterns from the changelog excerpt."""
    return curated_rules(ecosystem, package, current_version, latest_version) + changelog_rules(ecosystem, excerpt)

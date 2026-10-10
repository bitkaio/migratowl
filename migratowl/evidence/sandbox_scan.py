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

"""Static evidence of how a repository uses its dependencies, computed inside the sandbox.

Standalone on purpose (stdlib + ``ast_grep_py`` only): Migratowl uploads this file into the
sandbox and runs it there, so the repository's code never leaves the pod and is parsed, not run::

    python3 sandbox_scan.py REQUEST.json SOURCE_DIR

REQUEST is ``{"packages": [{"name", "ecosystem", "rules"?: [rule, ...]}]}``. A rule is
``{"id", "note", "language", "rule": <ast-grep rule>, "constraints"?: {...}}``. The JSON printed
on stdout has, per package, the files that import it, the test files among them, whether tests
reach it (True / False / None = unknown) and the hits of its rules.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sys
from collections import deque
from typing import Any

_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
}
_ECOSYSTEM_LANGS = {
    "python": {"python"},
    "nodejs": {"javascript", "typescript", "tsx"},
    "go": {"go"},
    "rust": {"rust"},
    "java": {"java", "kotlin"},
}
# A rule written for one of these languages also runs on the others in its group.
_RULE_LANGS = {"javascript": {"javascript", "typescript", "tsx"}, "typescript": {"typescript", "tsx"}}
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", "target", "vendor", ".next", ".nuxt", "coverage", ".gradle", "bower_components",
}
_MAX_FILE_BYTES = 1_000_000
_MAX_FILES = 5000
_MAX_LISTED = 15
_MAX_HITS = 10
_JS_EXTS = ("", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".mts", ".cts")

_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|src/test)/"
    r"|(^|/)test_[^/]+\.py$|_test\.(py|go)$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|(Test|Tests|IT)\.(java|kt)$"
)

# Import names that differ from the distribution name.
_PY_ALIASES = {
    "pyyaml": ["yaml"], "beautifulsoup4": ["bs4"], "pillow": ["PIL"], "scikit-learn": ["sklearn"],
    "python-dateutil": ["dateutil"], "opencv-python": ["cv2"], "opencv-python-headless": ["cv2"],
    "protobuf": ["google.protobuf"], "attrs": ["attr", "attrs"], "pyjwt": ["jwt"], "python-dotenv": ["dotenv"],
    "psycopg2-binary": ["psycopg2"], "psycopg-binary": ["psycopg"], "typing-extensions": ["typing_extensions"],
    "setuptools": ["setuptools", "pkg_resources"], "google-cloud-storage": ["google.cloud.storage"],
    "msgpack-python": ["msgpack"], "python-multipart": ["multipart"], "pyserial": ["serial"],
}
_JAVA_ALIASES = {
    "com.google.guava:guava": ["com.google.common"],
    "org.apache.commons:commons-lang3": ["org.apache.commons.lang3"],
    "com.fasterxml.jackson.core:jackson-databind": ["com.fasterxml.jackson.databind"],
    "junit:junit": ["org.junit"],
    "org.junit.jupiter:junit-jupiter": ["org.junit.jupiter"],
}

_JS_IMPORT_FROM = re.compile(r"""(?:from|import)\s*\(?\s*['"`]([^'"`]+)['"`]""")
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.MULTILINE)
_RUST_PATH_ROOT = re.compile(r"(?<![\w:])([a-z_][a-z0-9_]*)::")


# --------------------------------------------------------------------------- files


def _source_files(root: str) -> tuple[list[tuple[str, str]], bool]:
    """``(repo-relative path, language)`` for every source file, and whether the cap was hit."""
    files: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            lang = _LANG_BY_EXT.get(os.path.splitext(name)[1])
            if lang is None:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append((os.path.relpath(full, root).replace(os.sep, "/"), lang))
            if len(files) >= _MAX_FILES:
                return files, True
    return files, False


def _is_test(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


# --------------------------------------------------------------------------- imports


def _imports(node: Any, lang: str, text: str) -> list[str]:
    """Module specifiers a file imports, as written."""
    specs: list[str] = []
    if lang == "python":
        for n in node.find_all(kind="import_statement"):
            for part in n.text()[len("import"):].split(","):
                name = part.strip().split(" as ")[0].strip()
                if name:
                    specs.append(name)
        for n in node.find_all(kind="import_from_statement"):
            words = n.text().split()
            if len(words) >= 2:
                specs.append(words[1])
    elif lang in ("javascript", "typescript", "tsx"):
        for n in node.find_all(pattern="require($M)"):
            arg = n.get_match("M")
            if arg is not None and arg.kind() == "string":
                specs.append(arg.text().strip("'\"`"))
        for kind in ("import_statement", "export_statement"):
            for n in node.find_all(kind=kind):
                specs += _JS_IMPORT_FROM.findall(n.text())
        for n in node.find_all(pattern="import($M)"):
            arg = n.get_match("M")
            if arg is not None and arg.kind() == "string":
                specs.append(arg.text().strip("'\"`"))
    elif lang == "go":
        for n in node.find_all(kind="import_spec"):
            path = n.field("path")
            if path is not None:
                specs.append(path.text().strip('"`'))
    elif lang == "rust":
        for n in node.find_all(kind="use_declaration"):
            specs.append(n.text()[len("use"):].strip().lstrip(":").split("::")[0].strip("{}; "))
        for n in node.find_all(kind="extern_crate_declaration"):
            specs.append(n.text().split()[2].rstrip(";"))
        specs += _RUST_PATH_ROOT.findall(text)
    elif lang in ("java", "kotlin"):
        specs += _JAVA_IMPORT.findall(text)
    return specs


def _matches_package(spec: str, package: str, ecosystem: str) -> bool:
    if ecosystem == "nodejs":
        if spec.startswith((".", "/", "node:")):
            return False
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        return name == package
    if ecosystem == "python":
        if spec.startswith("."):
            return False
        candidates = _PY_ALIASES.get(package.lower(), []) + [
            package.lower().replace("-", "_").replace(".", "_"),
            re.sub(r"^python[-_]", "", package.lower()).replace("-", "_"),
        ]
        return any(spec == c or spec.startswith(c + ".") or spec.lower() == c.lower() for c in candidates)
    if ecosystem == "go":
        return spec == package or spec.startswith(package + "/")
    if ecosystem == "rust":
        return spec == package.replace("-", "_")
    if ecosystem == "java":
        group, _, artifact = package.partition(":")
        candidates = _JAVA_ALIASES.get(package, []) + [group, f"{group}.{artifact.replace('-', '.')}"]
        return any(spec == c or spec.startswith(c + ".") for c in candidates if c)
    return False


# --------------------------------------------------------------------------- local import graph


def _resolve_js(importer: str, spec: str, known: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    for ext in _JS_EXTS:
        if base + ext in known:
            return base + ext
    for ext in _JS_EXTS[1:]:
        if f"{base}/index{ext}" in known:
            return f"{base}/index{ext}"
    return None


def _resolve_python(importer: str, spec: str, known: set[str]) -> list[str]:
    if spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        base = posixpath.dirname(importer)
        for _ in range(dots - 1):
            base = posixpath.dirname(base)
        rest = spec.lstrip(".").replace(".", "/")
        stem = posixpath.join(base, rest) if rest else base
        prefixes = [""]
    else:
        stem = spec.replace(".", "/")
        prefixes = ["", "src/"]
    found = []
    for prefix in prefixes:
        for candidate in (f"{prefix}{stem}.py", f"{prefix}{stem}/__init__.py"):
            candidate = posixpath.normpath(candidate)
            if candidate in known:
                found.append(candidate)
    return found


def _reachable_from_tests(
    tests: list[str], edges: dict[str, set[str]]
) -> set[str]:
    seen = set(tests)
    queue = deque(tests)
    while queue:
        for nxt in edges.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


# --------------------------------------------------------------------------- scan


def scan(root: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        from ast_grep_py import SgRoot
    except ImportError:
        return {"available": False, "reason": "ast-grep-py is not installed in the sandbox image", "packages": {}}

    packages = request.get("packages") or []
    files, truncated = _source_files(root)
    known = {path for path, _ in files}
    lang_of = dict(files)

    parsed: dict[str, Any] = {}
    texts: dict[str, str] = {}
    specs_of: dict[str, list[str]] = {}
    for path, lang in files:
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            node = SgRoot(text, lang).root()
            parsed[path], texts[path] = node, text
            specs_of[path] = _imports(node, lang, text)
        except Exception:  # an unparseable file is skipped, never fatal
            continue

    # Local import edges (JS/TS and Python resolve file paths; Go links a package directory).
    edges: dict[str, set[str]] = {}
    for path, specs in specs_of.items():
        lang = lang_of[path]
        targets: set[str] = set()
        for spec in specs:
            if lang in ("javascript", "typescript", "tsx"):
                hit = _resolve_js(path, spec, known)
                if hit:
                    targets.add(hit)
            elif lang == "python":
                targets.update(_resolve_python(path, spec, known))
        if lang == "go":
            directory = posixpath.dirname(path)
            targets.update(p for p in known if posixpath.dirname(p) == directory and lang_of[p] == "go" and p != path)
        edges[path] = targets
    tests = [p for p in specs_of if _is_test(p)]
    reached = _reachable_from_tests(tests, edges)

    result: dict[str, Any] = {}
    for pkg in packages:
        name, ecosystem = str(pkg.get("name", "")), str(pkg.get("ecosystem", ""))
        langs = _ECOSYSTEM_LANGS.get(ecosystem, set())
        importing = sorted(
            p for p, specs in specs_of.items()
            if lang_of[p] in langs and any(_matches_package(s, name, ecosystem) for s in specs)
        )
        test_importers = [p for p in importing if _is_test(p)]
        reach: bool | None
        if ecosystem in ("python", "nodejs", "go"):
            reach = any(p in reached for p in importing)
        elif ecosystem == "rust":
            reach = True if test_importers or any("#[cfg(test)]" in texts.get(p, "") for p in importing) else None
        else:
            reach = True if test_importers else None

        hits: list[dict[str, Any]] = []
        for rule in pkg.get("rules") or []:
            rule_langs = _RULE_LANGS.get(rule.get("language", ""), {rule.get("language", "")})
            config: dict[str, Any] = {"rule": rule.get("rule") or {}}
            if rule.get("constraints"):
                config["constraints"] = rule["constraints"]
            for path, node in parsed.items():
                if lang_of[path] not in rule_langs or len(hits) >= _MAX_HITS:
                    continue
                try:
                    matches = node.find_all(config)
                except Exception:
                    continue
                for m in matches:
                    if len(hits) >= _MAX_HITS:
                        break
                    hits.append({
                        "rule": rule.get("id", ""),
                        "note": rule.get("note", ""),
                        "file": path,
                        "line": m.range().start.line + 1,
                        "text": " ".join(m.text().split())[:160],
                    })

        result[name] = {
            "importing_files": importing[:_MAX_LISTED],
            "importing_count": len(importing),
            "test_files": test_importers[:_MAX_LISTED],
            "tests_reach": reach,
            "hits": hits,
        }

    return {"available": True, "files_scanned": len(parsed), "truncated": truncated, "packages": result}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: sandbox_scan.py REQUEST.json SOURCE_DIR", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        request = json.load(fh)
    print(json.dumps(scan(argv[2], request)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

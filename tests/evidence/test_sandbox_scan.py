# SPDX-License-Identifier: Apache-2.0

"""The evidence scanner that runs inside the sandbox (stdlib + ast_grep_py only)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from migratowl.evidence import sandbox_scan

EXPRESS_ROUTES = {
    "id": "express5-route-syntax",
    "note": "Express 5 (path-to-regexp 8) no longer supports ?, *, ( ) or + in route paths",
    "language": "javascript",
    "rule": {"pattern": "$APP.$VERB($PATH, $$$REST)"},
    "constraints": {"VERB": {"regex": "^(get|post|put|delete|patch|all|use|route)$"},
                    "PATH": {"regex": "^['\"`].*[?*()+].*['\"`]$"}},
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _scan(root: Path, packages: list[dict]) -> dict:
    return sandbox_scan.scan(str(root), {"packages": packages})


class TestJavaScript:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        _write(tmp_path, {
            "app.js": "const express = require('express');\nconst bp = require('body-parser');\n"
                      "const app = express();\napp.get('/?search=:query', (req, res) => res.json(200, {}));\n"
                      "module.exports = app;\n",
            "routing/books.mjs": "import { Router } from 'express';\nexport const r = Router();\n",
            "scripts/seed.js": "const mongoose = require('mongoose');\n",
            "__tests__/app.test.js": "const request = require('supertest');\nconst app = require('../app');\n",
            "node_modules/express/index.js": "module.exports = require('./lib/express');\n",
        })
        return tmp_path

    def test_imports_and_reachability(self, repo: Path) -> None:
        out = _scan(repo, [
            {"name": "express", "ecosystem": "nodejs"},
            {"name": "body-parser", "ecosystem": "nodejs"},
            {"name": "mongoose", "ecosystem": "nodejs"},
        ])
        express, body_parser, mongoose = (out["packages"][n] for n in ("express", "body-parser", "mongoose"))

        assert out["available"] is True
        assert sorted(express["importing_files"]) == ["app.js", "routing/books.mjs"]
        assert express["tests_reach"] is True  # __tests__/app.test.js → ../app → express
        assert body_parser["tests_reach"] is True
        assert mongoose["importing_files"] == ["scripts/seed.js"]
        assert mongoose["tests_reach"] is False

    def test_curated_rule_hits_carry_file_line_and_text(self, repo: Path) -> None:
        out = _scan(repo, [{"name": "express", "ecosystem": "nodejs", "rules": [EXPRESS_ROUTES]}])
        hits = out["packages"]["express"]["hits"]

        assert len(hits) == 1
        assert hits[0]["rule"] == "express5-route-syntax"
        assert (hits[0]["file"], hits[0]["line"]) == ("app.js", 4)
        assert "/?search=:query" in hits[0]["text"]

    def test_dependency_folders_are_not_scanned(self, repo: Path) -> None:
        out = _scan(repo, [{"name": "express", "ecosystem": "nodejs"}])

        assert not any(f.startswith("node_modules/") for f in out["packages"]["express"]["importing_files"])


class TestPython:
    def test_imports_aliases_and_reachability(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "pkg/__init__.py": "",
            "pkg/client.py": "import requests\nimport yaml\n\ndef fetch(u):\n    return requests.get(u)\n",
            "pkg/models.py": "from pydantic import BaseModel, validator\n",
            "tests/test_client.py": "from pkg.client import fetch\n",
        })
        out = _scan(tmp_path, [
            {"name": "requests", "ecosystem": "python"},
            {"name": "PyYAML", "ecosystem": "python"},
            {"name": "pydantic", "ecosystem": "python",
             "rules": [{"id": "pydantic2-validator", "note": "@validator was removed in pydantic 2",
                        "language": "python", "rule": {"pattern": "@validator($$$)"}}]},
        ])
        packages = out["packages"]

        assert packages["requests"]["importing_files"] == ["pkg/client.py"]
        assert packages["requests"]["tests_reach"] is True
        assert packages["PyYAML"]["importing_files"] == ["pkg/client.py"]
        assert packages["pydantic"]["importing_files"] == ["pkg/models.py"]
        assert packages["pydantic"]["tests_reach"] is False
        assert packages["pydantic"]["hits"] == []  # imported, but no @validator in use


class TestOtherEcosystems:
    def test_go_tests_in_the_same_package_reach_it(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "db/db.go": 'package db\nimport pgx "github.com/jackc/pgx/v4"\nfunc C() { pgx.Connect(nil, "") }\n',
            "db/db_test.go": "package db\nimport \"testing\"\nfunc TestC(t *testing.T) {}\n",
        })
        out = _scan(tmp_path, [{"name": "github.com/jackc/pgx/v4", "ecosystem": "go"}])

        assert out["packages"]["github.com/jackc/pgx/v4"]["importing_files"] == ["db/db.go"]
        assert out["packages"]["github.com/jackc/pgx/v4"]["tests_reach"] is True

    def test_rust_and_java_imports(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "src/lib.rs": "use serde_json::Value;\n#[cfg(test)]\nmod tests {}\n",
            "src/main/java/a/A.java": "package a;\nimport com.google.common.base.Strings;\nclass A {}\n",
        })
        out = _scan(tmp_path, [
            {"name": "serde_json", "ecosystem": "rust"},
            {"name": "com.google.guava:guava", "ecosystem": "java"},
        ])

        assert out["packages"]["serde_json"]["importing_files"] == ["src/lib.rs"]
        assert out["packages"]["serde_json"]["tests_reach"] is True  # #[cfg(test)] in the same file
        assert out["packages"]["com.google.guava:guava"]["importing_files"] == ["src/main/java/a/A.java"]
        assert out["packages"]["com.google.guava:guava"]["tests_reach"] is None  # unknown, never "False"


def test_runs_as_a_standalone_script(tmp_path: Path) -> None:
    _write(tmp_path / "src", {"app.js": "const express = require('express');\n"})
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"packages": [{"name": "express", "ecosystem": "nodejs"}]}))

    proc = subprocess.run(
        [sys.executable, sandbox_scan.__file__, str(request), str(tmp_path / "src")],
        capture_output=True, text=True, check=True,
    )

    assert json.loads(proc.stdout)["packages"]["express"]["importing_files"] == ["app.js"]


def test_reports_unavailable_without_ast_grep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ast_grep_py", None)  # import raises ImportError

    out = sandbox_scan.scan(str(tmp_path), {"packages": [{"name": "x", "ecosystem": "nodejs"}]})

    assert out["available"] is False

# SPDX-License-Identifier: Apache-2.0

"""Breaking-change rules: curated ones for well-known majors, and call patterns derived from changelogs."""

from pathlib import Path

from migratowl.evidence import sandbox_scan
from migratowl.evidence.rules import changelog_rules, curated_rules, rules_for


def _hits(tmp_path: Path, files: dict[str, str], package: str, ecosystem: str, rules: list[dict]) -> list[dict]:
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    out = sandbox_scan.scan(str(tmp_path), {"packages": [{"name": package, "ecosystem": ecosystem, "rules": rules}]})
    return out["packages"][package]["hits"]


class TestCurated:
    def test_only_the_majors_crossed_apply(self) -> None:
        assert curated_rules("nodejs", "express", "4.21.2", "5.2.1")
        assert curated_rules("nodejs", "express", "5.0.0", "5.2.1") == []
        assert curated_rules("nodejs", "left-pad", "1.0.0", "2.0.0") == []

    def test_express5_rules_find_the_real_breaks(self, tmp_path: Path) -> None:
        app = (
            "const express = require('express');\nconst app = express();\n"
            "app.get('/?search=:query', (req, res) => res.json(200, { ok: true }));\n"
            "app.get('/plain', (req, res) => res.send(404));\n"
            "app.del('/old', h);\nconst id = req.param('id');\nres.sendfile('a.html');\n"
            "app.get('/fine/:id', (req, res) => res.status(200).json({ ok: true }));\n"
        )
        rules = curated_rules("nodejs", "express", "4.18.0", "5.0.0")
        hits = _hits(tmp_path, {"app.js": app}, "express", "nodejs", rules)
        found = {(h["rule"], h["line"]) for h in hits}

        assert ("express5-route-path-syntax", 3) in found
        assert ("express5-res-json-status", 3) in found
        assert ("express5-res-send-status", 4) in found
        assert ("express5-app-del", 5) in found
        assert ("express5-req-param", 6) in found
        assert ("express5-res-sendfile", 7) in found
        assert not any(h["line"] == 8 for h in hits)

    def test_pydantic2_rules(self, tmp_path: Path) -> None:
        src = (
            "from pydantic import BaseModel, BaseSettings, validator\n\n"
            "class U(BaseModel):\n    name: str\n\n    class Config:\n        orm_mode = True\n\n"
            "    @validator('name')\n    def check(cls, v):\n        return v\n\n"
            "u = U.parse_obj({'name': 'x'})\nd = u.dict()\n"
        )
        rules = curated_rules("python", "pydantic", "1.10.0", "2.7.0")
        hits = _hits(tmp_path, {"m.py": src}, "pydantic", "python", rules)
        rules = {h["rule"] for h in hits}

        assert {"pydantic2-validator", "pydantic2-basesettings", "pydantic2-class-config",
                "pydantic2-parse-obj", "pydantic2-dict"} <= rules


class TestFromChangelog:
    def test_call_snippets_become_arity_patterns(self, tmp_path: Path) -> None:
        excerpt = (
            "- **Deprecated API methods removed**: `res.json(status, obj)` and `res.jsonp(status, obj)` are gone.\n"
            "- Use `res.status(status).json(obj)` instead. The `get()` helper is unchanged.\n"
        )
        rules = changelog_rules("nodejs", excerpt)
        hits = _hits(tmp_path, {"a.js": "res.json(200, {});\nres.json({});\nres.status(200).json({});\n"},
                     "express", "nodejs", rules)

        assert [h["line"] for h in hits] == [1]
        assert all(r["id"].startswith("changelog:") for r in rules)
        assert not any("get" in r["id"] for r in rules), "generic names are not searched"

    def test_unknown_ecosystems_and_empty_text_give_nothing(self) -> None:
        assert changelog_rules("rust", "`serde_json::from_str(s)`") == []
        assert changelog_rules("nodejs", "") == []


def test_rules_for_combines_both_and_caps_the_count() -> None:
    excerpt = " ".join(f"`obj.method{i}(a, b)`" for i in range(30))
    rules = rules_for("nodejs", "express", "4.0.0", "5.0.0", excerpt)

    assert any(r["id"].startswith("express5-") for r in rules)
    assert sum(r["id"].startswith("changelog:") for r in rules) <= 8

"""Tests for manifest file parsers."""

from migratowl.models.schemas import Ecosystem
from migratowl.parsers import (
    parse_cargo_toml,
    parse_go_mod,
    parse_package_json,
    parse_pyproject_toml,
    parse_requirements_txt,
)

MANIFEST_PATH = "requirements.txt"


class TestParseRequirementsTxt:
    def test_pinned_version(self) -> None:
        content = "requests==2.31.0\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 1
        assert deps[0].name == "requests"
        assert deps[0].current_version == "2.31.0"
        assert deps[0].ecosystem == Ecosystem.PYTHON
        assert deps[0].manifest_path == MANIFEST_PATH

    def test_multiple_pinned(self) -> None:
        content = "requests==2.31.0\nflask==3.0.0\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 2
        assert deps[0].name == "requests"
        assert deps[1].name == "flask"

    def test_range_constraint(self) -> None:
        content = "requests>=2.28,<3.0\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 1
        assert deps[0].name == "requests"
        assert deps[0].current_version == ">=2.28,<3.0"

    def test_tilde_constraint(self) -> None:
        content = "requests~=2.28\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert deps[0].current_version == "~=2.28"

    def test_skips_comments_and_blanks(self) -> None:
        content = "# a comment\n\nrequests==1.0\n  \n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 1
        assert deps[0].name == "requests"

    def test_skips_option_lines(self) -> None:
        content = "-r base.txt\n-c constraints.txt\n-e .\n--index-url https://pypi.org\nrequests==1.0\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 1

    def test_skips_url_lines(self) -> None:
        content = "https://example.com/pkg.tar.gz\nrequests==1.0\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 1

    def test_no_version_spec(self) -> None:
        content = "requests\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert len(deps) == 1
        assert deps[0].name == "requests"
        assert deps[0].current_version == ""

    def test_empty_content(self) -> None:
        deps = parse_requirements_txt("", MANIFEST_PATH)
        assert deps == []

    def test_inline_comment_stripped(self) -> None:
        content = "requests==2.31.0  # needed for HTTP\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert deps[0].current_version == "2.31.0"

    def test_extras_bracket(self) -> None:
        content = "requests[security]==2.31.0\n"
        deps = parse_requirements_txt(content, MANIFEST_PATH)

        assert deps[0].name == "requests[security]"
        assert deps[0].current_version == "2.31.0"


class TestParsePyprojectToml:
    def test_pep621_dependencies(self) -> None:
        content = """\
[project]
name = "myapp"
dependencies = [
    "requests>=2.28",
    "flask==3.0.0",
]
"""
        deps = parse_pyproject_toml(content, "pyproject.toml")

        assert len(deps) == 2
        assert deps[0].name == "requests"
        assert deps[0].current_version == ">=2.28"
        assert deps[1].name == "flask"
        assert deps[1].current_version == "==3.0.0"

    def test_poetry_dependencies(self) -> None:
        content = """\
[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.31"
flask = {version = "^3.0", extras = ["async"]}
"""
        deps = parse_pyproject_toml(content, "pyproject.toml")

        names = {d.name for d in deps}
        assert "python" not in names
        assert "requests" in names
        assert "flask" in names
        req = next(d for d in deps if d.name == "requests")
        assert req.current_version == "^2.31"
        fl = next(d for d in deps if d.name == "flask")
        assert fl.current_version == "^3.0"

    def test_pep621_takes_precedence(self) -> None:
        content = """\
[project]
dependencies = ["requests>=2.28"]

[tool.poetry.dependencies]
flask = "^3.0"
"""
        deps = parse_pyproject_toml(content, "pyproject.toml")

        assert len(deps) == 1
        assert deps[0].name == "requests"

    def test_no_dependencies_section(self) -> None:
        content = """\
[project]
name = "myapp"
"""
        deps = parse_pyproject_toml(content, "pyproject.toml")
        assert deps == []

    def test_empty_content(self) -> None:
        deps = parse_pyproject_toml("", "pyproject.toml")
        assert deps == []

    def test_pep508_with_extras(self) -> None:
        content = """\
[project]
dependencies = ["uvicorn[standard]>=0.20"]
"""
        deps = parse_pyproject_toml(content, "pyproject.toml")

        assert deps[0].name == "uvicorn[standard]"
        assert deps[0].current_version == ">=0.20"

    def test_pep508_no_version(self) -> None:
        content = """\
[project]
dependencies = ["requests"]
"""
        deps = parse_pyproject_toml(content, "pyproject.toml")

        assert deps[0].name == "requests"
        assert deps[0].current_version == ""


class TestParsePackageJson:
    def test_dependencies(self) -> None:
        content = '{"dependencies": {"express": "^4.18.0", "lodash": "~4.17.21"}}'
        deps = parse_package_json(content, "package.json")

        assert len(deps) == 2
        exp = next(d for d in deps if d.name == "express")
        assert exp.current_version == "4.18.0"
        assert exp.ecosystem == Ecosystem.NODEJS
        lod = next(d for d in deps if d.name == "lodash")
        assert lod.current_version == "4.17.21"

    def test_dev_dependencies(self) -> None:
        content = '{"devDependencies": {"jest": "^29.0.0"}}'
        deps = parse_package_json(content, "package.json")

        assert len(deps) == 1
        assert deps[0].name == "jest"

    def test_merges_both(self) -> None:
        content = '{"dependencies": {"express": "^4.18.0"}, "devDependencies": {"jest": "^29.0.0"}}'
        deps = parse_package_json(content, "package.json")

        assert len(deps) == 2

    def test_exact_version(self) -> None:
        content = '{"dependencies": {"express": "4.18.0"}}'
        deps = parse_package_json(content, "package.json")

        assert deps[0].current_version == "4.18.0"

    def test_strips_eq_prefix(self) -> None:
        content = '{"dependencies": {"express": "=4.18.0"}}'
        deps = parse_package_json(content, "package.json")

        assert deps[0].current_version == "4.18.0"

    def test_strips_gte_prefix(self) -> None:
        content = '{"dependencies": {"express": ">=4.18.0"}}'
        deps = parse_package_json(content, "package.json")

        assert deps[0].current_version == "4.18.0"

    def test_no_deps(self) -> None:
        content = '{"name": "myapp"}'
        deps = parse_package_json(content, "package.json")
        assert deps == []

    def test_empty_content(self) -> None:
        deps = parse_package_json("", "package.json")
        assert deps == []

    def test_workspace_star_version(self) -> None:
        content = '{"dependencies": {"my-lib": "*"}}'
        deps = parse_package_json(content, "package.json")

        assert deps[0].current_version == "*"


class TestParseGoMod:
    def test_single_require(self) -> None:
        content = 'module example.com/mymod\n\ngo 1.21\n\nrequire github.com/gin-gonic/gin v1.9.1\n'
        deps = parse_go_mod(content, "go.mod")

        assert len(deps) == 1
        assert deps[0].name == "github.com/gin-gonic/gin"
        assert deps[0].current_version == "1.9.1"
        assert deps[0].ecosystem == Ecosystem.GO

    def test_require_block(self) -> None:
        content = """\
module example.com/mymod

go 1.21

require (
\tgithub.com/gin-gonic/gin v1.9.1
\tgolang.org/x/text v0.14.0
)
"""
        deps = parse_go_mod(content, "go.mod")

        assert len(deps) == 2
        assert deps[0].name == "github.com/gin-gonic/gin"
        assert deps[0].current_version == "1.9.1"
        assert deps[1].name == "golang.org/x/text"
        assert deps[1].current_version == "0.14.0"

    def test_indirect_deps_included(self) -> None:
        content = "module m\n\nrequire github.com/foo/bar v1.0.0 // indirect\n"
        deps = parse_go_mod(content, "go.mod")

        assert len(deps) == 1
        assert deps[0].name == "github.com/foo/bar"

    def test_empty_content(self) -> None:
        deps = parse_go_mod("", "go.mod")
        assert deps == []

    def test_no_require(self) -> None:
        content = "module example.com/mymod\n\ngo 1.21\n"
        deps = parse_go_mod(content, "go.mod")
        assert deps == []

    def test_v_prefix_stripped(self) -> None:
        content = "module m\n\nrequire github.com/foo/bar v2.3.4\n"
        deps = parse_go_mod(content, "go.mod")
        assert deps[0].current_version == "2.3.4"


class TestParseCargoToml:
    def test_string_version(self) -> None:
        content = """\
[dependencies]
serde = "1.0"
"""
        deps = parse_cargo_toml(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0].name == "serde"
        assert deps[0].current_version == "1.0"
        assert deps[0].ecosystem == Ecosystem.RUST

    def test_table_version(self) -> None:
        content = """\
[dependencies]
serde = { version = "1.0", features = ["derive"] }
"""
        deps = parse_cargo_toml(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0].name == "serde"
        assert deps[0].current_version == "1.0"

    def test_multiple_deps(self) -> None:
        content = """\
[dependencies]
serde = "1.0"
tokio = { version = "1.35", features = ["full"] }
"""
        deps = parse_cargo_toml(content, "Cargo.toml")

        assert len(deps) == 2

    def test_no_dependencies(self) -> None:
        content = """\
[package]
name = "myapp"
"""
        deps = parse_cargo_toml(content, "Cargo.toml")
        assert deps == []

    def test_empty_content(self) -> None:
        deps = parse_cargo_toml("", "Cargo.toml")
        assert deps == []

    def test_path_dependency_no_version(self) -> None:
        content = """\
[dependencies]
my-lib = { path = "../my-lib" }
"""
        deps = parse_cargo_toml(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0].name == "my-lib"
        assert deps[0].current_version == ""

    def test_dev_dependencies(self) -> None:
        content = """\
[dev-dependencies]
pretty_assertions = "1.4"
"""
        deps = parse_cargo_toml(content, "Cargo.toml")

        assert len(deps) == 1
        assert deps[0].name == "pretty_assertions"

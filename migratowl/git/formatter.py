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

"""Format ScanAnalysisReport as a GitHub/GitLab PR comment."""

from migratowl.models.schemas import AnalysisReport, ScanAnalysisReport

# (input_cost_per_1M_tokens, output_cost_per_1M_tokens) in USD
_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def _format_tokens(input_tokens: int, output_tokens: int) -> str:
    """Humanise token counts as '1.2M tokens (↑890K / ↓355K)'. Returns '' when both are zero."""
    total = input_tokens + output_tokens
    if total == 0:
        return ""

    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.0f}K"
        return str(n)

    return f"{_fmt(total)} tokens (↑{_fmt(input_tokens)} / ↓{_fmt(output_tokens)})"


def _estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> str:
    """Return '~$0.12' for known models, '' for unknown or zero-token runs."""
    pricing = _PRICING.get(model_name)
    if not pricing or (input_tokens + output_tokens) == 0:
        return ""
    cost = (input_tokens * pricing[0] + output_tokens * pricing[1]) / 1_000_000
    return f"~${cost:.2f}"


def _details_block(summary: str, body: str) -> list[str]:
    """Return lines for a collapsible <details> block."""
    return ["", f"<details><summary>{summary}</summary>", "", body, "", "</details>"]


def _repo_slug(url: str) -> str:
    """Extract owner/repo from a GitHub/GitLab URL."""
    parts = url.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else url


def _parse_skipped(entry: str) -> tuple[str, str]:
    """Split 'name (reason)' into (name, reason). Returns (entry, '') if no parens."""
    if " (" in entry and entry.endswith(")"):
        idx = entry.index(" (")
        return entry[:idx], entry[idx + 2 : -1]
    return entry, ""


def _skipped_card(name: str, reason: str) -> list[str]:
    """Return lines for a per-package skipped detail card."""
    title = f"⊘ `{name}`"
    lines: list[str] = ["", f"<details><summary>{title}</summary>"]
    lines += ["", reason if reason else "Skipped during analysis."]
    lines += ["", "</details>"]
    return lines


def _detail_card(r: AnalysisReport, current: str, latest: str) -> list[str]:
    """Return lines for a per-package breaking-change detail card."""
    if current != "—" and latest != "—":
        title = f"✗ `{r.dependency_name}` — {current} → {latest}"
    else:
        title = f"✗ `{r.dependency_name}`"

    lines: list[str] = ["", f"<details><summary>{title}</summary>"]
    if r.error_summary:
        lines += ["", f"**Error:** `{r.error_summary}`"]
    if r.changelog_citation:
        lines += ["", f"> _{r.changelog_citation}_"]
    if r.suggested_human_fix:
        lines += ["", f"**Fix:** {r.suggested_human_fix}"]
    lines += ["", "</details>"]
    return lines


def format_pr_comment(report: ScanAnalysisReport) -> str:
    """Return a markdown string suitable for posting as a PR/MR comment."""
    lines: list[str] = ["## Migratowl Dependency Analysis", ""]

    slug = _repo_slug(report.repo_url)
    lines.append(f"Scanned `{slug}` · `{report.branch_name}`")
    lines.append("")

    version_map: dict[str, tuple[str, str]] = {
        dep.name: (dep.current_version, dep.latest_version)
        for dep in report.scan_result.outdated
    }
    all_deps_map: dict[str, str] = {
        dep.name: dep.current_version
        for dep in report.scan_result.all_deps
    }

    if not report.reports and not report.skipped:
        lines.append("_No outdated dependencies found to analyze._")
    else:
        lines += [
            "| Package | Current | Latest | Status |",
            "|---------|---------|--------|--------|",
        ]
        sorted_reports = sorted(
            report.reports,
            key=lambda r: (not r.is_breaking, r.dependency_name),
        )
        for r in sorted_reports:
            current, latest = version_map.get(r.dependency_name, ("—", "—"))
            status = "✗ breaking" if r.is_breaking else "✓ safe"
            lines.append(f"| `{r.dependency_name}` | {current} | {latest} | {status} |")

        sorted_skipped = sorted(report.skipped)
        for entry in sorted_skipped:
            name, reason = _parse_skipped(entry)
            current = all_deps_map.get(name, "—")
            latest = current if reason.lower().startswith("already at latest") else "—"
            lines.append(f"| `{name}` | {current} | {latest} | — skipped |")

        breaking = [r for r in sorted_reports if r.is_breaking]
        for r in breaking:
            current, latest = version_map.get(r.dependency_name, ("—", "—"))
            lines += _detail_card(r, current, latest)

        for entry in sorted_skipped:
            name, reason = _parse_skipped(entry)
            lines += _skipped_card(name, reason)

    if report.scan_result.registry_failures:
        failed_str = ", ".join(
            f"`{f.name}` ({f.ecosystem})"
            for f in report.scan_result.registry_failures
        )
        lines += _details_block(
            f"{len(report.scan_result.registry_failures)} package(s) could not be queried",
            failed_str,
        )

    breaking_count = sum(1 for r in report.reports if r.is_breaking)
    summary = f"{breaking_count} breaking" if breaking_count else "all safe"

    footer_parts = [
        f"Scan duration: {report.total_duration_seconds:.1f}s",
        f"{len(report.reports)} package(s) analyzed",
        summary,
    ]

    token_str = _format_tokens(report.total_input_tokens, report.total_output_tokens)
    if token_str:
        footer_parts.append(token_str)
        cost_str = _estimate_cost(report.model_name, report.total_input_tokens, report.total_output_tokens)
        if cost_str:
            footer_parts.append(cost_str)

    lines += ["", f"_{' · '.join(footer_parts)}_"]

    return "\n".join(lines)

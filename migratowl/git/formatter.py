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

from migratowl.models.schemas import ScanAnalysisReport

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


def _short_fix(fix: str) -> str:
    """First sentence (or ≤80 chars) of a fix string for the inline table cell."""
    dot_pos = fix.find(". ")
    summary = fix[: dot_pos + 1] if 0 < dot_pos < 80 else fix[:80]
    if len(summary) < len(fix):
        summary = summary.rstrip(".") + "…"
    return summary


def format_pr_comment(report: ScanAnalysisReport) -> str:
    """Return a markdown string suitable for posting as a PR/MR comment."""
    lines: list[str] = ["## Migratowl Dependency Analysis", ""]

    if not report.reports:
        lines.append("_No outdated dependencies found to analyze._")
    else:
        lines += [
            "| Package | Status | Fix |",
            "|---------|--------|-----|",
        ]
        sorted_reports = sorted(
            report.reports,
            key=lambda r: (not r.is_breaking, r.dependency_name),
        )
        for r in sorted_reports:
            status = "⚠️ Breaking" if r.is_breaking else "✅ Safe"
            fix_inline = _short_fix(r.suggested_human_fix) if r.is_breaking and r.suggested_human_fix else "—"
            lines.append(f"| `{r.dependency_name}` | {status} | {fix_inline} |")

        breaking_with_fix = [r for r in sorted_reports if r.is_breaking and r.suggested_human_fix]
        if breaking_with_fix:
            fix_body = "\n\n".join(
                f"**`{r.dependency_name}`** — {r.suggested_human_fix}"
                for r in breaking_with_fix
            )
            lines += _details_block(f"Fix details ({len(breaking_with_fix)} package(s))", fix_body)

    if report.skipped:
        skipped_str = ", ".join(f"`{s}`" for s in report.skipped)
        lines += _details_block(f"{len(report.skipped)} package(s) skipped", skipped_str)

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

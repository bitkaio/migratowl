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

import re

from migratowl.models.schemas import ScanAnalysisReport

# LLM-written text (partly derived from untrusted changelogs) and manifest names are
# rendered as Markdown on GitHub/GitLab; keep them from breaking or abusing the comment.
_MAX_FIELD_CHARS = 1000
_MENTION = re.compile(r"(?<![\w.])@(?=[A-Za-z0-9])")  # @user / @org/team, not emails
_UNSAFE_NAME_CHARS = re.compile(r"[^\w@/.:+\-\[\]]")


def _safe_text(text: str, limit: int = _MAX_FIELD_CHARS) -> str:
    """Escape HTML, neutralise @mentions and image embeds, and cap the length."""
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("![", "!\\[")
    return _MENTION.sub("@\u200b", text)


def _cell(text: str) -> str:
    """``_safe_text`` for a table cell: one line, no column breaks."""
    return _safe_text(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _code(name: str) -> str:
    """A package name as inline code; characters no real package name uses are replaced."""
    return f"`{_UNSAFE_NAME_CHARS.sub('_', name)[:200]}`"

# USD per 1M tokens: (input, output, cache read or None). Anthropic prices from
# the published model table as of 2026-09-25. None = cache reads at 0.1x input.
_PRICING: dict[str, tuple[float, float, float | None]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, None),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, None),
    "claude-opus-4-8": (5.0, 25.0, None),
    "claude-opus-4-7": (5.0, 25.0, None),
    "claude-opus-4-6": (5.0, 25.0, None),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, None),
    "claude-sonnet-4-6": (3.0, 15.0, None),
    "claude-haiku-4-5": (1.0, 5.0, None),
    "claude-haiku-4-5-20251001": (1.0, 5.0, None),
    "gpt-4o": (2.50, 10.0, None),
    "gpt-4o-mini": (0.15, 0.60, None),
}

# Cache writes (5-minute TTL) cost 1.25x the input price.
_CACHE_WRITE_MULTIPLIER = 1.25


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


def _estimate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> str:
    """Return '~$0.12' for known models, '' for unknown or zero-token runs.

    ``input_tokens`` includes cache reads and writes (as LangChain reports it);
    those are priced at their own rates instead of the full input price.
    """
    pricing = _PRICING.get(model_name)
    if not pricing or (input_tokens + output_tokens) == 0:
        return ""
    input_price, output_price, cache_read_price = pricing
    if cache_read_price is None:
        cache_read_price = input_price * 0.1
    uncached = max(input_tokens - cache_read - cache_creation, 0)
    cost = (
        uncached * input_price
        + cache_read * cache_read_price
        + cache_creation * input_price * _CACHE_WRITE_MULTIPLIER
        + output_tokens * output_price
    ) / 1_000_000
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
            "| Package | Status | Confidence | Note |",
            "|---------|--------|------------|------|",
        ]
        sorted_reports = sorted(
            report.reports,
            key=lambda r: (not r.is_breaking, r.dependency_name not in report.reviews, r.dependency_name),
        )
        for r in sorted_reports:
            review = report.reviews.get(r.dependency_name)
            if r.is_breaking:
                status = "⚠️ Breaking"
                note = _cell(_short_fix(r.suggested_human_fix)) if r.suggested_human_fix else "—"
            elif review:
                status, note = "🔍 Review", _cell(_short_fix(review))
            else:
                status, note = "✅ Safe", "—"
            confidence = f"{round(r.confidence * 100)}%"
            lines.append(f"| {_code(r.dependency_name)} | {status} | {confidence} | {note} |")

        breaking_with_fix = [r for r in sorted_reports if r.is_breaking and r.suggested_human_fix]
        if breaking_with_fix:
            fix_body = "\n\n".join(
                f"**{_code(r.dependency_name)}** — {_safe_text(r.suggested_human_fix)}"
                for r in breaking_with_fix
            )
            lines += _details_block(f"Fix details ({len(breaking_with_fix)} package(s))", fix_body)

    reviewed = [r for r in sorted_reports if r.dependency_name in report.reviews] if report.reports else []
    if reviewed:
        review_body = "\n\n".join(
            f"**{_code(r.dependency_name)}** — {_safe_text(report.reviews[r.dependency_name])}" for r in reviewed
        )
        lines += _details_block(
            f"Why {len(reviewed)} package(s) need review (found by static analysis of the code)", review_body
        )

    if report.skipped:
        skipped_str = ", ".join(_code(s) for s in report.skipped)
        lines += _details_block(f"{len(report.skipped)} package(s) skipped", skipped_str)

    if report.scan_result.registry_failures:
        failed_str = ", ".join(
            f"{_code(f.name)} ({f.ecosystem})"
            for f in report.scan_result.registry_failures
        )
        lines += _details_block(
            f"{len(report.scan_result.registry_failures)} package(s) could not be queried",
            failed_str,
        )

    breaking_count = sum(1 for r in report.reports if r.is_breaking)
    review_count = sum(1 for r in report.reports if not r.is_breaking and r.dependency_name in report.reviews)
    summary_parts = [f"{breaking_count} breaking"] if breaking_count else []
    if review_count:
        summary_parts.append(f"{review_count} to review")
    summary = ", ".join(summary_parts) or "all safe"

    footer_parts = [
        f"Scan duration: {report.total_duration_seconds:.1f}s",
        f"{len(report.reports)} package(s) analyzed",
        summary,
    ]

    token_str = _format_tokens(report.total_input_tokens, report.total_output_tokens)
    if token_str:
        footer_parts.append(token_str)
        cost_str = _estimate_cost(
            report.model_name,
            report.total_input_tokens,
            report.total_output_tokens,
            cache_read=report.total_cache_read_tokens,
            cache_creation=report.total_cache_creation_tokens,
        )
        if cost_str:
            footer_parts.append(cost_str)

    lines += ["", f"_{' · '.join(footer_parts)}_"]

    return "\n".join(lines)

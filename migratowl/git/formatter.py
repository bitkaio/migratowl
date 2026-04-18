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


def format_pr_comment(report: ScanAnalysisReport) -> str:
    """Return a markdown string suitable for posting as a PR/MR comment."""
    lines: list[str] = ["## Migratowl Dependency Analysis", ""]

    if not report.reports:
        lines.append("_No outdated dependencies found to analyze._")
    else:
        lines += [
            "| Package | Status | Confidence | Fix |",
            "|---------|--------|------------|-----|",
        ]
        sorted_reports = sorted(
            report.reports,
            key=lambda r: (not r.is_breaking, r.dependency_name),
        )
        for r in sorted_reports:
            status = "⚠️ Breaking" if r.is_breaking else "✅ Safe"
            conf = f"{r.confidence:.0%}"
            fix = r.suggested_human_fix if r.is_breaking else "—"
            if len(fix) > 120:
                fix = fix[:117] + "..."
            lines.append(f"| `{r.dependency_name}` | {status} | {conf} | {fix} |")

    if report.skipped:
        skipped_str = ", ".join(f"`{s}`" for s in report.skipped)
        lines += [
            "",
            f"<details><summary>{len(report.skipped)} package(s) skipped</summary>",
            "",
            skipped_str,
            "",
            "</details>",
        ]

    breaking_count = sum(1 for r in report.reports if r.is_breaking)
    summary = f"{breaking_count} breaking" if breaking_count else "all safe"
    lines += [
        "",
        f"_Scan duration: {report.total_duration_seconds:.1f}s"
        f" · {len(report.reports)} package(s) analyzed · {summary}_",
    ]

    return "\n".join(lines)
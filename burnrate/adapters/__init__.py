"""Adapters turn a raw data source into the common format (see burnrate.schema).

Each adapter is small and does one thing: read one kind of log, return one
DataFrame with the standard columns. Add a new source by adding a file here —
nothing else in the project has to change.

`load_all` is the proof of that claim: it stacks every adapter's output into
one table without knowing anything about any of them.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd

from burnrate import schema


@dataclass
class CombinedReport:
    """Several adapters' load reports, presented as one.

    Deliberately the same shape as an individual adapter's LoadReport
    (`summary()` and `warnings`), so the dashboard does not have to care
    whether it loaded one source or five.
    """

    parts: List[Tuple[str, object]] = field(default_factory=list)

    def add(self, label: str, report: object) -> None:
        self.parts.append((label, report))

    @property
    def rows(self) -> int:
        return sum(getattr(report, "rows", 0) for _, report in self.parts)

    @property
    def warnings(self) -> List[str]:
        """Every source's warnings, each prefixed with where it came from."""
        out = []
        for label, report in self.parts:
            for warning in getattr(report, "warnings", []):
                out.append(f"{label}: {warning}")
        return out

    def summary(self) -> str:
        """One line per source that actually found something.

        Sources with no data are left out rather than reported as zero — an
        absent tool is not a finding, and listing it would push the sources
        that did find data out of view.
        """
        lines = [
            report.summary()
            for _, report in self.parts
            if getattr(report, "rows", 0)
        ]
        return "  \n".join(lines) if lines else "No usage found."


def load_all(
    claude_code_root: Optional[str] = None,
    codex_root: Optional[str] = None,
) -> Tuple[pd.DataFrame, CombinedReport]:
    """Every source this machine has, stacked into one table.

    A source that isn't installed contributes nothing and is not an error.
    """
    from burnrate.adapters import claude_code, codex

    combined = CombinedReport()
    frames = []

    cc_report = claude_code.LoadReport()
    frames.append(
        claude_code.load(claude_code_root or claude_code.DEFAULT_LOG_ROOT, cc_report)
    )
    combined.add("Claude Code", cc_report)

    codex_report = codex.LoadReport()
    frames.append(codex.load(codex_root or codex.DEFAULT_LOG_ROOT, codex_report))
    combined.add("Codex", codex_report)

    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return schema.empty_frame(), combined

    return schema.coerce(pd.concat(frames, ignore_index=True)), combined

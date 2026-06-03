"""
Excel exporter for POC-3B Phase 2 form fill reports.

Produces ``form_fill_report.xlsx`` with three sheets:
    - Filled Fields   — fields that were filled (or would be in DRY_RUN)
    - Unknown Fields  — fields with no bank answer
    - Values Used     — unique key → answer pairs used across all jobs
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from app.models.form_fill import FormFillReport


class FormFillReportExporter:
    """Export a list of FormFillReport objects to an Excel workbook."""

    def __init__(self, export_dir: Path) -> None:
        self._export_dir = export_dir

    def export(self, reports: list[FormFillReport]) -> Path:
        """Write the three-sheet workbook and return the file path."""
        self._export_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._export_dir / "form_fill_report.xlsx"

        filled_rows = []
        unknown_rows = []
        values_seen: dict[str, str] = {}

        for rep in reports:
            mode = "DRY_RUN" if rep.dry_run else "LIVE"
            for f in rep.filled:
                filled_rows.append({
                    "Job ID":          rep.job_id,
                    "Company":         rep.company,
                    "Role":            rep.role,
                    "Mode":            mode,
                    "Question Key":    f.question_key,
                    "Question":        f.question_text,
                    "Question Text":   f.question_text,
                    "Field Type":      f.field_type,
                    "Required":        "Yes" if f.required else "No",
                    "Status":          f.status,
                    "Answer Used":     f.answer_used or "",
                    "Answer Source":   f.answer_source or "",
                    "Error":           f.error or "",
                    "Screenshot Before": rep.screenshot_before or "",
                    "Screenshot After":  rep.screenshot_after or "",
                })
                if f.answer_used:
                    values_seen[f.question_key] = f.answer_used

            for u in rep.unknown:
                unknown_rows.append({
                    "Job ID":        rep.job_id,
                    "Company":       rep.company,
                    "Role":          rep.role,
                    "Mode":          mode,
                    "Question Key":  u.question_key,
                    "Question":      u.question_text,
                    "Question Text": u.question_text,
                    "Field Type":    u.field_type,
                    "Required":      "Yes" if u.required else "No",
                })

        values_rows = [
            {"Question Key": k, "Answer Used": v}
            for k, v in sorted(values_seen.items())
        ]

        _col_filled = [
            "Job ID", "Company", "Role", "Mode",
            "Question Key", "Question", "Question Text", "Field Type", "Required",
            "Status", "Answer Used", "Answer Source", "Error",
            "Screenshot Before", "Screenshot After",
        ]
        _col_unknown = [
            "Job ID", "Company", "Role", "Mode",
            "Question Key", "Question", "Question Text", "Field Type", "Required",
        ]
        _col_values = ["Question Key", "Answer Used"]

        df_filled  = pd.DataFrame(filled_rows)  if filled_rows  else pd.DataFrame(columns=_col_filled)
        df_unknown = pd.DataFrame(unknown_rows) if unknown_rows else pd.DataFrame(columns=_col_unknown)
        df_values  = pd.DataFrame(values_rows)  if values_rows  else pd.DataFrame(columns=_col_values)

        with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
            df_filled.to_excel(writer,  index=False, sheet_name="Filled Fields")
            df_unknown.to_excel(writer, index=False, sheet_name="Unknown Fields")
            df_values.to_excel(writer,  index=False, sheet_name="Values Used")

            for sheet_name, df in [
                ("Filled Fields",  df_filled),
                ("Unknown Fields", df_unknown),
                ("Values Used",    df_values),
            ]:
                ws = writer.sheets[sheet_name]
                for col_idx, col in enumerate(df.columns, start=1):
                    max_len = max(
                        len(str(col)),
                        df[col].astype(str).str.len().max() if not df.empty else 0,
                    )
                    ws.column_dimensions[
                        ws.cell(row=1, column=col_idx).column_letter
                    ].width = min(max(max_len + 2, 14), 80)

        logger.info(
            "Form fill report exported: {} ({} filled, {} unknown, {} unique answers)",
            filepath.name,
            len(df_filled),
            len(df_unknown),
            len(df_values),
        )
        return filepath

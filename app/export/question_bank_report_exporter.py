"""
Excel exporter for the POC-3B Phase 1 question bank report.

Produces ``question_bank_report.xlsx`` with three sheets:
    - Known Questions
    - Unknown Questions
    - Suggested Answers
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from app.question_bank.lookup_service import QuestionBankReport


class QuestionBankReportExporter:
    """Export a ``QuestionBankReport`` to an Excel workbook."""

    def __init__(self, export_dir: Path) -> None:
        self._export_dir = export_dir

    def export(self, report: QuestionBankReport) -> Path:
        """Write the three-sheet workbook and return the file path."""
        self._export_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._export_dir / "question_bank_report.xlsx"

        known_rows = [
            {
                "Job ID":          q.job_id,
                "Company":         q.company,
                "Role":            q.role,
                "Question Key":    q.question_key,
                "Question Text":   q.question_text,
                "Field Type":      q.field_type,
                "Required":        "Yes" if q.required else "No",
                "Stored Answer":   q.stored_answer,
            }
            for q in report.known
        ]

        unknown_rows = [
            {
                "Job ID":          q.job_id,
                "Company":         q.company,
                "Role":            q.role,
                "Question Key":    q.question_key,
                "Question Text":   q.question_text,
                "Field Type":      q.field_type,
                "Required":        "Yes" if q.required else "No",
                "Suggested Answer": q.suggested_answer or "— none —",
            }
            for q in report.unknown
        ]

        suggested_rows = [
            {
                "Question Key":     q.question_key,
                "Question Text":    q.question_text,
                "Suggested Answer": q.suggested_answer or "— none —",
                "Required":         "Yes" if q.required else "No",
                "Company":          q.company,
                "Role":             q.role,
            }
            for q in report.unknown
            if q.suggested_answer
        ]

        df_known = pd.DataFrame(known_rows) if known_rows else pd.DataFrame(
            columns=["Job ID", "Company", "Role", "Question Key",
                     "Question Text", "Field Type", "Required", "Stored Answer"]
        )
        df_unknown = pd.DataFrame(unknown_rows) if unknown_rows else pd.DataFrame(
            columns=["Job ID", "Company", "Role", "Question Key",
                     "Question Text", "Field Type", "Required", "Suggested Answer"]
        )
        df_suggested = pd.DataFrame(suggested_rows) if suggested_rows else pd.DataFrame(
            columns=["Question Key", "Question Text", "Suggested Answer",
                     "Required", "Company", "Role"]
        )

        with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
            df_known.to_excel(writer, index=False, sheet_name="Known Questions")
            df_unknown.to_excel(writer, index=False, sheet_name="Unknown Questions")
            df_suggested.to_excel(writer, index=False, sheet_name="Suggested Answers")

            for sheet_name, df in [
                ("Known Questions", df_known),
                ("Unknown Questions", df_unknown),
                ("Suggested Answers", df_suggested),
            ]:
                ws = writer.sheets[sheet_name]
                for col_idx, column in enumerate(df.columns, start=1):
                    max_len = max(
                        len(str(column)),
                        df[column].astype(str).str.len().max() if not df.empty else 0,
                    )
                    ws.column_dimensions[
                        ws.cell(row=1, column=col_idx).column_letter
                    ].width = min(max(max_len + 2, 14), 80)

        logger.info(
            "Question bank report exported: {} ({} known, {} unknown, {} suggested)",
            filepath.name,
            len(df_known),
            len(df_unknown),
            len(df_suggested),
        )
        return filepath

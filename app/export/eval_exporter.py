"""
Excel exporter for AI evaluated jobs.
"""

from pathlib import Path
import sqlite3

import pandas as pd
from loguru import logger


class EvaluatedJobsExporter:
    """Exports evaluated jobs to a single Excel workbook."""

    def __init__(self, db_path: Path, export_dir: Path):
        self._db_path = db_path
        self._export_dir = export_dir

    def _write_excel(self, df: pd.DataFrame, filepath: Path, sheet_name: str) -> Path:
        """Write a DataFrame to an Excel file with basic width formatting."""
        with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)

            worksheet = writer.sheets[sheet_name]
            for col_idx, column in enumerate(df.columns, start=1):
                max_length = max(
                    len(str(column)),
                    df[column].astype(str).str.len().max() if not df[column].empty else 0,
                )
                adjusted_width = min(max(max_length + 2, 12), 60)
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=col_idx).column_letter
                ].width = adjusted_width

        return filepath

    def export(self) -> Path:
        """
        Export evaluated jobs to Excel.

        Returns:
            Path to the created workbook.
        """
        self._export_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Evaluated jobs export source: jobs JOIN ai_evaluations on job_id"
        )

        query = """
            SELECT
                j.company_name as Company,
                j.job_title as Role,
                e.interview_probability as "Interview Probability",
                e.recommended_resume as Resume,
                e.priority as Priority,
                e.action as Action,
                e.confidence as Confidence,
                e.reason as Reason,
                e.model_name as "Provider Used"
            FROM jobs j
            JOIN ai_evaluations e ON j.id = e.job_id
            ORDER BY e.interview_probability DESC, j.id ASC
        """

        with sqlite3.connect(str(self._db_path)) as conn:
            df: pd.DataFrame = pd.read_sql_query(query, conn)

        filepath = self._export_dir / "evaluated_jobs.xlsx"

        if df.empty:
            logger.warning("No evaluated jobs found to export.")
        self._write_excel(df, filepath, "Evaluations")
        logger.info("Evaluated jobs exported to {} ({} rows)", filepath.name, len(df))
        return filepath

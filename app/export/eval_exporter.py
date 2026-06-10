"""
Excel exporter for AI evaluated jobs.
"""

from pathlib import Path
import sqlite3

import pandas as pd
from loguru import logger

from app.export.utils import write_excel


class EvaluatedJobsExporter:
    """Exports evaluated jobs to a single Excel workbook."""

    def __init__(self, db_path: Path, export_dir: Path):
        self._db_path = db_path
        self._export_dir = export_dir

    def export(self) -> Path | None:
        """
        Export evaluated jobs to Excel.

        Returns:
            Path to the created workbook, or None if no data.
        """
        self._export_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Evaluated jobs export source: jobs LEFT JOIN ai_evaluations on job_id"
        )

        query = """
            SELECT
                j.company_name as Company,
                j.job_title as Role,
                e.interview_probability as "Interview Probability",
                e.recommended_resume as Resume,
                e.priority as Priority,
                COALESCE(e.action, 'pending') as Action,
                e.confidence as Confidence,
                e.reason as Reason,
                e.model_name as "Provider Used"
            FROM jobs j
            LEFT JOIN ai_evaluations e ON j.id = e.job_id
            ORDER BY e.interview_probability DESC, j.id ASC
        """

        with sqlite3.connect(str(self._db_path)) as conn:
            df: pd.DataFrame = pd.read_sql_query(query, conn)

        filepath = self._export_dir / "evaluated_jobs.xlsx"

        if df.empty:
            logger.warning("No evaluated jobs found to export.")
            return None

        write_excel(df, filepath, "Evaluations")
        logger.info("Evaluated jobs exported to {} ({} rows)", filepath.name, len(df))
        return filepath


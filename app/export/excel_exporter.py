from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from app.export.utils import write_excel


# Column mapping: database column → Excel display name
_COLUMN_MAP: dict[str, str] = {
    "company_name": "Company",
    "job_title": "Role",
    "location": "Location",
    "experience_required": "Experience",
    "posted_date": "Posted Date",
    "job_url": "Job URL",
    "apply_url": "Apply URL",
    "recruiter_name": "Recruiter",
    "recruiter_email": "Email",
}


class ExcelExporter:
    """
    Exports job data to Excel files.

    Reads from SQLite, formats with friendly column names,
    and writes timestamped .xlsx files.
    """

    def __init__(self, db_path: Path, export_dir: Path) -> None:
        """
        Args:
            db_path: Path to the SQLite database.
            export_dir: Directory to write Excel files to.
        """
        self._db_path: Path = db_path
        self._export_dir: Path = export_dir

    def export(self) -> Path | None:
        """
        Export all jobs to an Excel file.

        Returns:
            Path to the created Excel file, or None if no jobs found.
        """
        self._export_dir.mkdir(parents=True, exist_ok=True)

        # Read from SQLite
        logger.info("Reading jobs from database for export...")

        import sqlite3
        with sqlite3.connect(str(self._db_path)) as conn:
            df: pd.DataFrame = pd.read_sql_query(
                "SELECT * FROM jobs ORDER BY created_at DESC",
                conn,
            )

        if df.empty:
            logger.warning("No jobs in database — nothing to export")
            return None

        # Select and rename columns
        available_cols: list[str] = [
            col for col in _COLUMN_MAP.keys() if col in df.columns
        ]
        df_export: pd.DataFrame = df[available_cols].rename(columns=_COLUMN_MAP)

        # Generate filename
        timestamp: str = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        filename: str = f"jobs_{timestamp}.xlsx"
        filepath: Path = self._export_dir / filename

        # Write to Excel with formatting using the shared utility
        write_excel(df_export, filepath, "Jobs")

        logger.info("Excel export complete: {} ({} rows)", filepath.name, len(df_export))
        return filepath


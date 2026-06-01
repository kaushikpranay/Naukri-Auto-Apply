"""
Excel exporter.

Exports job data from SQLite to a formatted .xlsx file using
Pandas and OpenPyXL.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger


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

    def export(self) -> Path:
        """
        Export all jobs to an Excel file.

        Returns:
            Path to the created Excel file.

        Raises:
            ValueError: If no jobs exist in the database.
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
            raise ValueError("No jobs found in database to export")

        # Select and rename columns
        available_cols: list[str] = [
            col for col in _COLUMN_MAP.keys() if col in df.columns
        ]
        df_export: pd.DataFrame = df[available_cols].rename(columns=_COLUMN_MAP)

        # Generate filename
        date_str: str = datetime.now().strftime("%Y_%m_%d")
        filename: str = f"jobs_{date_str}.xlsx"
        filepath: Path = self._export_dir / filename

        # Write to Excel with formatting
        with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
            df_export.to_excel(writer, index=False, sheet_name="Jobs")

            # Auto-fit column widths
            worksheet = writer.sheets["Jobs"]
            for col_idx, column in enumerate(df_export.columns, start=1):
                max_length: int = max(
                    len(str(column)),
                    df_export[column].astype(str).str.len().max() if not df_export[column].empty else 0,
                )
                # Cap at 60 chars, minimum 12
                adjusted_width: int = min(max(max_length + 2, 12), 60)
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=col_idx).column_letter
                ].width = adjusted_width

        logger.info("Excel export complete: {} ({} rows)", filepath.name, len(df_export))
        return filepath

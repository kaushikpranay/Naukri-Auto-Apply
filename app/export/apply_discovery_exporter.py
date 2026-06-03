"""
Excel exporter for apply discovery results.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd
from loguru import logger


class ApplyDiscoveryExporter:
    """Export discovered application flow data to Excel."""

    def __init__(self, db_path: Path, export_dir: Path) -> None:
        self._db_path = db_path
        self._export_dir = export_dir

    def export(self) -> Path:
        """Write apply discovery results to ``apply_discovery.xlsx``."""
        self._export_dir.mkdir(parents=True, exist_ok=True)
        query = """
            WITH question_counts AS (
                SELECT job_id, COUNT(*) AS questions_found
                FROM job_application_questions
                GROUP BY job_id
            )
            SELECT
                j.company_name AS Company,
                j.job_title AS Role,
                a.apply_type AS "Apply Type",
                a.apply_url AS "Apply URL",
                a.email AS Email,
                a.hr_name AS "HR Name",
                a.status AS Status,
                COALESCE(q.questions_found, 0) AS "Questions Found",
                a.detected_at AS "Discovery Date"
            FROM job_applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN question_counts q ON q.job_id = a.job_id
            ORDER BY a.detected_at DESC, j.id ASC
        """

        with sqlite3.connect(str(self._db_path)) as conn:
            df: pd.DataFrame = pd.read_sql_query(query, conn)

        filepath = self._export_dir / "apply_discovery.xlsx"
        with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Apply Discovery")

            worksheet = writer.sheets["Apply Discovery"]
            for col_idx, column in enumerate(df.columns, start=1):
                max_length = max(
                    len(str(column)),
                    df[column].astype(str).str.len().max() if not df[column].empty else 0,
                )
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=col_idx).column_letter
                ].width = min(max(max_length + 2, 12), 60)

        logger.info("Apply discovery exported to {} ({} rows)", filepath.name, len(df))
        return filepath

    def export_debug(self) -> Path:
        """Write debug discovery details to ``apply_discovery_debug.xlsx``."""
        self._export_dir.mkdir(parents=True, exist_ok=True)
        query = """
            SELECT
                j.company_name AS Company,
                j.job_title AS Role,
                a.button_text AS "Button Text",
                a.button_selector AS "Button Selector",
                a.url_before AS "URL Before",
                a.url_after AS "URL After",
                a.redirect_count AS "Redirects",
                COALESCE(a.redirect_chain, '') AS "Redirect Chain",
                a.apply_type AS "Apply Type",
                a.apply_url AS "Apply URL",
                a.status AS Status,
                COALESCE(a.screenshot_before, '') AS "Screenshot Before",
                COALESCE(a.screenshot_after, '') AS "Screenshot After",
                COALESCE(a.screenshot_modal, '') AS "Screenshot Modal",
                CASE WHEN a.html_before_path IS NOT NULL AND a.html_before_path != '' THEN COALESCE(a.html_before_path, '') ELSE '' END AS "HTML Before",
                CASE WHEN a.html_path IS NOT NULL AND a.html_path != '' THEN COALESCE(a.html_path, '') ELSE '' END AS "HTML After",
                COALESCE(a.elements_path, '') AS "Elements JSON",
                a.detected_at AS "Detected At"
            FROM job_applications a
            JOIN jobs j ON j.id = a.job_id
            ORDER BY a.detected_at DESC, j.id ASC
        """

        with sqlite3.connect(str(self._db_path)) as conn:
            df: pd.DataFrame = pd.read_sql_query(query, conn)

        filepath = self._export_dir / "apply_discovery_debug.xlsx"
        with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Debug Discovery")

            worksheet = writer.sheets["Debug Discovery"]
            for col_idx, column in enumerate(df.columns, start=1):
                max_length = max(
                    len(str(column)),
                    df[column].astype(str).str.len().max() if not df[column].empty else 0,
                )
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=col_idx).column_letter
                ].width = min(max(max_length + 2, 12), 60)

        logger.info("Apply discovery debug exported to {} ({} rows)", filepath.name, len(df))
        return filepath

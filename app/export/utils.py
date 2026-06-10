"""
Shared export utilities.
"""

from pathlib import Path
import pandas as pd


def write_excel(df: pd.DataFrame, filepath: Path, sheet_name: str) -> Path:
    """Write a DataFrame to an Excel file with basic width formatting, handling empty/NaN safely."""
    with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)

        worksheet = writer.sheets[sheet_name]
        for col_idx, column in enumerate(df.columns, start=1):
            try:
                col_max = df[column].astype(str).str.len().max()
                col_max = int(col_max) if pd.notna(col_max) else 0
            except Exception:
                col_max = 0
            max_length = max(len(str(column)), col_max)
            adjusted_width = min(max(max_length + 2, 12), 60)
            worksheet.column_dimensions[
                worksheet.cell(row=1, column=col_idx).column_letter
            ].width = adjusted_width

    return filepath

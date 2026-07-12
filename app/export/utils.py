"""
Shared export utilities.
"""

from pathlib import Path
import pandas as pd


def write_excel(df: pd.DataFrame, filepath: Path, sheet_name: str) -> Path:
    """Write a DataFrame to an Excel file with basic width formatting, handling empty/NaN safely."""
    with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        autofit_sheets(writer, [(sheet_name, df)], min_w=12, max_w=60)
    return filepath


def autofit_sheets(
    writer: "pd.ExcelWriter",
    sheets: list[tuple[str, "pd.DataFrame"]],
    min_w: int = 14,
    max_w: int = 80,
) -> None:
    """Auto-size column widths for multiple sheets inside an open ExcelWriter context."""
    for sheet_name, df in sheets:
        ws = writer.sheets[sheet_name]
        for col_idx, column in enumerate(df.columns, start=1):
            try:
                col_max = df[column].astype(str).str.len().max()
                col_max = int(col_max) if pd.notna(col_max) else 0
            except Exception:
                col_max = 0
            max_len = max(len(str(column)), col_max)
            ws.column_dimensions[
                ws.cell(row=1, column=col_idx).column_letter
            ].width = min(max(max_len + 2, min_w), max_w)

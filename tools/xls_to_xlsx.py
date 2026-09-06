"""Convert a legacy .xls workbook to .xlsx while preserving its table layout.

This is a bridge for the result annotator, which intentionally edits only
modern .xlsx/.xlsm files.  It preserves values, basic cell styles, merged
ranges, column widths, row heights, hidden rows/columns, and number formats.
It does not attempt to recreate legacy Excel macros or unsupported drawing
objects.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import xlrd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


LINE_STYLES = {
    0: None,
    1: "thin",
    2: "medium",
    3: "dashed",
    4: "dotted",
    5: "thick",
    6: "double",
    7: "hair",
    8: "mediumDashed",
    9: "dashDot",
    10: "mediumDashDot",
    11: "dashDotDot",
    12: "mediumDashDotDot",
    13: "slantDashDot",
}


def _argb(book: xlrd.book.Book, colour_index: int | None) -> str | None:
    if colour_index in (None, 0, 64, 65, 32767):
        return None
    rgb = book.colour_map.get(colour_index)
    if not rgb or any(value is None for value in rgb):
        return None
    return "FF%02X%02X%02X" % tuple(rgb)


def _side(book: xlrd.book.Book, style: int, colour_index: int) -> Side:
    return Side(style=LINE_STYLES.get(style), color=_argb(book, colour_index))


def _copy_style(book: xlrd.book.Book, source_cell: xlrd.sheet.Cell, target_cell) -> None:
    xf = book.xf_list[source_cell.xf_index]
    font = book.font_list[xf.font_index]
    target_cell.font = Font(
        name=font.name or "宋体",
        sz=max(1, font.height / 20),
        bold=bool(font.bold or font.weight >= 700),
        italic=bool(font.italic),
        underline="single" if font.underlined or font.underline_type else None,
        strike=bool(font.struck_out),
        color=_argb(book, font.colour_index),
    )

    alignment = xf.alignment
    horizontal = {0: "general", 1: "left", 2: "center", 3: "right"}.get(alignment.hor_align)
    vertical = {0: "bottom", 1: "center", 2: "top"}.get(alignment.vert_align)
    target_cell.alignment = Alignment(
        horizontal=horizontal,
        vertical=vertical,
        wrap_text=bool(alignment.text_wrapped),
        shrink_to_fit=bool(alignment.shrink_to_fit),
        indent=alignment.indent_level or 0,
        text_rotation=alignment.rotation or 0,
    )

    background = xf.background
    fill_colour = _argb(book, background.pattern_colour_index)
    if background.fill_pattern and fill_colour:
        target_cell.fill = PatternFill(fill_type="solid", fgColor=fill_colour)

    border = xf.border
    target_cell.border = Border(
        left=_side(book, border.left_line_style, border.left_colour_index),
        right=_side(book, border.right_line_style, border.right_colour_index),
        top=_side(book, border.top_line_style, border.top_colour_index),
        bottom=_side(book, border.bottom_line_style, border.bottom_colour_index),
    )

    fmt = book.format_map.get(xf.format_key)
    if fmt and fmt.format_str:
        target_cell.number_format = fmt.format_str


def _value(book: xlrd.book.Book, cell: xlrd.sheet.Cell):
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    return cell.value


def convert_xls_to_xlsx(source: str | Path, output: str | Path) -> Path:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if source_path.suffix.casefold() != ".xls":
        raise ValueError(f"源文件必须是 .xls: {source_path}")
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if source_path == output_path:
        raise ValueError("输出文件不能覆盖源文件")

    book = xlrd.open_workbook(source_path, formatting_info=True)
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)

    for source_sheet in book.sheets():
        target_sheet = workbook.create_sheet(source_sheet.name)
        target_sheet.sheet_view.showGridLines = bool(source_sheet.show_grid_lines)

        for col_index, info in source_sheet.colinfo_map.items():
            column = get_column_letter(col_index + 1)
            target_sheet.column_dimensions[column].width = max(0.5, info.width / 256)
            target_sheet.column_dimensions[column].hidden = bool(info.hidden)

        for row_index in range(source_sheet.nrows):
            row_info = source_sheet.rowinfo_map.get(row_index)
            target_row = row_index + 1
            if row_info:
                target_sheet.row_dimensions[target_row].height = max(1, row_info.height / 20)
                target_sheet.row_dimensions[target_row].hidden = bool(row_info.hidden)
            for col_index in range(source_sheet.ncols):
                source_cell = source_sheet.cell(row_index, col_index)
                target_cell = target_sheet.cell(target_row, col_index + 1)
                target_cell.value = _value(book, source_cell)
                _copy_style(book, source_cell, target_cell)

        for row_lo, row_hi, col_lo, col_hi in source_sheet.merged_cells:
            target_sheet.merge_cells(
                start_row=row_lo + 1,
                end_row=row_hi,
                start_column=col_lo + 1,
                end_column=col_hi,
            )

        target_sheet.freeze_panes = "A2"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="将 .xls 转换为保留基础表格样式的 .xlsx")
    parser.add_argument("--src", required=True, help="源 .xls 文件")
    parser.add_argument("--out", required=True, help="目标 .xlsx 文件")
    args = parser.parse_args()
    result = convert_xls_to_xlsx(args.src, args.out)
    print(f"converted: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

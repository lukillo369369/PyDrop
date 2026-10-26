"""CSV and column operations embedded into exported PyDrop programs.

Tables are ordered dictionaries of equally sized column lists. CSV input keeps
cell text intact; only selected numeric outputs and plot axes are converted.
The editor runs this runtime in its existing worker process.
"""
import csv as _data_csv
import json as _data_json
import math as _data_math_module
import operator as _data_operator
import os as _data_os
from pathlib import Path as _DataPath
import tempfile as _data_tempfile
import sys as _data_sys


class DataError(ValueError):
    """An actionable data error, safe to show directly in the application."""


_DATA_BASE = (_DataPath(_data_sys.executable).resolve().parent if getattr(_data_sys, 'frozen', False) else
              _DataPath(__file__).resolve().parent if '__file__' in globals() else _DataPath.cwd())


def _data_path(path, action):
    if not isinstance(path, str) or not path.strip():
        raise DataError(f'{action}: choose a CSV file path in the node properties.')
    if '\0' in path:
        raise DataError(f'{action}: the file path contains an invalid null character. Choose the file again.')
    target = _DataPath(path).expanduser()
    if target.is_absolute():
        return target
    external = _DATA_BASE / target
    # Standalone graph executables prefer editable files beside the executable.
    # Explicit --add-data inputs remain available inside a one-file bundle.
    if action == 'CSV Input' and not external.exists() and getattr(_data_sys, 'frozen', False):
        bundled = _DataPath(getattr(_data_sys, '_MEIPASS', _DATA_BASE)) / target
        if bundled.is_file():
            return bundled
    return external


def _data_delimiter(delimiter):
    if delimiter == r'\t':
        delimiter = '\t'
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\0':
        raise DataError('CSV delimiter must be one character, such as a comma, semicolon, or \\t for a tab.')
    return delimiter


def _data_validate(data, label='Data'):
    if not isinstance(data, dict) or not data:
        raise DataError(f'{label}: connect a table with at least one named column, such as CSV Input.data.')
    lengths = {}
    for name, values in data.items():
        if not isinstance(name, str) or not name.strip():
            raise DataError(f'{label}: every column needs a nonempty text name.')
        if len(name) > 2000:
            raise DataError(f'{label}: column names must contain at most 2,000 characters. Shorten the header so it can be selected in node properties.')
        if not isinstance(values, (list, tuple)):
            raise DataError(f'{label}: column {name!r} must be a list of values.')
        lengths[name] = len(values)
    if len(set(lengths.values())) != 1:
        details = ', '.join(f'{name!r}: {count}' for name, count in list(lengths.items())[:8])
        raise DataError(f'{label}: unequal column lengths ({details}). Every column must have the same number of rows; no values were truncated.')
    return next(iter(lengths.values()))


def _data_select(data, columns='', label='Data'):
    _data_validate(data, label)
    if not isinstance(columns, str):
        raise DataError(f'{label}: enter column names separated by commas, or leave columns blank for all.')
    if columns.strip():
        try:
            selected = next(_data_csv.reader([columns], skipinitialspace=True, strict=True))
        except _data_csv.Error as exc:
            raise DataError(f'{label}: invalid column selection: {exc}. Separate names with commas and quote names containing commas, such as "Force, N".') from None
        # Prefer exact headers, including significant spaces in quoted names.
        # Plain names may have padding for readability: time, force, power.
        selected = [name if name in data else name.strip() if name.strip() in data else name
                    for name in selected]
    else:
        selected = list(data)
    if any(not name for name in selected) or len(set(selected)) != len(selected):
        raise DataError(f'{label}: select each column once, without empty names between commas.')
    missing = [name for name in selected if name not in data]
    if missing:
        available = ', '.join(list(data)[:12])
        raise DataError(f'{label}: column {missing[0]!r} is missing. Available columns: {available}.')
    return {name: data[name] for name in selected}


def _data_numeric(value, label, row):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        shown = repr(value)[:80]
        raise DataError(f'{label}, data row {row}: {shown} is not a number. Use numeric values such as 12.5; fill or remove blank cells.') from None
    if not _data_math_module.isfinite(result) or abs(result) > 1e100:
        raise DataError(f'{label}, data row {row}: numeric values must be finite and at most 1e100 in magnitude; NaN and infinity are not supported.')
    return result


def _data_column(data, name='', position=0, label='Data'):
    _data_validate(data, label)
    names = list(data)
    if not name:
        if position >= len(names):
            raise DataError(f'{label}: choose a column; this table has only {len(names)} column(s).')
        name = names[position]
    if name not in data:
        raise DataError(f'{label}: column {name!r} is missing. Available columns: {", ".join(names[:12])}.')
    values = [_data_numeric(value, f'{label}, column {name!r}', row) for row, value in enumerate(data[name], 1)]
    return values, name


def _data_read(path, delimiter=',', x_column='', y_column='', need_x=False, need_y=False):
    target = _data_path(path, 'CSV Input')
    delimiter = _data_delimiter(delimiter)
    try:
        with target.open('r', encoding='utf-8-sig', newline='') as handle:
            reader = _data_csv.reader(handle, delimiter=delimiter, strict=True)
            header = next(reader, None)
            if not header:
                raise DataError(f'CSV Input: {str(target)!r} is empty. Add a header row with column names.')
            if any(not name.strip() for name in header):
                raise DataError(f'CSV Input: {str(target)!r} has a blank column name in its header. Name every column.')
            if len(set(header)) != len(header):
                raise DataError(f'CSV Input: {str(target)!r} has duplicate column names. Give each header a unique name.')
            if any(len(name) > 2000 for name in header):
                raise DataError(f'CSV Input: {str(target)!r} has a column name longer than 2,000 characters. Shorten the header so it can be selected in node properties.')
            table = {name: [] for name in header}
            for row in reader:
                # csv.reader returns [] for empty physical lines; ignore those.
                if not row:
                    continue
                if len(row) != len(header):
                    raise DataError(f'CSV Input: {str(target)!r}, CSV line {reader.line_num}: expected {len(header)} columns but found {len(row)}. Check the delimiter and missing or extra fields; no rows were truncated.')
                for name, cell in zip(header, row):
                    table[name].append(cell)
    except FileNotFoundError:
        raise DataError(f'CSV Input: file {str(target)!r} was not found. Choose an existing file; relative paths start at {str(_DATA_BASE)!r}.') from None
    except IsADirectoryError:
        raise DataError(f'CSV Input: {str(target)!r} is a directory. Choose a CSV file.') from None
    except UnicodeError:
        raise DataError(f'CSV Input: {str(target)!r} is not UTF-8 text. Save the file as UTF-8 CSV and try again.') from None
    except _data_csv.Error as exc:
        raise DataError(f'CSV Input: cannot parse {str(target)!r}: {exc}. Check its delimiter and quoted fields.') from None
    except OSError as exc:
        raise DataError(f'CSV Input: cannot read {str(target)!r}: {exc.strerror or exc}. Check the file path and read permissions.') from None
    result = {'data': table}
    if need_x:
        result['x'] = _data_column(table, x_column, 0, f'CSV Input {str(target)!r}')[0]
    if need_y:
        result['y'] = _data_column(table, y_column, 1, f'CSV Input {str(target)!r}')[0]
    return result


def _data_cell(value):
    # Bound both cells and columns before serialization across the process pipe.
    if isinstance(value, (int, float, bool)) or value is None:
        if isinstance(value, float) and not _data_math_module.isfinite(value):
            return str(value)
        return value
    shown = str(value)
    return shown[:300] + ('…' if len(shown) > 300 else '')


def _data_preview(data, title='Data Table'):
    row_count = _data_validate(data, title)
    columns = list(data)[:100]
    rows = [[_data_cell(data[name][row]) for name in columns] for row in range(min(row_count, 200))]
    return {'type': 'table', 'kind': 'table', 'columns': columns, 'column_count': len(data),
            'rows': rows, 'row_count': row_count, 'title': title}


def _data_table(node_id, data, columns='', column_name='derived', values=None):
    row_count = _data_validate(data, 'Data Table')
    selected = _data_select(data, columns, 'Data Table')
    if values is not None:
        if not isinstance(values, (list, tuple)):
            raise DataError('Data Table.values: connect a numeric column or vector calculation. A single number cannot fill a derived column.')
        if len(values) != row_count:
            raise DataError(f'Data Table: unequal column lengths: table has {row_count} rows and derived values have {len(values)}. Each measurement needs one derived value; no values were truncated.')
        if not isinstance(column_name, str) or not column_name.strip():
            raise DataError('Data Table: enter a nonempty name for the derived column.')
        if column_name in selected:
            raise DataError(f'Data Table: column {column_name!r} already exists. Choose a different derived column name to preserve the original measurements.')
        selected[column_name] = list(values)
    _data_present(node_id, _data_preview(selected))
    return selected


def _data_plot(node_id, kind, data=None, x=None, y=None, x_column='', y_column='', title='', x_label='', y_label=''):
    selected_x, selected_y = x_column, y_column
    if x is None:
        x, selected_x = _data_column(data, x_column, 0, 'Plot x axis')
    if y is None:
        y, selected_y = _data_column(data, y_column, 1, 'Plot y axis')
    if not isinstance(x, (list, tuple)) or not isinstance(y, (list, tuple)):
        raise DataError('Plot: x and y must be numeric column lists. Connect CSV Input.x/y or connect data and select axis columns.')
    if len(x) != len(y):
        raise DataError(f'Plot: unequal column lengths: x has {len(x)} values and y has {len(y)}. Match the rows for both axes; no points were truncated.')
    if not x:
        raise DataError('Plot: there are no measurement rows to plot. Add data rows beneath the CSV header.')
    x = [_data_numeric(value, 'Plot x axis', row) for row, value in enumerate(x, 1)]
    y = [_data_numeric(value, 'Plot y axis', row) for row, value in enumerate(y, 1)]
    row_count = len(x)
    count = min(row_count, 2000)
    indexes = [round(index * (row_count - 1) / (count - 1)) for index in range(count)] if count > 1 else [0]
    payload = {'type': 'plot', 'kind': kind, 'x': [x[index] for index in indexes],
               'y': [y[index] for index in indexes], 'row_count': row_count,
               'title': title or ('Line Plot' if kind == 'line' else 'Scatter Plot'),
               'x_label': x_label or selected_x or 'x', 'y_label': y_label or selected_y or 'y'}
    _data_present(node_id, payload)


def _data_write(path, data, columns='', delimiter=',', mode='Error if exists'):
    selected = _data_select(data, columns, 'CSV Output')
    row_count = _data_validate(selected, 'CSV Output')
    target = _data_path(path, 'CSV Output')
    delimiter = _data_delimiter(delimiter)
    if mode not in ('Error if exists', 'Overwrite'):
        raise DataError('CSV Output: choose Error if exists or Overwrite. Appending is not supported.')
    temp_path = None
    try:
        if mode == 'Error if exists' and target.exists():
            raise DataError(f'CSV Output: {str(target)!r} already exists. Choose another file or select Overwrite explicitly.')
        with _data_tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', dir=target.parent,
                                             prefix='.pydrop-', suffix='.csv.tmp', delete=False) as handle:
            temp_path = _DataPath(handle.name)
            writer = _data_csv.writer(handle, delimiter=delimiter)
            writer.writerow(selected)
            for row in range(row_count):
                writer.writerow([selected[name][row] for name in selected])
            handle.flush()
            _data_os.fsync(handle.fileno())
        if mode == 'Overwrite':
            _data_os.replace(temp_path, target)
            temp_path = None
        else:
            # Hard-link publication is atomic and refuses a concurrently created
            # destination. Unlike replace(), it cannot silently overwrite a file.
            _data_os.link(temp_path, target)
    except FileExistsError:
        raise DataError(f'CSV Output: {str(target)!r} already exists. Choose another file or select Overwrite explicitly.') from None
    except FileNotFoundError:
        raise DataError(f'CSV Output: folder {str(target.parent)!r} was not found. Create the folder or choose an existing destination.') from None
    except (OSError, _data_csv.Error, UnicodeError) as exc:
        raise DataError(f'CSV Output: cannot write {str(target)!r}: {exc}. Check the destination folder and write permissions.') from None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _data_math(operation, a, b):
    operations = {'+': _data_operator.add, '-': _data_operator.sub, '*': _data_operator.mul,
                  '/': _data_operator.truediv, '//': _data_operator.floordiv,
                  '%': _data_operator.mod, '**': _data_operator.pow}
    left, right = isinstance(a, (list, tuple)), isinstance(b, (list, tuple))
    if not left and not right:
        return _number(operations[operation](_number(a), _number(b)))
    if left and right and len(a) != len(b):
        raise DataError(f'Math: unequal column lengths ({len(a)} and {len(b)}). Match measurement rows before calculating; no values were truncated.')
    count = len(a) if left else len(b)
    result = []
    for index in range(count):
        av = _data_numeric(a[index] if left else a, 'Math input a', index + 1)
        bv = _data_numeric(b[index] if right else b, 'Math input b', index + 1)
        try:
            result.append(_data_numeric(operations[operation](av, bv), 'Math result', index + 1))
        except (ArithmeticError, TypeError, ValueError) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError(f'Math, data row {index + 1}: {exc}. Check the operands and avoid division by zero or invalid powers.') from None
    return result


def _data_formula(calculate, a, b, c):
    arguments = (a, b, c)
    vectors = [value for value in arguments if isinstance(value, (list, tuple))]
    if not vectors:
        return _number(calculate(*[_number(value) for value in arguments]))
    count = len(vectors[0])
    if any(len(value) != count for value in vectors):
        raise DataError('Function: unequal column lengths. Match measurement rows before calculating; no values were truncated.')
    result = []
    for index in range(count):
        values = [_data_numeric(value[index] if isinstance(value, (list, tuple)) else value,
                                f'Function input {name}', index + 1) for name, value in zip('abc', arguments)]
        try:
            result.append(_data_numeric(calculate(*values), 'Function result', index + 1))
        except (ArithmeticError, TypeError, ValueError) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError(f'Function, data row {index + 1}: {exc}. Check the formula and numeric inputs.') from None
    return result


def _data_result(value):
    return value if isinstance(value, list) else _number(value)


def _data_summary(value):
    if isinstance(value, dict):
        if 'data' in value and isinstance(value['data'], dict):
            return _data_summary(value['data'])
        if value and all(isinstance(column, (list, tuple)) for column in value.values()):
            rows = len(next(iter(value.values())))
            names = ', '.join(str(name)[:40] for name in list(value)[:5])
            return f'Table: {rows} rows, {len(value)} columns ({names})'
        return f'Dictionary: {len(value)} entries'
    if isinstance(value, (list, tuple)):
        preview = ', '.join(repr(_data_cell(item))[:40] for item in value[:5])
        return f'{type(value).__name__}: {len(value)} values [{preview}{", …" if len(value) > 5 else ""}]'
    return repr(value[:200] if isinstance(value, str) else value)[:240]

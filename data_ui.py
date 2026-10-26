"""Small, dependency-free data viewers, also embedded in standalone exports.

This module deliberately has no project imports: generated Python includes its
source so exported tables and plots work wherever Python has Tk installed.
"""
import math as _data_ui_math
import tkinter as _data_tk
from tkinter import ttk as _data_ttk


_data_root = None
_data_windows = set()


def _data_axis(values):
    """Return a scaled domain and nice ticks without overflowing large floats."""
    scale = max((abs(value) for value in values), default=1.0) or 1.0
    low = min(values, default=0.0) / scale
    high = max(values, default=1.0) / scale
    gap = high - low
    padding = gap * 0.06 if gap else max(abs(low) * 0.06, 0.06)
    low, high = low - padding, high + padding
    rough = (high - low) / 5.0
    exponent = _data_ui_math.floor(_data_ui_math.log10(rough))
    magnitude = 10.0 ** exponent
    fraction = rough / magnitude
    step = next((size for size in (1, 2, 2.5, 5, 10) if size >= fraction), 10) * magnitude
    first = _data_ui_math.ceil(low / step)
    last = _data_ui_math.floor(high / step)
    ticks = [index * step for index in range(first, last + 1)]
    return low, high, ticks, scale


def _data_number(value, scale):
    number = value * scale
    if _data_ui_math.isfinite(number):
        return "0" if abs(value) < 1e-14 else format(number, ".6g")
    # Axis padding can exceed the floating-point range, even for finite data.
    exponent = _data_ui_math.floor(_data_ui_math.log10(scale))
    coefficient = value * (scale / (10.0 ** exponent))
    return "{:.6g}e{:+d}".format(coefficient, exponent)


def _data_display(payload, parent=None):
    """Open a read-only table or plot, and return its Toplevel window.

    A supplied parent keeps ownership in the editor. Without a parent, a hidden
    private root owns the presentations; call _data_show after producing them.
    """
    global _data_root
    standalone = parent is None
    if standalone:
        if _data_root is None:
            _data_root = _data_tk.Tk()
            _data_root.withdraw()
        parent = _data_root
    window = _data_tk.Toplevel(parent)
    window.title(str(payload.get("title") or "Data inspection"))
    window.geometry("840x540")
    window.minsize(520, 360)
    window.configure(background="#f4f7fb")
    window._data_payload = payload
    if standalone:
        _data_windows.add(window)

    def closed(event=None):
        global _data_root
        if event is not None and event.widget is not window:
            return
        _data_windows.discard(window)
        if standalone and not _data_windows and _data_root is not None:
            root, _data_root = _data_root, None
            root.quit()
            root.destroy()

    window.bind("<Destroy>", closed, add="+")
    window.bind("<Escape>", lambda event: window.destroy())
    body = _data_ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    _data_ttk.Label(body, text=str(payload.get("title") or "Data inspection"),
              font=("TkDefaultFont", 14, "bold")).pack(anchor="w", pady=(0, 12))

    if payload.get("type") == "table" or payload.get("kind") == "table":
        columns = list(payload.get("columns", []))
        rows = list(payload.get("rows", []))[:200]
        frame = _data_ttk.Frame(body)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        identifiers = ["column_{}".format(index) for index in range(len(columns))]
        table = _data_ttk.Treeview(frame, columns=identifiers, show="headings", selectmode="browse")
        vertical = _data_ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        horizontal = _data_ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        for identifier, name in zip(identifiers, columns):
            table.heading(identifier, text=str(name))
            table.column(identifier, width=max(135, min(300, len(str(name)) * 9 + 25)),
                         minwidth=90, stretch=False, anchor="w")
        for row in rows:
            table.insert("", "end", values=["" if cell is None else str(cell) for cell in row])
        window._data_table = table
        total = int(payload.get("row_count", len(rows)))
        note = ("Preview: first {:,} of {:,} rows. CSV exports include every row."
                .format(len(rows), total) if len(rows) < total else "{:,} rows. Read-only preview.".format(total))
        column_count = int(payload.get("column_count", len(columns)))
        if len(columns) < column_count:
            note += " Showing first {:,} of {:,} columns.".format(len(columns), column_count)
        _data_ttk.Label(body, text=note, wraplength=760).pack(anchor="w", pady=(12, 0))
    else:
        x_values = list(payload.get("x", []))[:2000]
        y_values = list(payload.get("y", []))[:2000]
        if len(x_values) != len(y_values):
            window.destroy()
            raise ValueError("Cannot display plot: X and Y columns have unequal lengths.")
        if not all(_data_ui_math.isfinite(value) for value in x_values + y_values):
            window.destroy()
            raise ValueError("Cannot display plot: X and Y values must be finite numbers.")
        canvas = _data_tk.Canvas(body, background="#ffffff", highlightthickness=1,
                           highlightbackground="#d8e0eb", width=780, height=390)
        canvas.pack(fill="both", expand=True)
        window._data_canvas = canvas
        total = int(payload.get("row_count", len(x_values)))
        note = ("Showing {:,} of {:,} points (sampled across the data). CSV exports include every row."
                .format(len(x_values), total) if len(x_values) < total else "{:,} points.".format(total))
        _data_ttk.Label(body, text=note, wraplength=760).pack(anchor="w", pady=(12, 0))
        x_domain = _data_axis(x_values)
        y_domain = _data_axis(y_values)
        pending = [None]

        def draw():
            pending[0] = None
            if not canvas.winfo_exists():
                return
            width, height = canvas.winfo_width(), canvas.winfo_height()
            canvas.delete("all")
            left, top, right, bottom = 100, 28, width - 28, height - 75
            if right <= left or bottom <= top:
                return
            x_low, x_high, x_ticks, x_scale = x_domain
            y_low, y_high, y_ticks, y_scale = y_domain

            def x_position(value):
                return left + ((value - x_low) / (x_high - x_low)) * (right - left)

            def y_position(value):
                return bottom - ((value - y_low) / (y_high - y_low)) * (bottom - top)

            for tick in x_ticks:
                position = x_position(tick)
                canvas.create_line(position, top, position, bottom, fill="#e8edf4")
                canvas.create_text(position, bottom + 17, text=_data_number(tick, x_scale),
                                   fill="#48566a", font=("TkDefaultFont", 9))
            for tick in y_ticks:
                position = y_position(tick)
                canvas.create_line(left, position, right, position, fill="#e8edf4")
                canvas.create_text(left - 10, position, text=_data_number(tick, y_scale),
                                   anchor="e", fill="#48566a", font=("TkDefaultFont", 9))
            canvas.create_line(left, top, left, bottom, right, bottom, fill="#64748b", width=1.5)
            canvas.create_text((left + right) / 2, height - 24, text=str(payload.get("x_label") or "X"),
                               fill="#25354b", font=("TkDefaultFont", 10))
            canvas.create_text(22, (top + bottom) / 2, text=str(payload.get("y_label") or "Y"),
                               angle=90, fill="#25354b", font=("TkDefaultFont", 10))
            points = [(x_position(x / x_scale), y_position(y / y_scale))
                      for x, y in zip(x_values, y_values)]
            if payload.get("kind") == "line" and len(points) > 1:
                canvas.create_line(*[coordinate for point in points for coordinate in point],
                                   fill="#2563b9", width=2)
            if payload.get("kind") != "line" or len(points) <= 100:
                for x, y in points:
                    canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5,
                                       fill="#2563b9", outline="")
            if not points:
                canvas.create_text((left + right) / 2, (top + bottom) / 2,
                                   text="No data points to display", fill="#64748b")

        def schedule(event=None):
            if pending[0] is not None:
                canvas.after_cancel(pending[0])
            pending[0] = canvas.after(70, draw)

        def cancel_draw(event):
            if event.widget is canvas and pending[0] is not None:
                canvas.after_cancel(pending[0])
                pending[0] = None

        canvas.bind("<Configure>", schedule)
        canvas.bind("<Destroy>", cancel_draw, add="+")
        schedule()
    return window


def _data_show():
    """Keep standalone presentation windows open until the last is closed."""
    if _data_root is not None and _data_windows:
        _data_root.mainloop()

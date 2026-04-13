#!/usr/bin/env python3
"""Minimal CLI for logging hours per day and exporting HTML summaries."""

from __future__ import annotations

import argparse
import calendar
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib import error, request


DATE_FMT = "%Y-%m-%d"
DEFAULT_DATA_FILE = Path(__file__).with_name("time_entries.csv")
HOURLY_RATE_USD = 23.0


@dataclass
class TimeEntry:
    """Tracked hours for a single date."""

    date: date
    hours: float
    description: str


def ensure_data_file(path: Path) -> None:
    if path.exists():
        return
    path.write_text("date,hours,description\n", encoding="utf-8")


def read_entries(path: Path) -> List[TimeEntry]:
    if not path.exists():
        return []
    entries: List[TimeEntry] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row or not row.get("date"):
                continue
            try:
                entries.append(
                    TimeEntry(
                        date=parse_date(row["date"], required=True),
                        hours=parse_hours(row.get("hours"), required=True),
                        description=(row.get("description") or "").strip(),
                    )
                )
            except ValueError as exc:
                raise ValueError(f"Invalid row in {path}: {row}") from exc
    return entries


def append_entry(path: Path, entry: TimeEntry) -> None:
    ensure_data_file(path)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                entry.date.strftime(DATE_FMT),
                serialize_hours(entry.hours),
                entry.description,
            ]
        )


def parse_date(value: str | None, *, required: bool = False) -> date:
    if value:
        normalized = value.strip().lower()
        if normalized == "today":
            return date.today()
        if normalized == "yesterday":
            return date.today() - timedelta(days=1)
        if normalized == "tomorrow":
            return date.today() + timedelta(days=1)
        return datetime.strptime(value, DATE_FMT).date()
    if required:
        raise ValueError("Date is required")
    return date.today()


def parse_hours(value: str | None, *, required: bool = False) -> float:
    if value is not None:
        hours = float(value)
        if hours <= 0:
            raise ValueError("Hours must be greater than zero")
        return hours
    if required:
        raise ValueError("Hours value is required")
    raise ValueError("Hours must be provided")


def serialize_hours(hours: float) -> str:
    return f"{hours:.2f}"


def format_hours(hours: float) -> str:
    rounded = round(hours + 1e-9, 2)
    if rounded.is_integer():
        return f"{int(rounded)}h"
    return f"{rounded:.2f}".rstrip("0").rstrip(".") + "h"


def cli_add(args: argparse.Namespace) -> None:
    data_path: Path = args.data_file
    ensure_data_file(data_path)
    entry = TimeEntry(
        date=parse_date(args.date),
        hours=parse_hours(args.hours, required=True),
        description=args.desc.strip(),
    )
    append_entry(data_path, entry)
    print(
        "Saved:",
        entry.date.strftime(DATE_FMT),
        format_hours(entry.hours),
        entry.description,
    )


def cli_list(args: argparse.Namespace) -> None:
    data_path: Path = args.data_file
    entries = read_entries(data_path)
    if not entries:
        print("No entries yet. Use the add command or edit the CSV manually.")
        return

    start_date = parse_date(args.start_date) if args.start_date else None
    end_date = parse_date(args.end_date) if args.end_date else None
    date_filter = parse_date(args.date) if args.date else None

    def within_range(entry: TimeEntry) -> bool:
        if date_filter and entry.date != date_filter:
            return False
        if start_date and entry.date < start_date:
            return False
        if end_date and entry.date > end_date:
            return False
        return True

    filtered = [entry for entry in entries if within_range(entry)]
    if not filtered:
        print("No entries matched the filter.")
        return

    filtered.sort(key=lambda e: e.date)
    rows = []
    total_hours = 0.0
    for entry in filtered:
        total_hours += entry.hours
        rows.append(
            [
                entry.date.strftime(DATE_FMT),
                format_hours(entry.hours),
                entry.description,
            ]
        )

    print_table(["Date", "Hours", "Description"], rows)
    print(f"Total: {format_hours(total_hours)} across {len(rows)} entries")


def cli_report(args: argparse.Namespace) -> None:
    data_path: Path = args.data_file
    entries = read_entries(data_path)
    if not entries:
        raise SystemExit("No entries available to report on.")

    if args.frequency == "weekly":
        start = date.fromisocalendar(args.year, args.week, 1)
        end = start + timedelta(days=6)
        title = f"Week {args.week} of {args.year}"
        slug = f"weekly-{args.year}-W{args.week:02d}"
    elif args.frequency == "monthly":
        start = date(args.year, args.month, 1)
        _, last_day = calendar.monthrange(args.year, args.month)
        end = date(args.year, args.month, last_day)
        title = f"{calendar.month_name[args.month]} {args.year}"
        slug = f"monthly-{args.year}-{args.month:02d}"
    else:  # custom range
        start = parse_date(args.start_date, required=True)
        end = parse_date(args.end_date)
        if end < start:
            raise SystemExit("End date must be on or after start date.")
        title = f"{start.strftime(DATE_FMT)} → {end.strftime(DATE_FMT)}"
        slug = f"range-{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    output = Path(args.output) if args.output else Path("reports") / f"{slug}.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    sub_entries = [entry for entry in entries if start <= entry.date <= end]
    if not sub_entries:
        raise SystemExit("No entries fell inside that time range.")

    rate_value, rate_timestamp = fetch_usd_to_idr_rate()
    if rate_value is None:
        print("Warning: failed to fetch USD→IDR exchange rate; showing USD only.")

    html = build_html_report(
        sub_entries,
        start,
        end,
        title,
        usd_to_idr_rate=rate_value,
        rate_timestamp=rate_timestamp,
    )
    output.write_text(html, encoding="utf-8")
    print(f"Report saved to {output}")


def build_html_report(
    entries: Iterable[TimeEntry],
    start: date,
    end: date,
    title: str,
    *,
    usd_to_idr_rate: Optional[float],
    rate_timestamp: Optional[str],
) -> str:
    sorted_entries = sorted(entries, key=lambda e: (e.date, e.description))
    day_totals: dict[date, float] = {}
    total_hours = 0.0

    row_html: list[str] = []
    for entry in sorted_entries:
        hours = entry.hours
        total_hours += hours
        day_totals[entry.date] = day_totals.get(entry.date, 0.0) + hours
        row_html.append(
            "<tr>"
            f"<td data-label='Date'>{entry.date.strftime(DATE_FMT)}</td>"
            f"<td data-label='Hours'>{format_hours(hours)}</td>"
            f"<td data-label='Description'>{escape_html(entry.description)}</td>"
            "</tr>"
        )

    calendar_html = build_calendar_sections(start, end, day_totals)
    revenue_usd = total_hours * HOURLY_RATE_USD
    revenue_idr = revenue_usd * usd_to_idr_rate if usd_to_idr_rate else None
    rate_note = (
        f"USD→IDR @ {usd_to_idr_rate:,.2f} (as of {rate_timestamp})"
        if usd_to_idr_rate and rate_timestamp
        else (
            f"USD→IDR @ {usd_to_idr_rate:,.2f}"
            if usd_to_idr_rate
            else "USD→IDR conversion unavailable"
        )
    )

    return f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <title>Time Report – {title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f3f6fb;
      --card: #ffffff;
      --border: #d9e2ef;
      --accent: #1d4ed8;
      --text-muted: #566073;
      font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
    }}
    body {{
      margin: 0;
      padding: 2.5rem;
      background: var(--bg);
      color: #111827;
    }}
    .shell {{
      max-width: 960px;
      margin: 0 auto;
      background: var(--card);
      border-radius: 16px;
      box-shadow: 0 25px 45px rgba(15, 23, 42, 0.08);
      padding: 2.5rem;
    }}
    header {{
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      margin-bottom: 1.5rem;
    }}
    header h1 {{
      margin: 0;
      font-size: 2rem;
      color: var(--accent);
    }}
    header p {{
      margin: 0;
      color: var(--text-muted);
      font-weight: 500;
    }}
    .calendar-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2rem;
    }}
    .calendar {{
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1rem;
      background: linear-gradient(145deg, #fdfcff, #eef3ff);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.6);
    }}
    .calendar h2 {{
      margin: 0 0 0.75rem;
      font-size: 1.15rem;
      color: var(--accent);
      text-align: center;
    }}
    .calendar table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }}
    .calendar th {{
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding-bottom: 0.35rem;
    }}
    .calendar td {{
      width: 14.285%;
      height: 68px;
      border-radius: 12px;
      padding: 0.4rem;
      vertical-align: top;
      position: relative;
    }}
    .calendar td.empty {{
      background: transparent;
    }}
    .calendar td .date {{
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-muted);
    }}
    .calendar td strong {{
      display: block;
      margin-top: 0.2rem;
      font-size: 1rem;
      color: #0f172a;
    }}
    .calendar td .muted {{
      margin-top: 0.15rem;
      font-size: 0.75rem;
      color: #cbd5f5;
    }}
    .calendar td.has-hours {{
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.4);
      box-shadow: 0 8px 20px rgba(37, 99, 235, 0.15);
    }}
    .calendar td.has-hours strong {{
      color: var(--accent);
    }}
    .calendar td.muted .date {{
      color: #c3c9d3;
    }}
    .revenue-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .revenue-card {{
      border-radius: 14px;
      padding: 1rem 1.25rem;
      background: linear-gradient(135deg, #1d4ed8, #3b82f6);
      color: #fff;
      box-shadow: 0 15px 35px rgba(30, 64, 175, 0.35);
    }}
    .revenue-card.alt {{
      background: linear-gradient(135deg, #f97316, #facc15);
      color: #1f2937;
    }}
    .revenue-card h3 {{
      margin: 0;
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .revenue-card p {{
      margin: 0.35rem 0 0;
      font-size: 1.8rem;
      font-weight: 700;
    }}
    .rate-note {{
      margin-top: 0.35rem;
      font-size: 0.75rem;
      opacity: 0.85;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 12px;
      border: 1px solid var(--border);
    }}
    thead {{
      background: linear-gradient(90deg, #1d4ed8, #3b82f6);
      color: white;
    }}
    th, td {{
      padding: 0.85rem 1rem;
      text-align: left;
    }}
    tbody tr:nth-child(odd) {{
      background: #f8fafc;
    }}
    tbody tr:nth-child(even) {{
      background: #ffffff;
    }}
    tfoot td {{
      font-weight: 700;
      background: #eff6ff;
    }}
    @media (max-width: 640px) {{
      body {{ padding: 1.25rem; }}
      .shell {{ padding: 1.5rem; }}
      header h1 {{ font-size: 1.5rem; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead {{ display: none; }}
      tbody tr {{ margin-bottom: 1rem; border-radius: 12px; border: 1px solid var(--border); }}
      tbody td {{
        display: flex;
        justify-content: space-between;
        border-bottom: 1px solid var(--border);
      }}
      tbody td::before {{
        content: attr(data-label);
        font-weight: 600;
        color: var(--text-muted);
      }}
      tfoot tr {{ display: flex; justify-content: space-between; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <h1>Time Report – {title}</h1>
      <p>{start.strftime(DATE_FMT)} → {end.strftime(DATE_FMT)}</p>
      <p>Total Hours: <strong>{format_hours(total_hours)}</strong></p>
    </header>
    <section class="calendar-grid">
      {calendar_html}
    </section>
    <section class="revenue-grid">
      <article class="revenue-card">
        <h3>Revenue (USD)</h3>
        <p>{format_currency(revenue_usd, "USD")}</p>
        <div class="rate-note">Rate: {format_currency(HOURLY_RATE_USD, "USD")} per hour</div>
      </article>
      <article class="revenue-card alt">
        <h3>Revenue (IDR)</h3>
        <p>{format_currency(revenue_idr, "IDR") if revenue_idr else "N/A"}</p>
        <div class="rate-note">{rate_note}</div>
      </article>
    </section>
    <section>
      <table>
        <thead>
          <tr><th>Date</th><th>Hours</th><th>Description</th></tr>
        </thead>
        <tbody>
          {''.join(row_html)}
        </tbody>
        <tfoot>
          <tr><td>Total</td><td>{format_hours(total_hours)}</td><td></td></tr>
        </tfoot>
      </table>
    </section>
  </main>
</body>
</html>"""


def build_calendar_sections(start: date, end: date, day_totals: dict[date, float]) -> str:
    """Render calendar month cards spanning the requested interval."""

    def month_iterator(start_date: date, end_date: date) -> List[tuple[int, int]]:
        months: List[tuple[int, int]] = []
        cursor = date(start_date.year, start_date.month, 1)
        while cursor <= end_date:
            months.append((cursor.year, cursor.month))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        return months

    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    sections: List[str] = []

    for year, month in month_iterator(start, end):
        weeks = calendar.monthcalendar(year, month)  # weeks start on Monday
        week_rows: List[str] = []
        for week in weeks:
            cells: List[str] = []
            for idx, day in enumerate(week):
                if day == 0:
                    cells.append("<td class='empty'></td>")
                    continue
                current = date(year, month, day)
                classes = ["day"]
                if current < start or current > end:
                    classes.append("muted")
                hours = day_totals.get(current)
                if hours:
                    classes.append("has-hours")
                    content = (
                        f"<span class='date'>{day}</span>"
                        f"<strong>{format_hours(hours)}</strong>"
                    )
                else:
                    content = (
                        f"<span class='date'>{day}</span>"
                        "<span class='muted'>—</span>"
                    )
                class_attr = " ".join(classes)
                cells.append(f"<td class='{class_attr}'>{content}</td>")
            week_rows.append("<tr>" + "".join(cells) + "</tr>")

        sections.append(
            "<section class='calendar'>"
            f"<h2>{calendar.month_name[month]} {year}</h2>"
            "<table>"
            "<thead><tr>"
            + "".join(f"<th>{label}</th>" for label in weekday_labels)
            + "</tr></thead>"
            "<tbody>"
            + "".join(week_rows)
            + "</tbody>"
            "</table>"
            "</section>"
        )

    return "".join(sections)


def fetch_usd_to_idr_rate() -> Tuple[Optional[float], Optional[str]]:
    """Fetch USD→IDR conversion using a public API."""

    url = "https://open.er-api.com/v6/latest/USD"
    headers = {"User-Agent": "time-logger-cli/1.0"}
    try:
        req = request.Request(url, headers=headers)
        with request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"Warning: exchange lookup failed ({exc}).")
        return None, None

    if data.get("result") != "success":
        return None, None
    rates = data.get("rates") or {}
    rate = rates.get("IDR")
    timestamp = data.get("time_last_update_utc")
    if isinstance(rate, (int, float)):
        return float(rate), timestamp
    return None, timestamp


def format_currency(amount: Optional[float], currency: str) -> str:
    if amount is None:
        return "N/A"
    if currency == "USD":
        return f"${amount:,.2f}"
    if currency == "IDR":
        return f"Rp{amount:,.0f}"
    return f"{amount:,.2f} {currency}"


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def print_table(headers: List[str], rows: List[List[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def format_row(row: Iterable[str]) -> str:
        return "  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))

    print(format_row(headers))
    print(format_row(["-" * w for w in widths]))
    for row in rows:
        print(format_row(row))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track hours and create reports")
    parser.add_argument(
        "--data-file",
        type=Path,
        default=DEFAULT_DATA_FILE,
        help="Path to the CSV data store (default: time_entries.csv next to the script)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new time entry")
    add_parser.add_argument("--date", help="YYYY-MM-DD (defaults to today)")
    add_parser.add_argument("--hours", required=True, help="Number of hours, e.g. 1.5")
    add_parser.add_argument("--desc", required=True, help="Short description")
    add_parser.set_defaults(func=cli_add)

    list_parser = subparsers.add_parser("list", help="Show saved entries")
    list_parser.add_argument("--date", help="Only show a specific date (YYYY-MM-DD)")
    list_parser.add_argument("--start-date", help="Filter entries on/after this date")
    list_parser.add_argument("--end-date", help="Filter entries on/before this date")
    list_parser.set_defaults(func=cli_list)

    report_parser = subparsers.add_parser("report", help="Build weekly, monthly, or custom HTML")
    report_sub = report_parser.add_subparsers(dest="frequency", required=True)

    weekly = report_sub.add_parser("weekly", help="Report on ISO week")
    weekly.add_argument("--year", type=int, required=True)
    weekly.add_argument("--week", type=int, required=True, help="ISO week number (1-53)")
    weekly.add_argument("--output", help="Target HTML path")
    weekly.set_defaults(func=cli_report)

    monthly = report_sub.add_parser("monthly", help="Report on calendar month")
    monthly.add_argument("--year", type=int, required=True)
    monthly.add_argument("--month", type=int, required=True, choices=range(1, 13))
    monthly.add_argument("--output", help="Target HTML path")
    monthly.set_defaults(func=cli_report)

    custom = report_sub.add_parser("range", help="Report on an explicit date range")
    custom.add_argument("--start-date", required=True, help="YYYY-MM-DD or keyword (today, ...)")
    custom.add_argument(
        "--end-date",
        required=False,
        help="YYYY-MM-DD or keyword (defaults to today when omitted)",
    )
    custom.add_argument("--output", help="Target HTML path")
    custom.set_defaults(func=cli_report)

    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

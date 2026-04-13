# Time Logger CLI

Simple command line utility to record daily hours into a CSV text file and generate HTML timesheet reports weekly or monthly.

## Quick start

```bash
python3 time_logger.py add --hours 2.5 --desc "Deep work" --date today
python3 time_logger.py list --start-date 2026-04-01 --end-date 2026-04-07
python3 time_logger.py report weekly --year 2026 --week 15
python3 time_logger.py report monthly --year 2026 --month 4 --output april.html
python3 time_logger.py report range --start-date 2026-03-25 --end-date 2026-04-05
python3 time_logger.py rate --set 25         # update hourly USD rate
```

- Data is stored in `time_entries.csv` next to the script; edit it manually if desired.
- CSV columns: `date,hours,description` (hours stored as decimal numbers).
- Date inputs accept `today`, `yesterday`, or `tomorrow` shortcuts in addition to `YYYY-MM-DD`.
- Hourly rate is stored in `billing_rate.txt` (next to the script) and defaults to USD $23/h; change it at any time with `python3 time_logger.py rate --set <amount>`.
- Reports render to `reports/` unless you pass `--output`, and you can generate weekly, monthly, or arbitrary date-range HTML (the custom range defaults `--end-date` to today if omitted). Report pages now include a calendar view plus a revenue summary using the stored USD rate with live USD→IDR conversion.

Run `python3 time_logger.py -h` for the complete CLI reference.

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from icalendar import Calendar, Event
import requests

from datetime import datetime, timedelta, UTC, date
import argparse
import json
import logging
import re
import xml.etree.ElementTree as ET


CALENDAR_URL = "https://raw.githubusercontent.com/xmlcalendar/data/master/ru/{year}/calendar.xml"

DAY_OFF, SHORTENED, WORKING = "1", "2", "3"


def fetch_calendar(year: int) -> bytes | None:
    url = CALENDAR_URL.format(year=year)
    logging.info(url)

    r = requests.get(url, timeout=20)

    if r.status_code == 404:
        logging.warning("No calendar for year %d (404)", year)
        return None

    r.raise_for_status()
    return r.content


def classify_day(node: ET.Element) -> str | None:
    day_type = node.get("t")

    if day_type == DAY_OFF:
        return "holiday" if node.get("h") else "dayoff"

    if day_type == SHORTENED:
        return "shortened"

    if day_type == WORKING:
        return None

    raise RuntimeError(f"Unknown day type {day_type!r} on {node.get('d')}")


def parse_calendar(year: int, content: bytes) -> list[dict[int, str]]:
    root = ET.fromstring(content)

    if root.get("year") != str(year):
        raise RuntimeError(f"Calendar for year {year} declares year {root.get('year')!r}")

    listed: dict[date, str | None] = {}
    for node in root.iterfind("days/day"):
        month, day = (int(part) for part in node.attrib["d"].split("."))
        listed[date(year, month, day)] = classify_day(node)

    months: list[dict[int, str]] = [{} for _ in range(12)]
    day = date(year, 1, 1)
    while day.year == year:
        kind = listed[day] if day in listed else ("dayoff" if day.weekday() >= 5 else None)
        if kind:
            months[day.month - 1][day.day] = kind
        day += timedelta(days=1)

    return months


def parse_year(year: int) -> list[dict] | None:
    content = fetch_calendar(year)
    if content is None:
        return None

    months = parse_calendar(year, content)
    logging.info("Parsed calendar for year %d", year)
    return months


def build_non_working_dates(months_by_year: dict[int, list[dict]]) -> set[date]:
    """
    Treat 'holiday' and 'dayoff' as non-working.
    'shortened' is still working.

    IMPORTANT: We intentionally do NOT infer weekends by weekday(),
    because Russia has "working Saturdays" (moved workdays) and also
    moved days off. The production calendar already encodes this.
    """
    non_working: set[date] = set()

    for y, months in months_by_year.items():
        for m, days_map in enumerate(months, start=1):
            for d, kind in days_map.items():
                if kind in ("holiday", "dayoff"):
                    non_working.add(date(y, m, d))

    return non_working


def is_working_day(d: date, non_working: set[date], covered_years: set[int]) -> bool:
    """
    If we have production calendar coverage for the year, trust it fully:
      working day  <=>  not in non_working set
    This correctly handles moved working Saturdays.

    If we don't have coverage for the year (fallback), we assume:
      weekend (Sat/Sun) is non-working, plus any explicitly known non_working.
    """
    if d.year in covered_years:
        return d not in non_working

    # Fallback (should be rare if you parse start_year-1..end_year)
    if d.weekday() >= 5:
        return False
    return d not in non_working


def shift_to_previous_working_day(d: date, non_working: set[date], covered_years: set[int]) -> date:
    while not is_working_day(d, non_working, covered_years):
        d -= timedelta(days=1)
    return d


def parse_rules(rules_str: str) -> list[tuple[int, str]]:
    """
    Format: "5:Salary;20:Mid-month pay"
    - separator between rules: ';'
    - day and label: ':'
    """
    rules_str = (rules_str or "").strip()
    if not rules_str:
        raise ValueError("rules string is empty")

    rules: list[tuple[int, str]] = []
    parts = [p.strip() for p in rules_str.split(";") if p.strip()]

    for part in parts:
        if ":" not in part:
            raise ValueError(f"Invalid rule '{part}'. Expected format DAY:LABEL")

        day_s, label = part.split(":", 1)
        day_s = day_s.strip()
        label = label.strip()

        if not day_s.isdigit():
            raise ValueError(f"Invalid day '{day_s}' in rule '{part}'")

        day = int(day_s)
        if day < 1 or day > 31:
            raise ValueError(f"Day out of range in rule '{part}'")

        if not label:
            raise ValueError(f"Empty label in rule '{part}'")

        rules.append((day, label))

    rules.sort(key=lambda x: x[0])
    return rules


def make_salary_event(base_year: int, base_month: int, base_day: int, actual_day: date, summary: str) -> Event:
    e = Event()
    e.add("summary", summary)

    # all-day
    e.add("dtstart", actual_day)
    e.add("dtend", actual_day + timedelta(days=1))

    e.add("dtstamp", datetime.now(UTC))

    # Stable UID tied to intended payday day-of-month, not shifted date
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", summary).strip("-").lower() or "event"
    e.add("uid", f"ru-salary-{base_year}{base_month:02d}{base_day:02d}-{safe}@salary-ics")

    base_date = date(base_year, base_month, base_day)
    if actual_day != base_date:
        e.add("description", f"Shifted from {base_date.isoformat()} to {actual_day.isoformat()}.")

    return e


def generate_salary_events(
    year: int,
    non_working: set[date],
    covered_years: set[int],
    rules: list[tuple[int, str]],
) -> list[Event]:
    events: list[Event] = []

    for month in range(1, 13):
        for day, label in rules:
            # if day doesn't exist in month (e.g. 31 in Feb) -> skip
            try:
                base = date(year, month, day)
            except ValueError:
                continue

            actual = shift_to_previous_working_day(base, non_working, covered_years)
            events.append(make_salary_event(year, month, day, actual, label))

    return events


def build_calendar(events: list[Event], calname: str) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//ru-salary-ics//Salary Calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("NAME", calname)
    cal.add("X-WR-CALNAME", calname)

    for e in sorted(events, key=lambda x: x.decoded("dtstart")):
        cal.add_component(e)

    return cal


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-year", type=int, default=datetime.today().year)
    p.add_argument("--end-year", type=int, default=datetime.today().year + 1)
    p.add_argument("-o", default="salary.ics")
    p.add_argument("--years-out", help="Write the years covered by the calendar to this file as a JSON list")
    p.add_argument("--log-level", default="INFO")

    p.add_argument(
        "--rules",
        default="5:Salary;20:Mid-month pay",
        help='Rules in format "DAY:LABEL;DAY:LABEL". Example: "5:Salary;20:Mid-month pay"',
    )
    p.add_argument(
        "--calendar-name",
        default="Salary (RU, shifted to working day)",
        help="Calendar display name",
    )

    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    rules = parse_rules(args.rules)

    # Parse one extra year back so Jan paydays can shift into previous Dec safely
    parse_from = args.start_year - 1
    parse_to = args.end_year

    months_by_year: dict[int, list[dict]] = {}

    for year in range(parse_from, parse_to + 1):
        months = parse_year(year)
        if not months:
            logging.info("Stopping at year %d (no data yet)", year)
            break
        months_by_year[year] = months

    covered_years = set(months_by_year.keys())
    non_working = build_non_working_dates(months_by_year)

    events: list[Event] = []
    years: list[int] = []
    for year in range(args.start_year, args.end_year + 1):
        if year not in months_by_year:
            break
        events.extend(generate_salary_events(year, non_working, covered_years, rules))
        years.append(year)

    cal = build_calendar(events, args.calendar_name)

    with open(args.o, "wb") as f:
        f.write(cal.to_ical())

    if args.years_out:
        with open(args.years_out, "w", encoding="utf-8") as f:
            json.dump(years, f)


if __name__ == "__main__":
    main()

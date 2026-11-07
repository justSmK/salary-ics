import unittest
from collections import Counter
from datetime import date
from unittest import mock

import salary_ics


CALENDAR_2027 = b"""<?xml version="1.0" encoding="UTF-8"?>
<calendar year="2027" lang="ru" date="2026.09.30" country="ru">
    <holidays>
        <holiday id="1" title="New Year holidays"/>
        <holiday id="2" title="Christmas"/>
        <holiday id="3" title="Defender of the Fatherland Day"/>
        <holiday id="4" title="International Women's Day"/>
        <holiday id="5" title="Spring and Labour Day"/>
        <holiday id="6" title="Victory Day"/>
        <holiday id="7" title="Russia Day"/>
        <holiday id="8" title="Unity Day"/>
    </holidays>
    <days>
        <day d="01.01" t="1" h="1"/>
        <day d="01.02" t="1" h="1"/>
        <day d="01.03" t="1" h="1"/>
        <day d="01.04" t="1" h="1"/>
        <day d="01.05" t="1" h="1"/>
        <day d="01.06" t="1" h="1"/>
        <day d="01.07" t="1" h="2"/>
        <day d="01.08" t="1" h="1"/>
        <day d="02.20" t="2"/>
        <day d="02.22" t="1" f="02.20"/>
        <day d="02.23" t="1" h="3"/>
        <day d="03.08" t="1" h="4"/>
        <day d="04.30" t="2"/>
        <day d="05.01" t="1" h="5"/>
        <day d="05.03" t="1" f="05.01"/>
        <day d="05.09" t="1" h="6"/>
        <day d="05.10" t="1" f="05.09"/>
        <day d="06.11" t="2"/>
        <day d="06.12" t="1" h="7"/>
        <day d="06.14" t="1" f="06.12"/>
        <day d="11.03" t="2"/>
        <day d="11.04" t="1" h="8"/>
        <day d="11.05" t="1" f="01.02"/>
        <day d="12.31" t="1" f="01.03"/>
    </days>
</calendar>
"""


def calendar_2027_with(old: bytes, new: bytes) -> bytes:
    assert old in CALENDAR_2027
    return CALENDAR_2027.replace(old, new)


class ParseCalendarTests(unittest.TestCase):
    def test_2027_calendar_maps_holidays_days_off_and_shortened_days(self):
        months = salary_ics.parse_calendar(2027, CALENDAR_2027)

        self.assertEqual(len(months), 12)
        for day in range(1, 9):
            self.assertEqual(months[0][day], "holiday")
        self.assertEqual(months[0][9], "dayoff")
        self.assertEqual(months[0][10], "dayoff")
        self.assertEqual(months[1][20], "shortened")
        self.assertEqual(months[1][21], "dayoff")
        self.assertEqual(months[1][22], "dayoff")
        self.assertEqual(months[1][23], "holiday")
        self.assertEqual(months[4][1], "holiday")
        self.assertEqual(months[4][3], "dayoff")
        self.assertEqual(months[10][3], "shortened")
        self.assertEqual(months[10][5], "dayoff")
        self.assertNotIn(30, months[11])
        self.assertEqual(months[11][31], "dayoff")

        kinds = Counter(kind for month in months for kind in month.values())
        self.assertEqual(kinds, {"holiday": 14, "dayoff": 104, "shortened": 4})

    def test_rejects_a_file_published_for_another_year(self):
        content = calendar_2027_with(b'year="2027"', b'year="2026"')

        with self.assertRaises(RuntimeError):
            salary_ics.parse_calendar(2027, content)

    def test_rejects_an_unknown_day_type(self):
        content = calendar_2027_with(b'<day d="04.30" t="2"/>', b'<day d="04.30" t="4"/>')

        with self.assertRaises(RuntimeError):
            salary_ics.parse_calendar(2027, content)


class ParseYearTests(unittest.TestCase):
    def test_unpublished_year_returns_none(self):
        response = mock.Mock(status_code=404)

        with mock.patch.object(salary_ics.requests, "get", return_value=response) as get:
            self.assertIsNone(salary_ics.parse_year(2028))

        get.assert_called_once()
        self.assertEqual(
            get.call_args.args[0],
            "https://raw.githubusercontent.com/xmlcalendar/data/master/ru/2028/calendar.xml",
        )


class SalaryEventTests(unittest.TestCase):
    def paydays_2027(self) -> dict[str, date]:
        months = salary_ics.parse_calendar(2027, CALENDAR_2027)
        non_working = salary_ics.build_non_working_dates({2027: months})
        rules = [(5, "Salary"), (20, "Mid-month pay")]
        events = salary_ics.generate_salary_events(2027, non_working, {2027}, rules)
        return {str(e["UID"]): e.decoded("DTSTART") for e in events}

    def test_payday_on_a_working_saturday_is_not_moved(self):
        paydays = self.paydays_2027()

        self.assertEqual(paydays["ru-salary-20270220-mid-month-pay@salary-ics"], date(2027, 2, 20))

    def test_payday_on_a_transferred_day_off_moves_to_the_previous_working_day(self):
        paydays = self.paydays_2027()

        self.assertEqual(paydays["ru-salary-20271105-salary@salary-ics"], date(2027, 11, 3))


if __name__ == "__main__":
    unittest.main()

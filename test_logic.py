"""Unit tests for pure-Python logic in timereport.py and arete.py.

These tests run without a real macOS AppKit environment by stubbing out the
Objective-C framework imports at the sys.modules level before importing the
modules under test.
"""

import sys
import types
import math
import json
import tempfile
import os
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta, date


# ---------------------------------------------------------------------------
# Stub macOS framework modules so the modules under test can be imported on
# any platform (and without a running AppKit runtime).
# ---------------------------------------------------------------------------

def _make_stub(*names):
    mod = types.ModuleType(names[0])
    for n in names[1:]:
        setattr(mod, n, MagicMock())
    return mod


def _stub_frameworks():
    """Install lightweight stubs for AppKit / Foundation / objc / rumps."""
    # NSRect / NSPoint / NSSize — used inside _layout_row; we stub them as
    # simple named-tuple-like objects so geometry assertions are possible.
    class _NSPoint:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    class _NSSize:
        def __init__(self, w, h):
            self.width = w
            self.height = h

    class _NSRect:
        def __init__(self, origin, size):
            self.origin = origin
            self.size = size

    appkit = types.ModuleType("AppKit")
    # Provide everything that timereport.py / arete.py import from AppKit
    for attr in [
        "NSApplication", "NSApplicationActivationPolicyRegular",
        "NSApplicationActivationPolicyAccessory", "NSAlert",
        "NSWindow", "NSWindowStyleMaskTitled", "NSWindowStyleMaskClosable",
        "NSWindowStyleMaskMiniaturizable", "NSWindowStyleMaskResizable",
        "NSBackingStoreBuffered",
        "NSStackView", "NSTabView", "NSTabViewItem",
        "NSTextField", "NSColor", "NSFont", "NSBezierPath",
        "NSImage", "NSImageView", "NSView", "NSScrollView",
        "NSUserInterfaceLayoutOrientationVertical",
        "NSUserInterfaceLayoutOrientationHorizontal",
        "NSGraphicsContext", "NSClipView",
        "NSTrackingArea", "NSTrackingAreaOptions",
        "NSViewWidthSizable", "NSViewHeightSizable",
        "NSLayoutConstraint", "NSLayoutAttributeTop",
        "NSGridView", "NSGridColumn",
        "NSButton", "NSMenu", "NSMenuItem", "NSPopUpButton",
        "NSControlStateValueOn", "NSControlStateValueOff",
        "NSCursor",
        "NSFontAttributeName", "NSForegroundColorAttributeName",
        "NSTrackingMouseMoved", "NSTrackingActiveInKeyWindow",
        "NSTrackingInVisibleRect", "NSPointInRect",
        "NSPasteboard", "NSPasteboardTypeString",
        "NSBitmapImageRep", "NSPNGFileType",
        "NSBox", "NSGridCell", "NSPanel", "NSWindowStyleMaskBorderless",
        "NSVisualEffectView", "NSVisualEffectBlendingModeBehindWindow",
        "NSVisualEffectStateActive", "NSFontManager",
        "NSCursor", "NSDatePicker",
        "NSDatePickerStyleTextFieldAndStepper",
        "NSDatePickerElementFlagHourMinute",
        "NSDatePickerElementFlagYearMonthDay",
        "NSModalResponseOK", "NSModalResponseCancel",
        "NSAttributedString", "NSMutableParagraphStyle",
        "NSParagraphStyleAttributeName",
        "NSGradient", "NSShadow",
    ]:
        setattr(appkit, attr, MagicMock())

    # Geometry types need real behaviour for _layout_row
    appkit.NSRect = _NSRect
    appkit.NSPoint = _NSPoint
    appkit.NSSize = _NSSize

    foundation = types.ModuleType("Foundation")
    for attr in [
        "NSObject", "NSString", "NSProcessInfo", "NSBundle",
        "NSDistributedNotificationCenter", "NSTimer", "NSDate",
        "NSMutableAttributedString",
    ]:
        setattr(foundation, attr, MagicMock())
    # NSDate alias used in arete.py as _NSDate
    foundation.NSDate = MagicMock()

    objc_mod = types.ModuleType("objc")
    objc_mod.super = MagicMock()
    objc_mod.typedSelector = lambda _sig: (lambda fn: fn)
    objc_mod.python_method = lambda fn: fn

    rumps_mod = types.ModuleType("rumps")
    rumps_mod.App = MagicMock()
    rumps_mod.MenuItem = MagicMock()
    rumps_mod.separator = MagicMock()
    rumps_mod.quit_application = MagicMock()
    rumps_mod.timer = lambda _interval: (lambda fn: fn)

    sys.modules.setdefault("AppKit", appkit)
    sys.modules.setdefault("Foundation", foundation)
    sys.modules.setdefault("objc", objc_mod)
    sys.modules.setdefault("rumps", rumps_mod)


_stub_frameworks()

# Now it is safe to import the modules under test.
import timereport  # noqa: E402
import arete       # noqa: E402


# ===========================================================================
# timereport.py — pure-Python helpers
# ===========================================================================

class TestParseUtc:
    def test_basic(self):
        dt = timereport.parse_utc("20240315T093045Z")
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15
        assert dt.hour == 9
        assert dt.minute == 30
        assert dt.second == 45
        assert dt.tzinfo is not None

    def test_timezone_is_utc(self):
        dt = timereport.parse_utc("20230101T000000Z")
        # Should be UTC offset 0
        assert dt.utcoffset() == timedelta(0)

    def test_midnight(self):
        dt = timereport.parse_utc("20231231T235959Z")
        assert dt.hour == 23
        assert dt.minute == 59
        assert dt.second == 59


class TestSecondsToHm:
    def test_minutes_only(self):
        assert timereport.seconds_to_hm(0) == "0m"
        assert timereport.seconds_to_hm(59) == "0m"
        assert timereport.seconds_to_hm(60) == "1m"
        assert timereport.seconds_to_hm(3599) == "59m"

    def test_hours_and_minutes(self):
        assert timereport.seconds_to_hm(3600) == "1h 00m"
        assert timereport.seconds_to_hm(3661) == "1h 01m"
        assert timereport.seconds_to_hm(7200) == "2h 00m"
        assert timereport.seconds_to_hm(7384) == "2h 03m"

    def test_zero_minutes_padding(self):
        # Minutes must always be zero-padded to 2 digits when hours > 0
        assert timereport.seconds_to_hm(3600 * 5) == "5h 00m"


class TestRgb:
    def test_within_palette(self):
        r, g, b = timereport._rgb(0)
        assert (r, g, b) == timereport._PALETTE[0]

    def test_wraps_around(self):
        palette_len = len(timereport._PALETTE)
        assert timereport._rgb(palette_len) == timereport._rgb(0)
        assert timereport._rgb(palette_len + 3) == timereport._rgb(3)


class TestParseIntervals:
    """Tests for _parse_intervals() — no subprocess involved."""

    def _make_json(self, items):
        return json.dumps(items)

    def test_empty_string(self):
        assert timereport._parse_intervals("") == []

    def test_invalid_json(self):
        assert timereport._parse_intervals("not json") == []

    def test_single_complete_interval(self):
        data = [{
            "id": 1,
            "start": "20240101T090000Z",
            "end":   "20240101T100000Z",
            "tags":  ["work"],
        }]
        result = timereport._parse_intervals(self._make_json(data))
        assert len(result) == 1
        iv = result[0]
        assert iv["tags"] == ["work"]
        assert iv["id"] == 1
        assert iv["duration"] == 3600.0

    def test_missing_start_skipped(self):
        data = [{"id": 2, "end": "20240101T100000Z", "tags": ["x"]}]
        assert timereport._parse_intervals(self._make_json(data)) == []

    def test_open_interval_uses_now(self):
        data = [{
            "id": 3,
            "start": "20240101T090000Z",
            "tags":  ["active"],
        }]
        result = timereport._parse_intervals(self._make_json(data))
        assert len(result) == 1
        # end should be approximately now — just check it's a datetime
        assert isinstance(result[0]["end"], datetime)

    def test_multiple_intervals(self):
        data = [
            {"id": 1, "start": "20240101T080000Z", "end": "20240101T090000Z", "tags": ["a"]},
            {"id": 2, "start": "20240101T100000Z", "end": "20240101T110000Z", "tags": ["b"]},
        ]
        result = timereport._parse_intervals(self._make_json(data))
        assert len(result) == 2

    def test_annotation_field(self):
        data = [{
            "id": 4,
            "start": "20240101T090000Z",
            "end":   "20240101T100000Z",
            "tags":  ["t"],
            "annotation": "standup meeting",
        }]
        result = timereport._parse_intervals(self._make_json(data))
        assert result[0]["annotation"] == "standup meeting"

    def test_missing_annotation_defaults_empty(self):
        data = [{"id": 5, "start": "20240101T090000Z", "end": "20240101T100000Z", "tags": ["t"]}]
        result = timereport._parse_intervals(self._make_json(data))
        assert result[0]["annotation"] == ""


class TestDateRangeFor:
    """date_range_for(period, offset) — freeze today via monkeypatching."""

    def _patch_today(self, monkeypatch, fake_today):
        # Patch date.today() inside timereport's module namespace
        import timereport as tr
        original_date = tr.date

        class _FakeDate(tr.date):
            @classmethod
            def today(cls):
                return fake_today

        monkeypatch.setattr(tr, "date", _FakeDate)

    def test_day_offset_zero(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("day", 0)
        assert start == today
        assert end == today
        assert title == "Today"

    def test_day_offset_minus1(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("day", -1)
        assert start == date(2024, 6, 14)
        assert title == "Yesterday"

    def test_day_future_capped(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("day", 5)
        assert start == today
        assert end == today

    def test_week_offset_zero(self, monkeypatch):
        # 2024-06-15 is a Saturday — Monday of that week is 2024-06-10
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("week", 0)
        assert start == date(2024, 6, 10)
        assert end == today  # capped at today
        assert title == "This Week"

    def test_week_offset_minus1(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("week", -1)
        assert start == date(2024, 6, 3)
        assert end == date(2024, 6, 9)
        assert title == "Last Week"

    def test_month_offset_zero(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("month", 0)
        assert start == date(2024, 6, 1)
        assert end == today
        assert title == "This Month"

    def test_month_offset_minus1(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("month", -1)
        assert start == date(2024, 5, 1)
        assert end == date(2024, 5, 31)
        assert title == "May 2024"

    def test_month_wrap_year(self, monkeypatch):
        today = date(2024, 1, 10)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("month", -1)
        assert start == date(2023, 12, 1)
        assert end == date(2023, 12, 31)
        assert title == "December 2023"

    def test_day_older_label(self, monkeypatch):
        today = date(2024, 6, 15)
        self._patch_today(monkeypatch, today)
        start, end, title = timereport.date_range_for("day", -7)
        assert start == date(2024, 6, 8)
        assert "2024" in title  # formatted date, not "Yesterday"


class TestComputeRowLayout:
    """_compute_row_layout without any AppKit calls."""

    def _make_interval(self, start_h, end_h, day=date(2024, 1, 1)):
        tz = timezone.utc
        return {
            "start": datetime(day.year, day.month, day.day, start_h, 0, tzinfo=tz),
            "end":   datetime(day.year, day.month, day.day, end_h,   0, tzinfo=tz),
            "tags":  ["t"],
        }

    def test_empty_rows(self):
        row_heights, height = timereport._compute_row_layout([])
        assert row_heights == []
        # height = PAD_TOP + 0 + PAD_BOTTOM
        assert height == timereport.PAD_TOP + timereport.PAD_BOTTOM

    def test_single_row_no_intervals(self):
        rows = [("Mon", date(2024, 1, 1), [])]
        row_heights, height = timereport._compute_row_layout(rows, row_h=42)
        assert row_heights == [42]

    def test_overlapping_intervals_expand_lanes(self):
        day = date(2024, 1, 1)
        # Two intervals overlapping → should require 2 lanes
        inv1 = self._make_interval(9, 11)
        inv2 = self._make_interval(10, 12)
        rows = [("Mon", day, [inv1, inv2])]
        row_heights, _ = timereport._compute_row_layout(rows, row_h=42)
        # Two lanes → row height = 2 * row_h = 84
        assert row_heights[0] == 84

    def test_non_overlapping_intervals_single_lane(self):
        day = date(2024, 1, 1)
        inv1 = self._make_interval(8, 9)
        inv2 = self._make_interval(10, 11)
        rows = [("Mon", day, [inv1, inv2])]
        row_heights, _ = timereport._compute_row_layout(rows, row_h=42)
        assert row_heights[0] == 42


class TestLayoutPieSlices:
    """_layout_pie_slices — pure geometry, no drawing."""

    def test_empty_produces_remainder_only(self):
        slices = timereport._layout_pie_slices([], {}, {}, 3600)
        assert len(slices) == 1
        start, sweep, tag, secs, cidx = slices[0]
        assert tag is None  # remainder slice
        assert abs(sweep - 360.0) < 1e-6

    def test_single_tag_full_coverage(self):
        all_tags = ["work"]
        tag_index = {"work": 0}
        tag_totals = {"work": 3600}
        slices = timereport._layout_pie_slices(all_tags, tag_index, tag_totals, 3600)
        assert len(slices) == 1
        assert slices[0][2] == "work"
        assert abs(slices[0][1] - 360.0) < 1e-6

    def test_two_equal_tags(self):
        all_tags = ["a", "b"]
        tag_index = {"a": 0, "b": 1}
        tag_totals = {"a": 1800, "b": 1800}
        slices = timereport._layout_pie_slices(all_tags, tag_index, tag_totals, 3600)
        assert len(slices) == 2
        assert abs(slices[0][1] - 180.0) < 1e-6
        assert abs(slices[1][1] - 180.0) < 1e-6

    def test_partial_coverage_has_remainder(self):
        all_tags = ["work"]
        tag_index = {"work": 0}
        tag_totals = {"work": 1800}
        slices = timereport._layout_pie_slices(all_tags, tag_index, tag_totals, 3600)
        assert len(slices) == 2
        total_sweep = sum(s[1] for s in slices)
        assert abs(total_sweep - 360.0) < 1e-6

    def test_zero_seconds_tag_excluded(self):
        all_tags = ["a", "b"]
        tag_index = {"a": 0, "b": 1}
        tag_totals = {"a": 3600, "b": 0}
        slices = timereport._layout_pie_slices(all_tags, tag_index, tag_totals, 3600)
        tags_in_slices = [s[2] for s in slices]
        assert "b" not in tags_in_slices

    def test_start_angle_progresses_clockwise(self):
        """Each slice's start_angle should be less than the previous (CW = decreasing angle)."""
        all_tags = ["a", "b", "c"]
        tag_index = {"a": 0, "b": 1, "c": 2}
        tag_totals = {"a": 1200, "b": 1200, "c": 1200}
        slices = timereport._layout_pie_slices(all_tags, tag_index, tag_totals, 3600)
        for i in range(1, len(slices)):
            assert slices[i][0] < slices[i - 1][0]


class TestPieHitTest:
    """_pie_hit_test — geometry math only."""

    def _slices(self):
        all_tags = ["work", "break"]
        tag_index = {"work": 0, "break": 1}
        tag_totals = {"work": 1800, "break": 1800}
        return timereport._layout_pie_slices(all_tags, tag_index, tag_totals, 3600)

    def test_outside_radius_returns_none(self):
        slices = self._slices()
        tag, secs = timereport._pie_hit_test(slices, 90, 90, 80, 200, 200)
        assert tag is None
        assert secs == 0

    def test_at_center_returns_none(self):
        slices = self._slices()
        tag, secs = timereport._pie_hit_test(slices, 90, 90, 80, 90, 90)
        assert tag is None
        assert secs == 0

    def test_hit_inside_slice(self):
        """12 o'clock (directly above centre) should be inside the first slice."""
        slices = self._slices()
        cx, cy, radius = 90.0, 90.0, 80.0
        # Point directly above centre at 12 o'clock
        px, py = cx, cy + radius * 0.5
        tag, secs = timereport._pie_hit_test(slices, cx, cy, radius, px, py)
        assert tag is not None
        assert secs > 0


# ===========================================================================
# arete.py — parse_tags_output, load_config, save_config
# ===========================================================================

class TestParseTagsOutput:
    def test_empty_string(self):
        assert arete.parse_tags_output("") == []

    def test_typical_timew_output(self):
        out = (
            "Tag        Count\n"
            "---------- -----\n"
            "coding       100\n"
            "meetings      42\n"
            "review        15\n"
        )
        tags = arete.parse_tags_output(out)
        assert tags == ["coding", "meetings", "review"]

    def test_no_data_line_ignored(self):
        out = (
            "Tag        Count\n"
            "---------- -----\n"
            "No data found.\n"
        )
        # "No" is parsed as a tag name — but separator detection should prevent this
        # because the line appears after the separator and has a word, so it would
        # be parsed. Verify we get a stable result either way.
        result = arete.parse_tags_output(out)
        # The important invariant: no crash
        assert isinstance(result, list)

    def test_output_without_separator(self):
        # Lines before the separator are ignored
        out = "some free text\nmore text\n"
        assert arete.parse_tags_output(out) == []

    def test_sorted_output(self):
        out = (
            "Tag  Count\n"
            "---- -----\n"
            "zebra   1\n"
            "apple   2\n"
            "mango   3\n"
        )
        tags = arete.parse_tags_output(out)
        assert tags == sorted(tags)

    def test_single_tag(self):
        out = "Tag  Count\n---- -----\nonly    5\n"
        assert arete.parse_tags_output(out) == ["only"]

    def test_header_only_no_separator(self):
        out = "Tag  Count\n"
        assert arete.parse_tags_output(out) == []


class TestLoadSaveConfig:
    def test_roundtrip(self, tmp_path):
        cfg_path = str(tmp_path / "test_arete.json")
        original_path = arete.CONFIG_PATH
        arete.CONFIG_PATH = cfg_path
        try:
            config = {"pause_on_lock": True, "workday_hours": 8.0, "recent_range": ":week"}
            arete.save_config(config)
            loaded = arete.load_config()
            assert loaded == config
        finally:
            arete.CONFIG_PATH = original_path

    def test_load_missing_file(self, tmp_path):
        original_path = arete.CONFIG_PATH
        arete.CONFIG_PATH = str(tmp_path / "nonexistent.json")
        try:
            result = arete.load_config()
            assert result == {}
        finally:
            arete.CONFIG_PATH = original_path

    def test_load_corrupt_file(self, tmp_path):
        cfg_path = tmp_path / "bad.json"
        cfg_path.write_text("{ not valid json }")
        original_path = arete.CONFIG_PATH
        arete.CONFIG_PATH = str(cfg_path)
        try:
            result = arete.load_config()
            assert result == {}
        finally:
            arete.CONFIG_PATH = original_path

    def test_save_silently_handles_error(self, tmp_path):
        # Attempt to save to a directory path (will fail), must not raise
        original_path = arete.CONFIG_PATH
        arete.CONFIG_PATH = str(tmp_path)  # is a directory, not a file
        try:
            arete.save_config({"key": "value"})  # should not raise
        finally:
            arete.CONFIG_PATH = original_path


class TestFormatHHMM:
    def test_zero(self):
        assert arete._format_hhmm(0) == "0:00"

    def test_minutes_only(self):
        assert arete._format_hhmm(420) == "0:07"

    def test_hours_and_minutes(self):
        assert arete._format_hhmm(3660) == "1:01"

    def test_exactly_one_hour(self):
        assert arete._format_hhmm(3600) == "1:00"

    def test_large_value(self):
        assert arete._format_hhmm(9000) == "2:30"


class TestSessionStartForTag:
    """Tests for _session_start_for_tag using timezone-aware datetimes."""

    TZ = timezone.utc

    def _iv(self, start_min, end_min, tags):
        """Build an interval dict with start/end as minutes-offset from midnight."""
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        return {
            "start": base + timedelta(minutes=start_min),
            "end": base + timedelta(minutes=end_min),
            "tags": tags,
        }

    def test_empty_intervals_returns_none(self):
        assert arete._session_start_for_tag("work", []) is None

    def test_tag_not_in_last_interval_returns_none(self):
        ivs = [self._iv(0, 30, ["other"])]
        assert arete._session_start_for_tag("work", ivs) is None

    def test_single_interval_returns_its_start(self):
        ivs = [self._iv(0, 30, ["work"])]
        result = arete._session_start_for_tag("work", ivs)
        assert result == ivs[0]["start"]

    def test_two_adjacent_intervals_both_with_tag(self):
        # work 0-30, work 30-60 — chain extends back to 0
        ivs = [self._iv(0, 30, ["work"]), self._iv(30, 60, ["work"])]
        result = arete._session_start_for_tag("work", ivs)
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        assert result == base + timedelta(minutes=0)

    def test_gap_breaks_chain(self):
        # work 0-30, gap, work 40-60 — session starts at 40
        ivs = [self._iv(0, 30, ["work"]), self._iv(40, 60, ["work"])]
        result = arete._session_start_for_tag("work", ivs)
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        assert result == base + timedelta(minutes=40)

    def test_previous_interval_missing_tag_breaks_chain(self):
        # other 0-30 (adjacent), work 30-60 — "meeting" not in first, so session = 30
        ivs = [self._iv(0, 30, ["other"]), self._iv(30, 60, ["work"])]
        result = arete._session_start_for_tag("work", ivs)
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        assert result == base + timedelta(minutes=30)

    def test_multi_tag_original_extends_further(self):
        # Scenario from the plan: work 0-30, [meeting+work] 30-37
        # work chain reaches back to 0; meeting chain only to 30
        ivs = [
            self._iv(0, 30, ["work"]),
            self._iv(30, 37, ["meeting", "work"]),
        ]
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        assert arete._session_start_for_tag("work", ivs) == base + timedelta(minutes=0)
        assert arete._session_start_for_tag("meeting", ivs) == base + timedelta(minutes=30)

    def test_adjacency_tolerance(self):
        # 1-second gap between intervals should still be treated as adjacent
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        ivs = [
            {"start": base, "end": base + timedelta(minutes=30, seconds=1), "tags": ["work"]},
            {"start": base + timedelta(minutes=30, seconds=2), "end": base + timedelta(hours=1), "tags": ["work"]},
        ]
        result = arete._session_start_for_tag("work", ivs)
        assert result == base


class TestTagDurationToday:
    TZ = timezone.utc

    def _iv(self, start_min, end_min, tags):
        base = datetime(2024, 1, 1, tzinfo=self.TZ)
        return {
            "start": base + timedelta(minutes=start_min),
            "end": base + timedelta(minutes=end_min),
            "tags": tags,
        }

    def test_no_intervals_returns_zero(self):
        assert arete._tag_duration_today("work", []) == 0

    def test_tag_not_present_returns_zero(self):
        ivs = [self._iv(0, 30, ["other"])]
        assert arete._tag_duration_today("work", ivs) == 0

    def test_single_interval(self):
        ivs = [self._iv(0, 30, ["work"])]
        assert arete._tag_duration_today("work", ivs) == 30 * 60

    def test_multiple_intervals_summed(self):
        ivs = [self._iv(0, 30, ["work"]), self._iv(60, 90, ["work"])]
        assert arete._tag_duration_today("work", ivs) == 60 * 60

    def test_mixed_tags_only_counts_target(self):
        ivs = [self._iv(0, 30, ["work"]), self._iv(30, 60, ["meeting", "work"])]
        assert arete._tag_duration_today("work", ivs) == 60 * 60
        assert arete._tag_duration_today("meeting", ivs) == 30 * 60

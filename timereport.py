#!/usr/bin/env python3
"""Arête historic data viewer.

Displays graphical reports for 'timew day', 'timew week', and 'timew month'
data. Launched from the Arête menu applet.
"""

import subprocess
import shutil
import json
import sys
import os
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

from AppKit import (
    NSApplication, NSApplicationActivationPolicyRegular, NSAlert,
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSBackingStoreBuffered, NSRect, NSPoint, NSSize,
    NSStackView, NSTabView, NSTabViewItem,
    NSTextField, NSColor, NSFont, NSBezierPath,
    NSImage, NSImageView, NSView, NSScrollView,
    NSUserInterfaceLayoutOrientationVertical,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSGraphicsContext, NSClipView,
    NSTrackingArea,
    NSTrackingAreaOptions,
    NSViewWidthSizable, NSViewHeightSizable,
    NSLayoutConstraint,
    NSLayoutAttributeTop,
    NSGridView, NSGridColumn,
    NSButton, NSMenu, NSMenuItem, NSPopUpButton,
    NSControlStateValueOn, NSControlStateValueOff,
    NSCursor,
)
from AppKit import (
    NSFontAttributeName, NSForegroundColorAttributeName,
    NSTrackingMouseMoved, NSTrackingActiveInKeyWindow, NSTrackingInVisibleRect,
    NSPointInRect,
)
from AppKit import NSPasteboard, NSPasteboardTypeString
from Foundation import NSObject, NSString, NSProcessInfo, NSBundle
import objc


# ---------------------------------------------------------------------------
# timew helpers
# ---------------------------------------------------------------------------

def find_timew_path():
    path = shutil.which("timew")
    if path:
        return path
    for p in [
        "/opt/homebrew/bin/timew",
        "/usr/local/bin/timew",
        os.path.expanduser("~/.nix-profile/bin/timew"),
        "/run/current-system/sw/bin/timew",
    ]:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return "timew"


TIMEW = find_timew_path()


def run_timew(*args):
    result = subprocess.run(
        [TIMEW] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def run_timew_checked(*args):
    """Like run_timew() but raises RuntimeError on non-zero exit."""
    result = subprocess.run(
        [TIMEW] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def parse_utc(s):
    clean = s.replace("T", "").replace("Z", "+0000")
    return datetime.strptime(clean, "%Y%m%d%H%M%S%z")


def local_tz():
    return datetime.now().astimezone().tzinfo


def date_range_for(period, offset):
    """Return (start_date, end_date, title) for the given period and offset.

    offset=0 is current, -1 is previous, +1 is next (capped at today).
    period: "day" | "week" | "month"
    """
    today = date.today()
    if period == "day":
        d = today + timedelta(days=offset)
        if d > today:
            d = today
        if d == today:
            title = "Today"
        elif d == today - timedelta(days=1):
            title = "Yesterday"
        else:
            title = d.strftime("%-d %b %Y")
        return d, d, title
    elif period == "week":
        monday = today - timedelta(days=today.weekday())
        monday = monday + timedelta(weeks=offset)
        if monday > today - timedelta(days=today.weekday()):
            monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        if monday == today - timedelta(days=today.weekday()):
            title = "This Week"
        elif monday == today - timedelta(days=today.weekday()) - timedelta(weeks=1):
            title = "Last Week"
        else:
            title = f"W{monday.strftime('%W')} {monday.strftime('%Y')}"
        return monday, min(sunday, today), title
    else:  # month
        # Step months by offset from today's month
        year = today.year
        month = today.month + offset
        while month <= 0:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        if year > today.year or (year == today.year and month > today.month):
            year, month = today.year, today.month
        first = date(year, month, 1)
        # last day of that month (or today if current month)
        if year == today.year and month == today.month:
            last = today
            title = "This Month"
        else:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            last = date(year, month, last_day)
            title = first.strftime("%B %Y")
        return first, last, title


def _parse_intervals(out):
    """Parse raw JSON output from `timew export` into interval dicts."""
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []

    tz = local_tz()
    intervals = []
    for item in data:
        start_str = item.get("start")
        if not start_str:
            continue
        try:
            start = parse_utc(start_str).astimezone(tz)
        except Exception:
            continue

        end_str = item.get("end")
        if end_str:
            try:
                end = parse_utc(end_str).astimezone(tz)
            except Exception:
                end = datetime.now(tz)
        else:
            end = datetime.now(tz)

        intervals.append({
            "start": start,
            "end": end,
            "tags": item.get("tags", []),
            "duration": (end - start).total_seconds(),
            "annotation": item.get("annotation", ""),
            "id": item.get("id"),
        })
    return intervals


def export_intervals(range_arg):
    """Return list of interval dicts for a timew range keyword (e.g. ':day')."""
    return _parse_intervals(run_timew("export", range_arg))


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

_PALETTE = [
    (0.23, 0.51, 0.82),   # blue
    (0.87, 0.42, 0.17),   # orange
    (0.22, 0.67, 0.42),   # green
    (0.70, 0.25, 0.70),   # purple
    (0.80, 0.70, 0.10),   # gold
    (0.20, 0.73, 0.80),   # teal
    (0.85, 0.22, 0.35),   # red
    (0.52, 0.38, 0.22),   # brown
    (0.55, 0.55, 0.55),   # gray
    (0.38, 0.22, 0.52),   # indigo
]


def _rgb(idx):
    return _PALETTE[idx % len(_PALETTE)]


def tag_color(idx, alpha=1.0):
    r, g, b = _rgb(idx)
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


# ---------------------------------------------------------------------------
# Shared layout constants
# ---------------------------------------------------------------------------

ROW_H = 42          # height of each timeline lane/bar
ROW_GAP = 8         # vertical gap between rows
LABEL_W = 56        # width of the left-side day label column
PAD_RIGHT = 16
PAD_TOP = 22        # room for hour labels at top
PAD_BOTTOM = 8
TIMELINE_W = 900    # total image width (label + axis)
AXIS_W = TIMELINE_W - LABEL_W - PAD_RIGHT   # drawable axis width


def seconds_to_hm(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


# ---------------------------------------------------------------------------
# Core drawing: one timeline row
# ---------------------------------------------------------------------------

def _layout_row(ctx_intervals, y_base, row_h, graph_x0, graph_w, t_start, t_end):
    """Compute bar geometry for a row without drawing anything.

    Returns list of hit-test records:
      (NSRect, tag, start_dt, end_dt, annotation, interval_id,
       graph_x0, graph_w, t_start, t_end)
    The last four fields carry the axis geometry so callers can reverse-map
    x-positions back to datetimes (needed for in-place drag editing).
    """
    total_secs = (t_end - t_start).total_seconds()
    if total_secs <= 0:
        return []

    def x_for_dt(dt):
        frac = (dt - t_start).total_seconds() / total_secs
        frac = max(0.0, min(1.0, frac))
        return graph_x0 + frac * graph_w

    sorted_inv = sorted(ctx_intervals, key=lambda i: i["start"])
    lanes = []
    draw_data = []

    for inv in sorted_inv:
        x0 = x_for_dt(inv["start"])
        x1 = x_for_dt(inv["end"])
        if x1 - x0 < 2.0:
            x1 = x0 + 2.0

        lane_idx = 0
        while lane_idx < len(lanes):
            if lanes[lane_idx] <= inv["start"]:
                break
            lane_idx += 1
        if lane_idx == len(lanes):
            lanes.append(inv["end"])
        else:
            lanes[lane_idx] = inv["end"]

        draw_data.append((x0, x1, lane_idx, inv.get("tag", ""),
                          inv["start"], inv["end"],
                          inv.get("annotation", ""), inv.get("id")))

    num_lanes = max(len(lanes), 1)
    lane_h = min(float(row_h - 2), float(row_h) / num_lanes)

    hits = []
    for x0, x1, lane_idx, tag, start_dt, end_dt, annotation, interval_id in draw_data:
        y = y_base + lane_idx * lane_h + 1.0
        rect = NSRect(NSPoint(x0, y), NSSize(x1 - x0, lane_h - 1.0))
        hits.append((rect, tag, start_dt, end_dt, annotation, interval_id,
                     graph_x0, graph_w, t_start, t_end))
    return hits


def _draw_row(ctx_intervals, tag_index, y_base, row_h, graph_x0, graph_w,
              t_start, t_end, now_dt=None):
    """Draw one horizontal timeline row onto the current focus.

    ctx_intervals : list of {start, end, tag}  (already split per-tag)
    tag_index     : dict {tag_name: int} for color lookup
    y_base        : bottom y of this row's lane area
    row_h         : total lane height budget
    graph_x0      : left x of the drawable axis
    graph_w       : width of the drawable axis
    t_start/end   : datetime span for the full axis
    now_dt        : if not None, draw "now" indicator
    """
    hits = _layout_row(ctx_intervals, y_base, row_h, graph_x0, graph_w,
                       t_start, t_end)

    total_secs = (t_end - t_start).total_seconds()
    if total_secs <= 0:
        return hits

    def x_for_dt(dt):
        frac = (dt - t_start).total_seconds() / total_secs
        frac = max(0.0, min(1.0, frac))
        return graph_x0 + frac * graph_w

    for rect, tag, _s, _e, ann, _iid, *_ in hits:
        r_val = (rect.size.height - 1.0) / 2.0
        bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, r_val, r_val
        )
        cidx = tag_index.get(tag, 0)
        tag_color(cidx, 0.40).set()
        bar_path.fill()
        tag_color(cidx, 0.90).set()
        bar_path.setLineWidth_(1.0)
        bar_path.stroke()

    # "Now" indicator
    if now_dt and t_start <= now_dt <= t_end:
        nx = x_for_dt(now_dt)
        # Derive top_y from hits if available, else estimate
        lane_h_est = ROW_H
        top_y = y_base + lane_h_est + 2.0
        bot_y = y_base
        np_ = NSBezierPath.bezierPath()
        np_.setLineWidth_(1.5)
        np_.moveToPoint_(NSPoint(nx, top_y))
        np_.lineToPoint_(NSPoint(nx, bot_y))
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.5, 1.0, 1.0).set()
        np_.stroke()
        dr = 2.5
        dot = NSBezierPath.bezierPathWithOvalInRect_(
            NSRect(NSPoint(nx - dr, top_y - dr), NSSize(dr * 2, dr * 2))
        )
        dot.fill()

    return hits


# ---------------------------------------------------------------------------
# Draw a full multi-row timeline — returns (NSImage, height, hit_rects)
# hit_rects : list of (NSRect, tag, start_dt, end_dt)
# ---------------------------------------------------------------------------

def _compute_row_layout(rows, row_h=None):
    """Return (row_heights list, total_height int)."""
    if row_h is None:
        row_h = ROW_H
    row_lane_counts = []
    for _lbl, day, invs in rows:
        if not invs:
            row_lane_counts.append(1)
            continue
        sorted_i = sorted(invs, key=lambda i: i["start"])
        lanes = []
        for inv in sorted_i:
            li = 0
            while li < len(lanes) and lanes[li] > inv["start"]:
                li += 1
            if li == len(lanes):
                lanes.append(inv["end"])
            else:
                lanes[li] = inv["end"]
        row_lane_counts.append(max(len(lanes), 1))

    row_heights = [max(row_h, c * row_h) for c in row_lane_counts]
    total_rows_h = sum(row_heights) + ROW_GAP * len(rows)
    height = PAD_TOP + total_rows_h + PAD_BOTTOM
    return row_heights, height


def _paint_timeline(rows, tag_index, hour_start, hour_end, width, height,
                    row_heights, graph_x0, graph_w):
    """Paint the timeline onto the current graphics focus.

    Returns list of (NSRect, tag, start_dt, end_dt) hit records.
    """
    total_hours = hour_end - hour_start

    def x_for_hour(h):
        return graph_x0 + ((h - hour_start) / total_hours) * graph_w

    small_attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(9.0),
        NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
    }

    # Baseline
    base = NSBezierPath.bezierPath()
    base.setLineWidth_(0.5)
    base.moveToPoint_(NSPoint(graph_x0, height - PAD_TOP + 1))
    base.lineToPoint_(NSPoint(width - PAD_RIGHT, height - PAD_TOP + 1))
    NSColor.separatorColor().set()
    base.stroke()

    for h in range(hour_start, hour_end + 1):
        x = x_for_hour(h)
        gp = NSBezierPath.bezierPath()
        gp.setLineWidth_(0.5)
        gp.moveToPoint_(NSPoint(x, height - PAD_TOP))
        gp.lineToPoint_(NSPoint(x, PAD_BOTTOM))
        NSColor.separatorColor().set()
        gp.stroke()

        lbl = NSString.stringWithString_(f"{h:02d}")
        lbl_sz = lbl.sizeWithAttributes_(small_attrs)
        lbl.drawAtPoint_withAttributes_(
            NSPoint(x - lbl_sz.width / 2.0, height - PAD_TOP + 3),
            small_attrs,
        )

    today = date.today()
    now_dt = datetime.now().astimezone()
    tz = local_tz()
    y_cursor = height - PAD_TOP - ROW_GAP
    all_hits = []

    for row_idx, (label_str, day_date, invs) in enumerate(rows):
        rh = row_heights[row_idx]
        y_base = y_cursor - rh

        if day_date == today:
            hilite = NSRect(NSPoint(graph_x0, y_base - 1), NSSize(graph_w, rh + 2))
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.51, 0.82, 0.06).set()
            NSBezierPath.fillRect_(hilite)

        lbl = NSString.stringWithString_(label_str)
        lbl_sz = lbl.sizeWithAttributes_(small_attrs)
        lbl_attrs = dict(small_attrs)
        if day_date == today:
            lbl_attrs[NSForegroundColorAttributeName] = \
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.51, 0.82, 1.0)
        lbl.drawAtPoint_withAttributes_(
            NSPoint(graph_x0 - lbl_sz.width - 6,
                    y_base + rh / 2.0 - lbl_sz.height / 2.0),
            lbl_attrs,
        )

        if invs:
            t_start = datetime(day_date.year, day_date.month, day_date.day,
                               hour_start, 0, 0, tzinfo=tz)
            t_end = (datetime(day_date.year, day_date.month, day_date.day,
                              0, 0, 0, tzinfo=tz) + timedelta(hours=hour_end))
            flat = []
            for inv in invs:
                for tag in inv["tags"] or ["(untagged)"]:
                    flat.append({
                        "start": inv["start"], "end": inv["end"], "tag": tag,
                        "annotation": inv.get("annotation", ""),
                        "id": inv.get("id"),
                    })

            hits = _draw_row(
                flat, tag_index, y_base, rh,
                graph_x0, graph_w, t_start, t_end,
                now_dt=(now_dt if day_date == today else None),
            )
            all_hits.extend(hits)

        y_cursor = y_base - ROW_GAP

    return all_hits


def draw_timeline_report(rows, tag_index, hour_start=0, hour_end=24,
                         width=TIMELINE_W, show_now=True):
    """Render timeline to an NSImage.

    Returns (NSImage, height, hit_rects).
    hit_rects : list of (NSRect, tag, start_dt, end_dt)
    """
    row_heights, height = _compute_row_layout(rows)
    graph_x0 = float(LABEL_W)
    graph_w = float(width - LABEL_W - PAD_RIGHT)

    img = NSImage.alloc().initWithSize_(NSSize(float(width), float(height)))
    img.lockFocus()
    NSGraphicsContext.currentContext().setShouldAntialias_(True)
    NSColor.windowBackgroundColor().set()
    NSBezierPath.fillRect_(NSRect(NSPoint(0, 0), NSSize(width, height)))

    hit_rects = _paint_timeline(rows, tag_index, hour_start, hour_end,
                                width, height, row_heights, graph_x0, graph_w)
    img.unlockFocus()
    return img, height, hit_rects


# ---------------------------------------------------------------------------
# Interactive timeline view (NSView subclass with hover tooltips)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# timew summary output panel
# ---------------------------------------------------------------------------

class SummaryOutputPanel(NSObject):
    """Floating panel that shows the output of `timew summary` in a monospaced text view."""

    def initWithText_title_(self, text, title):
        self = objc.super(SummaryOutputPanel, self).init()
        if self is None:
            return None
        from AppKit import (
            NSPanel, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
            NSWindowStyleMaskResizable, NSBackingStoreBuffered,
            NSTextView, NSScrollView, NSFont as _NSFont,
        )
        W, H = 800, 480
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(W, H)),
            style, NSBackingStoreBuffered, False,
        )
        panel.setReleasedWhenClosed_(False)
        panel.setTitle_(title)
        panel.center()

        sv = NSScrollView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        sv.setHasVerticalScroller_(True)
        sv.setHasHorizontalScroller_(True)
        sv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        sv.setAutohidesScrollers_(True)

        tv = NSTextView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        tv.setEditable_(False)
        tv.setFont_(_NSFont.fontWithName_size_("Menlo", 11) or
                    _NSFont.monospacedSystemFontOfSize_weight_(11, 0))
        tv.setString_(text)
        tv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        sv.setDocumentView_(tv)

        panel.setContentView_(sv)
        self._panel = panel
        panel.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        return self


# ---------------------------------------------------------------------------
# Interactive timeline view (NSView subclass with hover tooltips)
# ---------------------------------------------------------------------------

class TimelineView(NSView):
    """NSView that draws the timeline and scales with the view width."""

    def initWithRows_tagIndex_hourStart_hourEnd_rowH_(
            self, rows, tag_index, hour_start, hour_end, row_h):
        return self.initWithRows_tagIndex_hourStart_hourEnd_rowH_startDate_endDate_filterTags_(
            rows, tag_index, hour_start, hour_end, row_h, None, None, None
        )

    def initWithRows_tagIndex_hourStart_hourEnd_rowH_startDate_endDate_filterTags_(
            self, rows, tag_index, hour_start, hour_end, row_h,
            start_date, end_date, filter_tags):
        row_heights, height = _compute_row_layout(rows, row_h=row_h)
        frame = NSRect(NSPoint(0, 0), NSSize(float(TIMELINE_W), float(height)))
        self = objc.super(TimelineView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._rows = rows
        self._tag_index = tag_index
        self._hour_start = hour_start
        self._hour_end = hour_end
        self._row_heights = row_heights
        self._height = height
        self._hit_rects = []
        self._start_date = start_date
        self._end_date = end_date
        self._filter_tags = filter_tags  # set|None
        self._summary_panel = None       # keep alive
        # Stretch horizontally with the scroll view's clip view
        self.setAutoresizingMask_(NSViewWidthSizable)
        self._setup_tracking()
        return self

    def _setup_tracking(self):
        opts = (NSTrackingMouseMoved |
                NSTrackingActiveInKeyWindow |
                NSTrackingInVisibleRect)
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None
        )
        self.addTrackingArea_(area)

    def setFrameSize_(self, size):
        objc.super(TimelineView, self).setFrameSize_(size)
        self.setNeedsDisplay_(True)

    # ── drag state ──────────────────────────────────────────────────────────
    # _drag: None | dict with keys:
    #   edge        – "start" or "end"
    #   interval_id – int
    #   orig_dt     – original datetime before drag
    #   current_dt  – datetime currently shown during drag
    #   rect        – original bar NSRect (for overlay drawing reference)
    #   tag         – tag string (for colour)
    #   gx0, gw     – graph_x0, graph_w
    #   t_start, t_end – axis span datetimes

    _EDGE_HIT_PX = 8.0   # px from bar edge that counts as a handle grab

    def _edge_hit(self, pt):
        """Return (entry, "start"|"end") if pt is near a bar edge, else None."""
        for entry in self._hit_rects:
            rect = entry[0]
            interval_id = entry[5]
            if interval_id is None:
                continue
            bar_left  = rect.origin.x
            bar_right = rect.origin.x + rect.size.width
            bar_top   = rect.origin.y + rect.size.height
            bar_bot   = rect.origin.y
            # Must be within the bar's vertical extent
            if not (bar_bot <= pt.y <= bar_top):
                continue
            if abs(pt.x - bar_left) <= self._EDGE_HIT_PX:
                return entry, "start"
            if abs(pt.x - bar_right) <= self._EDGE_HIT_PX:
                return entry, "end"
        return None

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        tip = ""
        for entry in self._hit_rects:
            rect, tag, start_dt, end_dt, annotation = entry[0], entry[1], entry[2], entry[3], entry[4]
            if NSPointInRect(pt, rect):
                duration = (end_dt - start_dt).total_seconds()
                tip = (
                    f"{tag}  "
                    f"{start_dt.strftime('%-H:%M')}–{end_dt.strftime('%-H:%M')}  "
                    f"({seconds_to_hm(duration)})"
                )
                if annotation:
                    tip += f"  📝 {annotation}"
                break
        self.setToolTip_(tip)

        # Update cursor based on proximity to bar edges
        edge = self._edge_hit(pt)
        if edge is not None:
            NSCursor.resizeLeftRightCursor().set()
        else:
            NSCursor.arrowCursor().set()

    def _hit_at_point(self, pt):
        """Return the hit-rect tuple under *pt*, or None."""
        for entry in self._hit_rects:
            if NSPointInRect(pt, entry[0]):
                return entry
        return None

    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        edge_hit = self._edge_hit(pt)
        if edge_hit is None:
            self._drag = None
            return
        entry, edge = edge_hit
        rect, tag, start_dt, end_dt, _ann, interval_id, gx0, gw, t_start, t_end = entry
        orig_dt = start_dt if edge == "start" else end_dt
        self._drag = {
            "edge":        edge,
            "interval_id": interval_id,
            "orig_dt":     orig_dt,
            "current_dt":  orig_dt,
            "rect":        rect,
            "tag":         tag,
            "gx0":         gx0,
            "gw":          gw,
            "t_start":     t_start,
            "t_end":       t_end,
        }
        NSCursor.resizeLeftRightCursor().push()

    def mouseDragged_(self, event):
        if not getattr(self, "_drag", None):
            return
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        d = self._drag
        # Reverse-map x → datetime, snap to minute
        total_secs = (d["t_end"] - d["t_start"]).total_seconds()
        frac = max(0.0, min(1.0, (pt.x - d["gx0"]) / d["gw"]))
        snapped = d["t_start"] + timedelta(seconds=round(frac * total_secs / 60) * 60)
        d["current_dt"] = snapped
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        drag = getattr(self, "_drag", None)
        if not drag:
            return
        NSCursor.pop()
        self._drag = None

        new_dt  = drag["current_dt"]
        orig_dt = drag["orig_dt"]
        if new_dt == orig_dt:
            return   # no change — skip the write

        iid      = f"@{drag['interval_id']}"
        timew_dt = new_dt.astimezone().strftime("%Y%m%dT%H%M%S%z")

        # Try without :adjust first; only prompt if there's an overlap conflict.
        try:
            run_timew_checked("modify", drag["edge"], iid, timew_dt)
        except RuntimeError as err:
            # timew returns non-zero when the change would create an overlap.
            # Ask the user whether to fix the overlap automatically.
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Overlapping interval")
            alert.setInformativeText_(
                "This change overlaps an adjacent interval. "
                "Adjust the neighbouring interval automatically?"
            )
            alert.addButtonWithTitle_("Adjust")   # return value 1000
            alert.addButtonWithTitle_("Cancel")   # return value 1001
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            response = alert.runModal()
            if response == 1000:   # NSAlertFirstButtonReturn
                try:
                    run_timew_checked("modify", drag["edge"], iid, timew_dt, ":adjust")
                except RuntimeError:
                    pass   # still failed — fall through to redraw/revert
            else:
                # User cancelled — redraw restores original from timew data
                self.setNeedsDisplay_(True)
                return

        refresh = getattr(self, "_on_refresh", None)
        if refresh is not None:
            refresh()
        else:
            self.setNeedsDisplay_(True)

    def resetCursorRects(self):
        for entry in self._hit_rects:
            rect = entry[0]
            interval_id = entry[5]
            if interval_id is None:
                continue
            bar_left  = rect.origin.x
            bar_top   = rect.origin.y + rect.size.height
            bar_bot   = rect.origin.y
            bar_h     = rect.size.height
            w         = self._EDGE_HIT_PX * 2
            for ex in (bar_left, bar_left + rect.size.width):
                self.addCursorRect_cursor_(
                    NSRect(NSPoint(ex - self._EDGE_HIT_PX, bar_bot),
                           NSSize(w, bar_h)),
                    NSCursor.resizeLeftRightCursor(),
                )

    def rightMouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._right_click_hit = self._hit_at_point(pt)
        hit = self._right_click_hit is not None

        menu = NSMenu.alloc().initWithTitle_("")

        annotate_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Annotate…", "annotateInterval:", ""
        )
        annotate_item.setTarget_(self)
        annotate_item.setEnabled_(hit)
        menu.addItem_(annotate_item)

        edit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Edit interval…", "editInterval:", ""
        )
        edit_item.setTarget_(self)
        edit_item.setEnabled_(hit)
        menu.addItem_(edit_item)

        sep = NSMenuItem.separatorItem()
        menu.addItem_(sep)

        raw_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show raw data", "showRawData:", ""
        )
        raw_item.setTarget_(self)
        menu.addItem_(raw_item)
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)

    @objc.typedSelector(b"v@:@")
    def annotateInterval_(self, sender):
        hit = getattr(self, "_right_click_hit", None)
        if not hit:
            return
        _rect, tag, start_dt, end_dt, _annotation, interval_id = hit[:6]
        if interval_id is None:
            return
        tags_hint = tag
        # Late import — arete is already in sys.modules when timereport runs
        import sys as _sys
        arete_mod = _sys.modules.get("arete") or _sys.modules.get("__main__")
        AnnotateWindow = getattr(arete_mod, "AnnotateWindow", None)
        if AnnotateWindow is None:
            return
        # Re-fetch the current annotation from timew so _existing is always
        # fresh (hit-rect value may be stale if the view hasn't been redrawn).
        existing = ""
        try:
            out = run_timew("export", f"@{interval_id}")
            if out:
                data = json.loads(out)
                if data:
                    existing = data[0].get("annotation", "")
        except Exception:
            pass
        ctrl = AnnotateWindow.alloc(
        ).initWithApp_intervalId_existing_onSave_tagsHint_(
            None, interval_id, existing,
            lambda: self.setNeedsDisplay_(True),
            tags_hint,
        )
        self._annotate_ctrl = ctrl  # keep alive
        ctrl.show()

    def drawRect_(self, dirty_rect):
        bounds = self.bounds()
        width = bounds.size.width
        height = self._height

        NSColor.windowBackgroundColor().set()
        NSBezierPath.fillRect_(bounds)
        NSGraphicsContext.currentContext().setShouldAntialias_(True)

        graph_x0 = float(LABEL_W)
        graph_w = float(width - LABEL_W - PAD_RIGHT)

        hits = _paint_timeline(
            self._rows, self._tag_index,
            self._hour_start, self._hour_end,
            width, height,
            self._row_heights, graph_x0, graph_w,
        )
        self._hit_rects = hits

        # ── drag overlay ────────────────────────────────────────────────────
        drag = getattr(self, "_drag", None)
        if drag:
            # Find the matching hit-rect to get bar geometry for overlay
            iid = drag["interval_id"]
            for entry in hits:
                if entry[5] != iid:
                    continue
                rect, tag = entry[0], entry[1]
                # Recompute x for the dragged edge
                d = drag
                total_secs = (d["t_end"] - d["t_start"]).total_seconds()
                frac = (d["current_dt"] - d["t_start"]).total_seconds() / total_secs
                frac = max(0.0, min(1.0, frac))
                new_x = d["gx0"] + frac * d["gw"]
                # Draw a vertical line at the drag position
                line = NSBezierPath.bezierPath()
                line.setLineWidth_(2.0)
                line.moveToPoint_(NSPoint(new_x, rect.origin.y))
                line.lineToPoint_(NSPoint(new_x, rect.origin.y + rect.size.height))
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.0, 0.45, 0.9, 0.9).set()
                line.stroke()
                # Time label above the line
                lbl_attrs = {
                    NSFontAttributeName: NSFont.boldSystemFontOfSize_(9.0),
                    NSForegroundColorAttributeName: NSColor.labelColor(),
                }
                lbl = NSString.stringWithString_(
                    d["current_dt"].strftime("%-H:%M"))
                lbl_sz = lbl.sizeWithAttributes_(lbl_attrs)
                lbl.drawAtPoint_withAttributes_(
                    NSPoint(new_x - lbl_sz.width / 2.0,
                            rect.origin.y + rect.size.height + 2.0),
                    lbl_attrs)
                break

    @objc.typedSelector(b"v@:@")
    def editInterval_(self, sender):
        hit = getattr(self, "_right_click_hit", None)
        if not hit:
            return
        _rect, tag, start_dt, end_dt, _annotation, interval_id = hit[:6]
        if interval_id is None:
            return
        # Late import — arete is already in sys.modules when timereport runs
        import sys as _sys
        arete_mod = _sys.modules.get("arete") or _sys.modules.get("__main__")
        EditIntervalWindow = getattr(arete_mod, "EditIntervalWindow", None)
        if EditIntervalWindow is None:
            return
        # Re-fetch fresh data from timew (hit-rect data may be stale)
        tags = [tag]
        try:
            out = run_timew("export", f"@{interval_id}")
            if out:
                data = json.loads(out)
                if data:
                    inv = data[0]
                    tags = inv.get("tags") or [tag]
                    tz = local_tz()
                    start_dt = parse_utc(inv["start"]).astimezone(tz)
                    raw_end  = inv.get("end")
                    end_dt   = parse_utc(raw_end).astimezone(tz) if raw_end else None
        except Exception:
            pass
        def _on_edit_save():
            # Full tab rebuild if we have the hook; fall back to simple redraw
            refresh = getattr(self, "_on_refresh", None)
            if refresh is not None:
                refresh()
            else:
                self.setNeedsDisplay_(True)

        ctrl = EditIntervalWindow.alloc(
        ).initWithIntervalId_startDt_endDt_tags_onSave_(
            interval_id, start_dt, end_dt, tags,
            _on_edit_save,
        )
        self._edit_ctrl = ctrl  # keep alive
        ctrl.show()

    @objc.typedSelector(b"v@:@")
    def showRawData_(self, sender):
        if self._start_date is None:
            return
        start_str  = self._start_date.strftime("%Y-%m-%dT00:00:00")
        end_str    = (self._end_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        range_args = [start_str, "-", end_str]

        export_out = run_timew("export", *range_args)
        try:
            data = json.loads(export_out) if export_out else []
        except Exception:
            data = []

        if self._filter_tags:
            # OR-filter: collect @ids of intervals that contain any selected tag
            ids = [
                str(item["id"]) for item in data
                if any(t in (item.get("tags") or []) for t in self._filter_tags)
            ]
            summary_args = ["summary"] + (["@" + i for i in ids] if ids else range_args)
            title = "timew summary — " + ", ".join(sorted(self._filter_tags))
            annotated = [item for item in data
                         if any(t in (item.get("tags") or []) for t in self._filter_tags)
                         and item.get("annotation")]
        else:
            summary_args = ["summary"] + range_args
            title = "timew summary"
            annotated = [item for item in data if item.get("annotation")]

        output = run_timew(*summary_args) or "(no tracked time)"

        if annotated:
            tz = local_tz()
            lines = ["\n\nAnnotations:"]
            lines.append("-" * 60)
            for item in annotated:
                try:
                    end_str = item.get("end") or item.get("start", "")
                    time_str = parse_utc(end_str).astimezone(tz).strftime("%-d %b %Y  %-H:%M")
                except Exception:
                    time_str = item.get("end") or item.get("start", "")
                tags_str = ", ".join(item.get("tags") or [])
                lines.append(f"{time_str}  [{tags_str}]  {item['annotation']}")
            output += "\n".join(lines)

        self._summary_panel = SummaryOutputPanel.alloc().initWithText_title_(output, title)

    def acceptsFirstResponder(self):
        return True


# ---------------------------------------------------------------------------
# Pie chart
# ---------------------------------------------------------------------------

def _layout_pie_slices(all_tags, tag_index, tag_totals, target_secs):
    """Return list of slice descriptors (no drawing).

    Each entry: (start_angle, sweep, tag_or_None, secs, cidx)
      tag_or_None=None  means the grey "untracked remainder" slice.
    Angles are in AppKit degrees (CCW from east), start at 12-o'clock,
    advance clockwise.
    """
    tracked = sum(tag_totals.get(t, 0) for t in all_tags)
    denom = max(tracked, target_secs) or 1.0
    remainder = max(0.0, target_secs - tracked)

    slices = []
    start_angle = 90.0  # 12 o'clock
    for tag in all_tags:
        secs = tag_totals.get(tag, 0)
        if secs == 0:
            continue
        sweep = (secs / denom) * 360.0
        cidx = tag_index.get(tag, 0)
        slices.append((start_angle, sweep, tag, secs, cidx))
        start_angle -= sweep

    if remainder > 0:
        sweep = (remainder / denom) * 360.0
        slices.append((start_angle, sweep, None, remainder, -1))

    return slices


def _paint_pie(slices, cx, cy, radius):
    """Draw pie slices onto the current graphics focus."""
    import math

    def _make_path(start, sweep):
        path = NSBezierPath.bezierPath()
        path.moveToPoint_(NSPoint(cx, cy))
        path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            NSPoint(cx, cy), radius, start, start - sweep, True,
        )
        path.closePath()
        return path

    for start_angle, sweep, tag, secs, cidx in slices:
        path = _make_path(start_angle, sweep)
        if tag is None:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.75, 0.75, 0.75, 0.35).set()
            path.fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 0.6, 0.6, 0.6).set()
            path.setLineWidth_(0.5)
        else:
            tag_color(cidx, 0.80).set()
            path.fill()
            tag_color(cidx, 1.0).set()
            path.setLineWidth_(0.75)
        path.stroke()


def _pie_hit_test(slices, cx, cy, radius, px, py):
    """Return (tag, secs) for the slice under (px, py), or (None, 0)."""
    import math
    dx, dy = px - cx, py - cy
    dist = math.sqrt(dx * dx + dy * dy)
    if dist > radius or dist < 1e-3:
        return None, 0

    # Angle of the cursor in AppKit degrees (CCW from east, 0–360)
    angle = math.degrees(math.atan2(dy, dx)) % 360.0

    for start_angle, sweep, tag, secs, _cidx in slices:
        # Normalise the slice's angular span to [0, 360)
        end_angle = start_angle - sweep
        # Convert to [0, 360) working clockwise from 12 o'clock
        # The slice spans from start_angle down to end_angle (CW)
        # In CCW-degree space that means the slice "owns" the arc
        # [end_angle mod 360, start_angle mod 360].
        s = start_angle % 360.0
        e = end_angle % 360.0
        if s > e:
            hit = e <= angle <= s
        else:
            # Wrapped slice (crosses 0°)
            hit = angle >= e or angle <= s
        if hit:
            return tag, secs
    return None, 0


def draw_pie_chart(all_tags, tag_index, tag_totals, target_secs, size=180):
    """Return an NSImage of a pie chart (static version, kept for reference)."""
    W = H = size
    img = NSImage.alloc().initWithSize_(NSSize(float(W), float(H)))
    img.lockFocus()
    NSColor.windowBackgroundColor().set()
    NSBezierPath.fillRect_(NSRect(NSPoint(0, 0), NSSize(W, H)))

    tracked = sum(tag_totals.get(t, 0) for t in all_tags)
    if tracked == 0 and target_secs == 0:
        img.unlockFocus()
        return img

    slices = _layout_pie_slices(all_tags, tag_index, tag_totals, target_secs)
    _paint_pie(slices, W / 2.0, H / 2.0, min(W, H) / 2.0 - 4.0)
    img.unlockFocus()
    return img


# ---------------------------------------------------------------------------
# Interactive pie view (NSView subclass with hover tooltips)
# ---------------------------------------------------------------------------

class PieView(NSView):
    """NSView that draws the pie chart at a fixed 180×180 size, hover tooltips."""

    PIE_SIZE = 180.0

    def initWithAllTags_tagIndex_tagTotals_targetSecs_(
            self, all_tags, tag_index, tag_totals, target_secs):
        s = self.PIE_SIZE
        frame = NSRect(NSPoint(0, 0), NSSize(s, s))
        self = objc.super(PieView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._slices = _layout_pie_slices(all_tags, tag_index, tag_totals, target_secs)
        self._target_secs = target_secs
        opts = (NSTrackingMouseMoved |
                NSTrackingActiveInKeyWindow |
                NSTrackingInVisibleRect)
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None
        )
        self.addTrackingArea_(area)
        return self

    def _geometry(self):
        s = self.PIE_SIZE
        return s / 2.0, s / 2.0, s / 2.0 - 4.0

    def drawRect_(self, dirty_rect):
        NSColor.windowBackgroundColor().set()
        NSBezierPath.fillRect_(self.bounds())
        NSGraphicsContext.currentContext().setShouldAntialias_(True)
        if self._slices:
            cx, cy, radius = self._geometry()
            _paint_pie(self._slices, cx, cy, radius)

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        cx, cy, radius = self._geometry()
        tag, secs = _pie_hit_test(self._slices, cx, cy, radius, pt.x, pt.y)
        if tag is None and secs > 0:
            tip = f"Untracked  ({seconds_to_hm(secs)})"
        elif tag:
            pct = (secs / self._target_secs * 100) if self._target_secs else 0
            tip = f"{tag}  {seconds_to_hm(secs)}  ({pct:.1f}%)"
        else:
            tip = ""
        self.setToolTip_(tip)

    def acceptsFirstResponder(self):
        return True


# ---------------------------------------------------------------------------
# Legend image
# ---------------------------------------------------------------------------

def _paint_legend(all_tags, tag_index, width):
    """Paint the tag colour legend onto the current graphics context."""
    x = float(LABEL_W)
    attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(9.5),
        NSForegroundColorAttributeName: NSColor.labelColor(),
    }
    for tag in all_tags:
        cidx = tag_index[tag]
        swatch = NSRect(NSPoint(x, 5), NSSize(11, 10))
        tag_color(cidx, 0.85).set()
        NSBezierPath.fillRect_(swatch)
        tag_color(cidx, 1.0).set()
        NSBezierPath.strokeRect_(swatch)
        x += 14
        lbl = NSString.stringWithString_(tag)
        sz = lbl.sizeWithAttributes_(attrs)
        lbl.drawAtPoint_withAttributes_(NSPoint(x, 4), attrs)
        x += sz.width + 12
        if x > width - 40:
            break


class LegendView(NSView):
    """Fixed-height legend bar that repaints at any width."""

    LEGEND_H = 20

    def initWithAllTags_tagIndex_(self, all_tags, tag_index):
        frame = NSRect(NSPoint(0, 0), NSSize(float(TIMELINE_W), float(self.LEGEND_H)))
        self = objc.super(LegendView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._all_tags = all_tags
        self._tag_index = tag_index
        self.setAutoresizingMask_(NSViewWidthSizable)
        return self

    def setFrameSize_(self, size):
        objc.super(LegendView, self).setFrameSize_(size)
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty_rect):
        NSColor.windowBackgroundColor().set()
        NSBezierPath.fillRect_(self.bounds())
        _paint_legend(self._all_tags, self._tag_index, self.bounds().size.width)


# ---------------------------------------------------------------------------
# Summary table helpers
# ---------------------------------------------------------------------------

def make_label(text, size=12, bold=False, muted=False, color=None):
    lbl = NSTextField.labelWithString_(text)
    lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    if color:
        lbl.setTextColor_(color)
    elif muted:
        lbl.setTextColor_(NSColor.secondaryLabelColor())
    lbl.setSelectable_(True)
    return lbl


def make_image_view(img, w, h):
    iv = NSImageView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(w, h)))
    iv.setImage_(img)
    iv.setTranslatesAutoresizingMaskIntoConstraints_(False)
    iv.widthAnchor().constraintEqualToConstant_(float(w)).setActive_(True)
    iv.heightAnchor().constraintEqualToConstant_(float(h)).setActive_(True)
    return iv


class SummaryTableView(NSView):
    """NSView wrapper around NSGridView that adds a right-click → Copy as CSV context menu."""

    def initWithGrid_csvRows_(self, grid, csv_rows):
        """
        grid     : the NSGridView to embed
        csv_rows : list of lists of strings — the raw data for CSV export
                   (first row is the header, no dot column)
        """
        # Size to the grid's natural size; constraints will adjust it later
        natural_h = grid.fittingSize().height
        natural_w = grid.fittingSize().width
        frame = NSRect(NSPoint(0, 0), NSSize(natural_w or 400, natural_h or 100))
        self = objc.super(SummaryTableView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._csv_rows = csv_rows
        grid.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.addSubview_(grid)
        # Pin top/leading/trailing only — height is set by the grid's own
        # heightAnchor constraint.  No bottomAnchor pin because the parent
        # (SummaryTableView) is frame-based when used as a scroll-view document.
        NSLayoutConstraint.activateConstraints_([
            grid.topAnchor().constraintEqualToAnchor_(self.topAnchor()),
            grid.leadingAnchor().constraintEqualToAnchor_(self.leadingAnchor()),
            grid.trailingAnchor().constraintEqualToAnchor_(self.trailingAnchor()),
        ])
        return self

    def acceptsFirstResponder(self):
        return True

    def rightMouseDown_(self, event):
        menu = NSMenu.alloc().initWithTitle_("")
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Copy as CSV", "copyCSV:", ""
        )
        item.setTarget_(self)
        menu.addItem_(item)
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)

    @objc.typedSelector(b"v@:@")
    def copyCSV_(self, sender):
        def _quote(v):
            v = str(v)
            if "," in v or '"' in v or "\n" in v:
                v = '"' + v.replace('"', '""') + '"'
            return v

        csv = "\n".join(",".join(_quote(cell) for cell in row)
                        for row in self._csv_rows)
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(csv, NSPasteboardTypeString)


def build_summary_table(all_tags, tag_totals, grand_total, target_secs, tag_index,
                        period_title=""):
    """Return a SummaryTableView wrapping an NSGridView.

    Columns:
      ● | Tag | Time | % of period | [% of target]  (last column only when target_secs > 0)
    Right-click the table to copy data as CSV.
    """
    from AppKit import NSGridCell, NSTextAlignmentRight, NSTextAlignmentLeft

    FONT_SIZE = 11.0
    ROW_H = 18.0
    show_target_col = target_secs > 0

    sorted_tags = [t for t in sorted(all_tags, key=lambda t: -tag_totals[t])
                   if tag_totals[t] > 0]

    def cell_label(text, size=FONT_SIZE, bold=False, muted=False,
                   color=None, align=None):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                     else NSFont.systemFontOfSize_(size))
        if color:
            lbl.setTextColor_(color)
        elif muted:
            lbl.setTextColor_(NSColor.secondaryLabelColor())
        if align is not None:
            lbl.setAlignment_(align)
        lbl.setSelectable_(True)
        return lbl

    num_cols = 5 if show_target_col else 4

    # Header row
    header = [
        cell_label(""),
        cell_label("Tag",       bold=True),
        cell_label("Time",      bold=True, align=NSTextAlignmentRight),
        cell_label("% of period", bold=True, align=NSTextAlignmentRight),
    ]
    if show_target_col:
        header.append(cell_label("% of target", bold=True, align=NSTextAlignmentRight))

    rows = [header]

    # CSV data (no dot column, plain strings); period title on its own row
    csv_rows = [[f"Period: {period_title}"]] if period_title else []
    csv_header = ["Tag", "Seconds", "Time", "% of period"]
    if show_target_col:
        csv_header.append("% of target")
    csv_rows.append(csv_header)

    for tag in sorted_tags:
        secs = tag_totals[tag]
        pct_period = (secs / grand_total * 100) if grand_total else 0
        cidx = tag_index[tag]
        r, g, b = _rgb(cidx)
        dot_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)
        row = [
            cell_label("●", color=dot_color),
            cell_label(tag),
            cell_label(seconds_to_hm(secs), align=NSTextAlignmentRight),
            cell_label(f"{pct_period:.1f}%", muted=True, align=NSTextAlignmentRight),
        ]
        csv_row = [tag, int(secs), seconds_to_hm(secs), f"{pct_period:.1f}%"]
        if show_target_col:
            pct_target = secs / target_secs * 100
            row.append(cell_label(f"{pct_target:.1f}%", muted=True, align=NSTextAlignmentRight))
            csv_row.append(f"{pct_target:.1f}%")
        rows.append(row)
        csv_rows.append(csv_row)

    grid = NSGridView.gridViewWithNumberOfColumns_rows_(num_cols, len(rows))
    grid.setRowSpacing_(2.0)
    grid.setColumnSpacing_(10.0)
    grid.setTranslatesAutoresizingMaskIntoConstraints_(False)

    for r_idx, row_cells in enumerate(rows):
        for c_idx, view in enumerate(row_cells):
            grid.cellAtColumnIndex_rowIndex_(c_idx, r_idx).setContentView_(view)

    # Column widths
    grid.columnAtIndex_(0).setWidth_(14.0)   # dot
    grid.columnAtIndex_(1).setWidth_(150.0)  # tag name
    grid.columnAtIndex_(2).setWidth_(70.0)   # time
    grid.columnAtIndex_(3).setWidth_(80.0)   # % of period
    if show_target_col:
        grid.columnAtIndex_(4).setWidth_(80.0)  # % of target

    for r_idx in range(len(rows)):
        grid.rowAtIndex_(r_idx).setHeight_(ROW_H)

    grid.rowAtIndex_(0).setBottomPadding_(4.0)
    if len(rows) > 1:
        grid.rowAtIndex_(1).setTopPadding_(2.0)

    natural_h = len(rows) * ROW_H + grid.rowSpacing() * (len(rows) - 1) + 4.0 + 2.0
    grid.heightAnchor().constraintEqualToConstant_(natural_h).setActive_(True)

    wrapper = SummaryTableView.alloc().initWithGrid_csvRows_(grid, csv_rows)
    wrapper.setTranslatesAutoresizingMaskIntoConstraints_(False)
    wrapper.heightAnchor().constraintEqualToConstant_(natural_h).setActive_(True)

    return wrapper


# ---------------------------------------------------------------------------
# Annotations table
# ---------------------------------------------------------------------------

def build_annotations_table(intervals, tag_index, height_above=0.0):
    """Return (scroll_view, natural_h) for the annotations table.

    Columns: time | ● tag(s) | annotation text
    Returns (None, 0) when there are no annotations in the period.

    Always returns an NSScrollView whose document view is the full-height
    SummaryTableView.  The scroll view's visible height is initially capped to
    fit the screen (height_above is used to compute the remaining space), but
    the caller can update it at any time via CollapsibleSection.set_content_h().
    autohidesScrollers=True means the scrollbar only appears when needed.
    """
    from AppKit import NSTextAlignmentLeft, NSGridCell, NSScreen

    annotated = sorted(
        [inv for inv in intervals if inv.get("annotation")],
        key=lambda i: i["start"],
    )
    if not annotated:
        return None, 0.0

    FONT_SIZE = 11.0
    ROW_H_A   = 18.0

    def cell(text, bold=False, muted=False, color=None):
        lbl = NSTextField.labelWithString_(text)
        lbl.setFont_(NSFont.boldSystemFontOfSize_(FONT_SIZE) if bold
                     else NSFont.systemFontOfSize_(FONT_SIZE))
        if color:
            lbl.setTextColor_(color)
        elif muted:
            lbl.setTextColor_(NSColor.secondaryLabelColor())
        lbl.setSelectable_(True)
        return lbl

    # Header
    rows  = [[cell("Ended", bold=True),
              cell("Tags", bold=True),
              cell("Annotation", bold=True)]]
    csv_rows = [["Ended", "Tags", "Annotation"]]

    for inv in annotated:
        time_str = inv["end"].strftime("%-d %b  %-H:%M")
        tags     = inv.get("tags") or ["(untagged)"]
        tags_str = ", ".join(tags)
        ann      = inv["annotation"]

        # Colour the first tag's dot
        first_tag = tags[0]
        cidx = tag_index.get(first_tag, 0)
        r, g, b = _rgb(cidx)
        dot_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)

        # Build a tags cell with coloured dot prefix
        tags_cell = NSTextField.labelWithString_(f"● {tags_str}")
        tags_cell.setFont_(NSFont.systemFontOfSize_(FONT_SIZE))
        tags_cell.setTextColor_(dot_color)
        tags_cell.setSelectable_(True)

        ann_cell = NSTextField.labelWithString_(ann)
        ann_cell.setFont_(NSFont.systemFontOfSize_(FONT_SIZE))
        ann_cell.setSelectable_(True)

        rows.append([cell(time_str, muted=True), tags_cell, ann_cell])
        csv_rows.append([time_str, tags_str, ann])

    num_rows = len(rows)
    grid = NSGridView.gridViewWithNumberOfColumns_rows_(3, num_rows)
    grid.setRowSpacing_(2.0)
    grid.setColumnSpacing_(12.0)
    grid.setTranslatesAutoresizingMaskIntoConstraints_(False)

    for r_idx, row_cells in enumerate(rows):
        for c_idx, view in enumerate(row_cells):
            grid.cellAtColumnIndex_rowIndex_(c_idx, r_idx).setContentView_(view)

    grid.columnAtIndex_(0).setWidth_(90.0)   # time
    grid.columnAtIndex_(1).setWidth_(160.0)  # tags
    grid.columnAtIndex_(2).setWidth_(500.0)  # annotation (stretches)

    for r_idx in range(num_rows):
        grid.rowAtIndex_(r_idx).setHeight_(ROW_H_A)
    grid.rowAtIndex_(0).setBottomPadding_(4.0)

    natural_h = (num_rows * ROW_H_A
                 + grid.rowSpacing() * (num_rows - 1)
                 + 4.0 + 2.0)
    grid.heightAnchor().constraintEqualToConstant_(natural_h).setActive_(True)

    # wrapper is the NSScrollView's document view — must be frame-based (TAMSIC=True)
    # so the scroll view can set its frame directly.  The grid inside uses Auto Layout.
    wrapper = SummaryTableView.alloc().initWithGrid_csvRows_(grid, csv_rows)
    wrapper.setFrameSize_(NSSize(760, natural_h))

    # Compute initial visible height: full natural height unless it would push
    # the window off screen, in which case cap it (scrollbar appears).
    CHROME     = 32.0 + 30.0   # tab chrome + filter bar
    BOTTOM_PAD = 12.0
    screen_h   = NSScreen.mainScreen().visibleFrame().size.height
    available  = screen_h - CHROME - height_above - BOTTOM_PAD
    available  = max(available, ROW_H_A * 3)   # always show at least 3 rows
    visible_h  = min(natural_h, available)

    sv = NSScrollView.alloc().initWithFrame_(
        NSRect(NSPoint(0, 0), NSSize(760, visible_h))
    )
    sv.setHasVerticalScroller_(True)
    sv.setHasHorizontalScroller_(False)
    sv.setAutohidesScrollers_(True)   # scrollbar only appears when content > visible area
    sv.setBorderType_(0)              # NSNoBorder
    sv.setDocumentView_(wrapper)
    sv.setTranslatesAutoresizingMaskIntoConstraints_(False)
    # NOTE: no heightAnchor constraint here — CollapsibleSection sets and updates
    # _content_h_con on this view directly, so a fixed constraint here would fight it.

    # Return the scroll view, its initial visible height, and the full natural
    # height of the content so callers can later grow it up to natural_h.
    return sv, visible_h, natural_h


# ---------------------------------------------------------------------------
# Collapsible section header
# ---------------------------------------------------------------------------

class CollapsibleSection(NSObject):
    """A disclosure-triangle header that shows/hides a content view.

    Usage
    -----
    sec = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
        "Timeline", my_view, 200.0, rebuild_callback
    )
    # sec.outer  – the NSView to add to your container (header + content)
    # sec.total_h – current outer height (changes when toggled)
    """

    HEADER_H = 22.0

    def initWithTitle_contentView_contentH_onResize_(
            self, title, content_view, content_h, on_resize):
        self = objc.super(CollapsibleSection, self).init()
        if self is None:
            return None
        self._title      = title
        self._content    = content_view
        self._content_h  = float(content_h)
        self._on_resize  = on_resize
        self._expanded   = True

        GAP = 4.0
        self.total_h = self.HEADER_H + GAP + self._content_h

        # ── outer container ─────────────────────────────────────────────────
        outer = NSView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(1000, self.total_h))
        )
        outer.setTranslatesAutoresizingMaskIntoConstraints_(False)

        # ── disclosure button (triangle + label) ────────────────────────────
        btn = NSButton.buttonWithTitle_target_action_(
            f"▾  {title}", self, "toggle:"
        )
        btn.setBezelStyle_(0)          # NSBezelStyleSmallSquare → borderless
        btn.setBordered_(False)
        btn.setFont_(NSFont.boldSystemFontOfSize_(11))
        btn.setContentTintColor_(NSColor.secondaryLabelColor())
        btn.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self._btn = btn

        content_view.setTranslatesAutoresizingMaskIntoConstraints_(False)

        outer.addSubview_(btn)
        outer.addSubview_(content_view)

        # ── height constraints ───────────────────────────────────────────────
        # outer's own height is set by the caller via heightAnchor
        self._outer_h_con = outer.heightAnchor().constraintEqualToConstant_(
            self.total_h)
        self._outer_h_con.setActive_(True)

        # content height constraint — deactivated when collapsed
        self._content_h_con = content_view.heightAnchor().constraintEqualToConstant_(
            self._content_h)
        self._content_h_con.setActive_(True)

        # zero-height constraint used when collapsed (mutually exclusive)
        self._content_zero_con = content_view.heightAnchor().constraintEqualToConstant_(0)
        self._content_zero_con.setActive_(False)

        NSLayoutConstraint.activateConstraints_([
            btn.topAnchor().constraintEqualToAnchor_(outer.topAnchor()),
            btn.leadingAnchor().constraintEqualToAnchor_(outer.leadingAnchor()),
            btn.heightAnchor().constraintEqualToConstant_(self.HEADER_H),

            content_view.topAnchor().constraintEqualToAnchor_constant_(
                btn.bottomAnchor(), GAP),
            content_view.leadingAnchor().constraintEqualToAnchor_(outer.leadingAnchor()),
            content_view.trailingAnchor().constraintEqualToAnchor_(outer.trailingAnchor()),
        ])

        self.outer = outer
        return self

    @objc.python_method
    def set_content_h(self, new_h):
        """Update the content height without triggering the resize callback.

        Used by the outer resize closure to grow/shrink the annotations section
        when other sections are collapsed/expanded.  If the section is currently
        collapsed the stored height is updated but the outer height stays at
        HEADER_H until the user expands again.
        """
        GAP = 4.0
        new_h = float(new_h)
        self._content_h = new_h
        self._content_h_con.setConstant_(new_h)
        if self._expanded:
            self.total_h = self.HEADER_H + GAP + new_h
            self._outer_h_con.setConstant_(self.total_h)

    @objc.typedSelector(b"v@:@")
    def toggle_(self, sender):
        GAP = 4.0
        self._expanded = not self._expanded
        if self._expanded:
            self._btn.setTitle_(f"▾  {self._title}")
            self._content_zero_con.setActive_(False)
            self._content_h_con.setActive_(True)
            self._content.setHidden_(False)
            self.total_h = self.HEADER_H + GAP + self._content_h
        else:
            self._btn.setTitle_(f"▸  {self._title}")
            self._content_h_con.setActive_(False)
            self._content_zero_con.setActive_(True)
            self._content.setHidden_(True)
            self.total_h = self.HEADER_H
        self._outer_h_con.setConstant_(self.total_h)
        if self._on_resize:
            self._on_resize()


# ---------------------------------------------------------------------------
# Report panel builders
# ---------------------------------------------------------------------------

class NavButtonTarget(NSObject):
    """Reusable action target for Prev/Next navigation buttons."""
    def initWithCallback_(self, cb):
        self = objc.super(NavButtonTarget, self).init()
        if self is None:
            return None
        self._cb = cb
        return self

    @objc.signature(b"v@:@")
    def fire_(self, sender):
        self._cb()



# Max visible height for the timeline before it becomes scrollable.
TL_MAX_H = 400.0


def _wrap_tl_scroll(tl_view, tl_h, max_h=TL_MAX_H):
    """Wrap *tl_view* in a vertically-scrolling NSScrollView.

    The scroll view's visible height is min(tl_h, max_h).
    Returns (scroll_view, visible_h).
    """
    visible_h = min(float(tl_h), float(max_h))
    sv = NSScrollView.alloc().initWithFrame_(
        NSRect(NSPoint(0, 0), NSSize(float(TIMELINE_W), visible_h))
    )
    sv.setHasVerticalScroller_(True)
    sv.setHasHorizontalScroller_(False)
    sv.setAutohidesScrollers_(True)
    sv.setBorderType_(0)  # NSNoBorder
    sv.setDocumentView_(tl_view)
    sv.setTranslatesAutoresizingMaskIntoConstraints_(False)
    sv.heightAnchor().constraintEqualToConstant_(visible_h).setActive_(True)
    return sv, visible_h



def build_report_view(period, offset, on_navigate, workday_hours=7.5,
                      show_empty_days=True, filter_tags=None, on_refresh=None,
                      on_resize=None, out_resize_cb=None):
    """Build and return (NSView, height) for the given period and offset.

    period          : "day" | "week" | "month"
    offset          : int, 0 = current, negative = past
    on_navigate     : callable(delta) — called when Prev/Next is clicked
    workday_hours   : daily work target in hours (0 = no target)
    show_empty_days : if False, omit days with no tracked intervals from
                      week/month timeline rows
    filter_tags     : set of tag strings to show, or None to show all
    """
    WORKDAY_SECS = workday_hours * 3600

    def _count_weekdays(start_d, end_d):
        n, d = 0, start_d
        while d <= end_d:
            if d.weekday() < 5:
                n += 1
            d += timedelta(days=1)
        return n

    start_date, end_date, period_title = date_range_for(period, offset)
    today = date.today()

    # Fetch intervals for the exact date range
    start_str = start_date.strftime("%Y-%m-%dT00:00:00")
    end_str    = (end_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    out = run_timew("export", start_str, "-", end_str)
    intervals = _parse_intervals(out)

    # Apply tag filter — keep only intervals that contain at least one
    # selected tag, and trim each interval's tag list to selected tags only.
    if filter_tags is not None:
        filtered = []
        for inv in intervals:
            matched = [t for t in inv["tags"] if t in filter_tags]
            if matched:
                filtered.append({**inv, "tags": matched})
        intervals = filtered

    # Collect all tags in order of first appearance (preserves chronology)
    seen_tags = []
    tag_set = set()
    for inv in intervals:
        for t in inv["tags"] or ["(untagged)"]:
            if t not in tag_set:
                seen_tags.append(t)
                tag_set.add(t)
    all_tags = seen_tags
    tag_index = {t: i for i, t in enumerate(all_tags)}

    # Aggregate totals
    tag_totals = defaultdict(float)
    for inv in intervals:
        for t in inv["tags"] or ["(untagged)"]:
            tag_totals[t] += inv["duration"]
    grand_total = sum(tag_totals.values())

    # Group intervals by local date
    by_date = defaultdict(list)
    for inv in intervals:
        by_date[inv["start"].date()].append(inv)

    # Build timeline rows
    if period == "day":
        rows = [(start_date.strftime("%-d %b"), start_date,
                 by_date.get(start_date, []))]
        target_secs = WORKDAY_SECS if start_date.weekday() < 5 else 0.0
    elif period == "week":
        rows = []
        d = start_date
        while d <= end_date:
            day_invs = by_date.get(d, [])
            if day_invs or show_empty_days or d == date.today():
                rows.append((d.strftime("%a %-d"), d, day_invs))
            d += timedelta(days=1)
        target_secs = _count_weekdays(start_date, end_date) * WORKDAY_SECS
    else:  # month
        rows = []
        d = start_date
        while d <= end_date:
            day_invs = by_date.get(d, [])
            if day_invs or show_empty_days or d == date.today():
                rows.append((d.strftime("%-d %b"), d, day_invs))
            d += timedelta(days=1)
        target_secs = _count_weekdays(start_date, end_date) * WORKDAY_SECS

    # --- Compute tight hour bounds across all intervals in this view ---
    if intervals:
        hour_start = min(inv["start"].hour for inv in intervals)
        hour_end   = max(inv["end"].hour + (1 if inv["end"].minute or inv["end"].second else 0)
                         for inv in intervals)
        # Also include "now" if today is in the view and tracking is active
        now_dt = datetime.now().astimezone()
        if any(r[1] == date.today() for r in rows):
            hour_end = max(hour_end, now_dt.hour + 1)
        # Clamp to valid range
        hour_start = max(0, hour_start)
        hour_end   = min(24, hour_end)
        # Ensure at least a 1-hour span
        if hour_end <= hour_start:
            hour_end = min(24, hour_start + 1)
    else:
        hour_start, hour_end = 0, 24

    # --- Build the header stack (title + summary + prev/next buttons) ---
    if target_secs:
        pct_of_target = grand_total / target_secs * 100
        summary_text = (
            f"{seconds_to_hm(grand_total)} tracked"
            f"  /  target {seconds_to_hm(target_secs)}"
            f"  ({pct_of_target:.0f}%)"
        )
    else:
        summary_text = f"{seconds_to_hm(grand_total)} tracked"

    prev_target = NavButtonTarget.alloc().initWithCallback_(lambda: on_navigate(-1))
    next_target = NavButtonTarget.alloc().initWithCallback_(lambda: on_navigate(+1))

    btn_prev = NSButton.buttonWithTitle_target_action_("◀", prev_target, "fire:")
    btn_prev.setBezelStyle_(4)
    btn_next = NSButton.buttonWithTitle_target_action_("▶", next_target, "fire:")
    btn_next.setBezelStyle_(4)
    # Disable Next when already at offset 0 (current period)
    if offset >= 0:
        btn_next.setEnabled_(False)

    # Keep targets alive for the lifetime of the buttons (NSButton holds
    # only a weak/unretained reference to its target in AppKit).
    # Use objc.setAssociatedObject to attach a strong Python ref to each button.
    objc.setAssociatedObject(btn_prev, b"nav_target", prev_target,
                             objc.OBJC_ASSOCIATION_RETAIN)
    objc.setAssociatedObject(btn_next, b"nav_target", next_target,
                             objc.OBJC_ASSOCIATION_RETAIN)

    header = NSStackView.stackViewWithViews_([])
    header.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    header.setSpacing_(8.0)
    header.addView_inGravity_(btn_prev, 1)
    header.addView_inGravity_(make_label(period_title, size=14, bold=True), 1)
    header.addView_inGravity_(btn_next, 1)
    header.addView_inGravity_(make_label(summary_text, size=11, muted=True), 3)
    header.setTranslatesAutoresizingMaskIntoConstraints_(False)

    # --- No-data path ---
    if not intervals:
        container = NSStackView.stackViewWithViews_([])
        container.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        container.setSpacing_(8.0)
        container.setEdgeInsets_((12.0, 16.0, 16.0, 16.0))
        container.addView_inGravity_(header, 1)
        container.addView_inGravity_(
            make_label("No tracked time in this period.", size=12, muted=True), 1
        )
        return container, 120.0

    PAD     = 12.0
    GAP_SEC = 8.0
    header_h = header.fittingSize().height

    # --- Timeline view wrapped in a scroll view ---
    tl_row_h = ROW_H // 2 if period == "month" else ROW_H
    tl_view = TimelineView.alloc().initWithRows_tagIndex_hourStart_hourEnd_rowH_startDate_endDate_filterTags_(
        rows, tag_index, hour_start, hour_end, tl_row_h,
        start_date, end_date, filter_tags,
    )
    if on_refresh is not None:
        tl_view._on_refresh = on_refresh
    tl_h = tl_view._height
    tl_scroll, tl_visible_h = _wrap_tl_scroll(tl_view, tl_h)

    # --- Legend (fixed height) ---
    legend_view = LegendView.alloc().initWithAllTags_tagIndex_(all_tags, tag_index)
    legend_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    legend_view.heightAnchor().constraintEqualToConstant_(float(LegendView.LEGEND_H)).setActive_(True)

    # timeline section = scroll + legend stacked
    tl_section_inner = NSView.alloc().initWithFrame_(
        NSRect(NSPoint(0, 0), NSSize(1000, tl_visible_h + GAP_SEC + LegendView.LEGEND_H))
    )
    tl_section_inner.setTranslatesAutoresizingMaskIntoConstraints_(False)
    tl_section_inner.addSubview_(tl_scroll)
    tl_section_inner.addSubview_(legend_view)
    NSLayoutConstraint.activateConstraints_([
        tl_scroll.topAnchor().constraintEqualToAnchor_(tl_section_inner.topAnchor()),
        tl_scroll.leadingAnchor().constraintEqualToAnchor_(tl_section_inner.leadingAnchor()),
        tl_scroll.trailingAnchor().constraintEqualToAnchor_(tl_section_inner.trailingAnchor()),
        legend_view.topAnchor().constraintEqualToAnchor_constant_(tl_scroll.bottomAnchor(), GAP_SEC),
        legend_view.leadingAnchor().constraintEqualToAnchor_(tl_section_inner.leadingAnchor()),
        legend_view.trailingAnchor().constraintEqualToAnchor_(tl_section_inner.trailingAnchor()),
    ])
    tl_inner_h = tl_visible_h + GAP_SEC + LegendView.LEGEND_H
    # No heightAnchor here — CollapsibleSection owns this view's height via _content_h_con.

    # --- Bottom row: pie (left) + summary table (right, compact) ---
    pie_view = PieView.alloc().initWithAllTags_tagIndex_tagTotals_targetSecs_(
        all_tags, tag_index, tag_totals, target_secs
    )
    pie_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    pie_view.widthAnchor().constraintEqualToConstant_(PieView.PIE_SIZE).setActive_(True)
    pie_view.heightAnchor().constraintEqualToConstant_(PieView.PIE_SIZE).setActive_(True)

    grid = build_summary_table(all_tags, tag_totals, grand_total, target_secs, tag_index,
                               period_title=period_title)

    bottom_row = NSStackView.stackViewWithViews_([])
    bottom_row.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    bottom_row.setSpacing_(24.0)
    bottom_row.setAlignment_(NSLayoutAttributeTop)
    bottom_row.addView_inGravity_(pie_view, 1)
    bottom_row.addView_inGravity_(grid, 1)
    bottom_row.setTranslatesAutoresizingMaskIntoConstraints_(False)

    grid_h   = grid.fittingSize().height
    bottom_h = max(PieView.PIE_SIZE, grid_h)
    # No heightAnchor here — CollapsibleSection owns this view's height via _content_h_con.

    height_above = (PAD + header_h + GAP_SEC
                    + CollapsibleSection.HEADER_H + GAP_SEC + tl_inner_h
                    + GAP_SEC
                    + CollapsibleSection.HEADER_H + GAP_SEC + bottom_h
                    + GAP_SEC)

    # --- Annotations table (optional) ---
    ann_result = build_annotations_table(intervals, tag_index, height_above)
    if ann_result[0] is not None:
        ann_view, ann_h, ann_natural_h = ann_result
    else:
        ann_view, ann_h, ann_natural_h = None, 0.0, 0.0

    # --- Collapsible sections ------------------------------------------------
    sections = []   # keep alive
    sec_ann_ref = [None]  # mutable cell for closure

    def _make_resize_cb():
        def _cb(avail_h=None):
            # If there's an annotations section, grow/shrink it to fill the
            # available space.  avail_h is the window content height available
            # to us; when None we compute from the screen (section-toggle case).
            from_window_resize = avail_h is not None
            if sec_ann_ref[0] is not None:
                # Everything in the container except sec_ann's scroll-view content:
                # top PAD + header + GAP + other sections (each with trailing GAP_SEC)
                # + sec_ann's own HEADER_H + GAP + trailing GAP_SEC + bottom PAD
                fixed_h = (PAD + header_h + GAP_SEC
                           + sum(s.total_h + GAP_SEC
                                 for s in sections if s is not sec_ann_ref[0])
                           + CollapsibleSection.HEADER_H + 4.0   # sec_ann header + inner GAP
                           + GAP_SEC + PAD)                       # trailing gap + bottom pad
                if not from_window_resize:
                    from AppKit import NSScreen as _NSS
                    screen_h   = _NSS.mainScreen().visibleFrame().size.height
                    CHROME     = 32.0 + 30.0
                    BOTTOM_PAD = 12.0
                    avail_h    = screen_h - CHROME - BOTTOM_PAD
                avail     = avail_h - fixed_h
                avail     = max(avail, 18.0 * 3)
                new_ann_h = min(ann_natural_h, avail)
                sec_ann_ref[0].set_content_h(new_ann_h)

            new_h = (PAD + header_h + GAP_SEC
                     + sum(s.total_h + GAP_SEC for s in sections)
                     + PAD)
            container.setFrameSize_(NSSize(container.frame().size.width, new_h))
            container_h_con[0].setConstant_(new_h)
            # Don't call on_resize when responding to a window resize — we're
            # already inside it and don't want to resize the window again.
            if on_resize and not from_window_resize:
                on_resize(new_h)
        if out_resize_cb is not None:
            out_resize_cb[0] = _cb
        return _cb

    resize_cb = _make_resize_cb()

    sec_tl  = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
        "Timeline", tl_section_inner, tl_inner_h, resize_cb)
    sec_sum = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
        "Summary", bottom_row, bottom_h, resize_cb)
    sections += [sec_tl, sec_sum]

    if ann_view:
        sec_ann = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
            "Annotations", ann_view, ann_h, resize_cb)
        sections.append(sec_ann)
        sec_ann_ref[0] = sec_ann
    else:
        sec_ann = None

    # --- Outer container ----------------------------------------------------
    total_h = (PAD + header_h + GAP_SEC
               + sum(s.total_h + GAP_SEC for s in sections)
               + PAD)

    container = NSView.alloc().initWithFrame_(
        NSRect(NSPoint(0, 0), NSSize(1000, total_h))
    )

    # mutable cell so the resize closure can update it
    container_h_con = [container.heightAnchor().constraintEqualToConstant_(total_h)]
    container_h_con[0].setActive_(True)

    header.setTranslatesAutoresizingMaskIntoConstraints_(False)
    for s in sections:
        container.addSubview_(s.outer)
    container.addSubview_(header)

    # chain: header → sec_tl → sec_sum → [sec_ann]
    prev_anchor = header.bottomAnchor()
    constraints = [
        header.topAnchor().constraintEqualToAnchor_constant_(container.topAnchor(), PAD),
        header.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), PAD),
        header.trailingAnchor().constraintEqualToAnchor_constant_(container.trailingAnchor(), -PAD),
    ]
    for s in sections:
        constraints += [
            s.outer.topAnchor().constraintEqualToAnchor_constant_(prev_anchor, GAP_SEC),
            s.outer.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), PAD),
            s.outer.trailingAnchor().constraintEqualToAnchor_constant_(container.trailingAnchor(), -PAD),
        ]
        prev_anchor = s.outer.bottomAnchor()
    NSLayoutConstraint.activateConstraints_(constraints)

    # keep section objects alive on the container view
    objc.setAssociatedObject(container, b"_sections", sections,
                             objc.OBJC_ASSOCIATION_RETAIN)

    return container, total_h


def build_custom_report_view(start_date, end_date, on_show,
                             workday_hours=7.5, show_empty_days=True,
                             filter_tags=None, on_refresh=None, on_resize=None,
                             out_resize_cb=None):
    """Build report view for an explicit start_date..end_date range.

    on_show : callable() — called when the user clicks Show (no-op after first build)
    Returns (NSView, height).
    """
    from datetime import date as _date
    WORKDAY_SECS = workday_hours * 3600

    def _count_weekdays(s, e):
        n, d = 0, s
        while d <= e:
            if d.weekday() < 5:
                n += 1
            d += timedelta(days=1)
        return n

    today = _date.today()
    if end_date > today:
        end_date = today

    period_title = (f"{start_date.strftime('%-d %b %Y')} – "
                    f"{end_date.strftime('%-d %b %Y')}")

    start_str = start_date.strftime("%Y-%m-%dT00:00:00")
    end_str   = (end_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    out = run_timew("export", start_str, "-", end_str)
    intervals = _parse_intervals(out)

    if filter_tags is not None:
        filtered = []
        for inv in intervals:
            matched = [t for t in inv["tags"] if t in filter_tags]
            if matched:
                filtered.append({**inv, "tags": matched})
        intervals = filtered

    seen_tags, tag_set = [], set()
    for inv in intervals:
        for t in inv["tags"] or ["(untagged)"]:
            if t not in tag_set:
                seen_tags.append(t); tag_set.add(t)
    all_tags = seen_tags
    tag_index = {t: i for i, t in enumerate(all_tags)}

    tag_totals = defaultdict(float)
    for inv in intervals:
        for t in inv["tags"] or ["(untagged)"]:
            tag_totals[t] += inv["duration"]
    grand_total = sum(tag_totals.values())

    by_date = defaultdict(list)
    for inv in intervals:
        by_date[inv["start"].date()].append(inv)

    rows = []
    d = start_date
    while d <= end_date:
        day_invs = by_date.get(d, [])
        if day_invs or show_empty_days or d == today:
            rows.append((d.strftime("%-d %b"), d, day_invs))
        d += timedelta(days=1)

    target_secs = _count_weekdays(start_date, end_date) * WORKDAY_SECS

    if intervals:
        hour_start = min(inv["start"].hour for inv in intervals)
        hour_end   = max(inv["end"].hour + (1 if inv["end"].minute or inv["end"].second else 0)
                         for inv in intervals)
        now_dt = datetime.now().astimezone()
        if any(r[1] == today for r in rows):
            hour_end = max(hour_end, now_dt.hour + 1)
        hour_start = max(0, hour_start)
        hour_end   = min(24, hour_end)
        if hour_end <= hour_start:
            hour_end = min(24, hour_start + 1)
    else:
        hour_start, hour_end = 0, 24

    if target_secs:
        pct = grand_total / target_secs * 100
        summary_text = (f"{seconds_to_hm(grand_total)} tracked"
                        f"  /  target {seconds_to_hm(target_secs)}"
                        f"  ({pct:.0f}%)")
    else:
        summary_text = f"{seconds_to_hm(grand_total)} tracked"

    header = NSStackView.stackViewWithViews_([])
    header.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    header.setSpacing_(8.0)
    header.addView_inGravity_(make_label(period_title, size=14, bold=True), 1)
    header.addView_inGravity_(make_label(summary_text, size=11, muted=True), 3)
    header.setTranslatesAutoresizingMaskIntoConstraints_(False)

    if not intervals:
        container = NSStackView.stackViewWithViews_([])
        container.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        container.setSpacing_(8.0)
        container.setEdgeInsets_((12.0, 16.0, 16.0, 16.0))
        container.addView_inGravity_(header, 1)
        container.addView_inGravity_(
            make_label("No tracked time in this period.", size=12, muted=True), 1)
        return container, 120.0

    PAD     = 12.0
    GAP_SEC = 8.0
    header_h = header.fittingSize().height

    # Use compact row height when range spans more than 7 days
    span = (end_date - start_date).days + 1
    tl_row_h = ROW_H // 2 if span > 7 else ROW_H
    tl_view = TimelineView.alloc().initWithRows_tagIndex_hourStart_hourEnd_rowH_startDate_endDate_filterTags_(
        rows, tag_index, hour_start, hour_end, tl_row_h,
        start_date, end_date, filter_tags,
    )
    if on_refresh is not None:
        tl_view._on_refresh = on_refresh
    tl_h = tl_view._height
    tl_scroll, tl_visible_h = _wrap_tl_scroll(tl_view, tl_h)

    legend_view = LegendView.alloc().initWithAllTags_tagIndex_(all_tags, tag_index)
    legend_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    legend_view.heightAnchor().constraintEqualToConstant_(float(LegendView.LEGEND_H)).setActive_(True)

    tl_section_inner = NSView.alloc().initWithFrame_(
        NSRect(NSPoint(0, 0), NSSize(1000, tl_visible_h + GAP_SEC + LegendView.LEGEND_H))
    )
    tl_section_inner.setTranslatesAutoresizingMaskIntoConstraints_(False)
    tl_section_inner.addSubview_(tl_scroll)
    tl_section_inner.addSubview_(legend_view)
    NSLayoutConstraint.activateConstraints_([
        tl_scroll.topAnchor().constraintEqualToAnchor_(tl_section_inner.topAnchor()),
        tl_scroll.leadingAnchor().constraintEqualToAnchor_(tl_section_inner.leadingAnchor()),
        tl_scroll.trailingAnchor().constraintEqualToAnchor_(tl_section_inner.trailingAnchor()),
        legend_view.topAnchor().constraintEqualToAnchor_constant_(tl_scroll.bottomAnchor(), GAP_SEC),
        legend_view.leadingAnchor().constraintEqualToAnchor_(tl_section_inner.leadingAnchor()),
        legend_view.trailingAnchor().constraintEqualToAnchor_(tl_section_inner.trailingAnchor()),
    ])
    tl_inner_h = tl_visible_h + GAP_SEC + LegendView.LEGEND_H
    # No heightAnchor here — CollapsibleSection owns this view's height via _content_h_con.

    pie_view = PieView.alloc().initWithAllTags_tagIndex_tagTotals_targetSecs_(
        all_tags, tag_index, tag_totals, target_secs)
    pie_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    pie_view.widthAnchor().constraintEqualToConstant_(PieView.PIE_SIZE).setActive_(True)
    pie_view.heightAnchor().constraintEqualToConstant_(PieView.PIE_SIZE).setActive_(True)

    grid = build_summary_table(all_tags, tag_totals, grand_total, target_secs,
                               tag_index, period_title=period_title)

    bottom_row = NSStackView.stackViewWithViews_([])
    bottom_row.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    bottom_row.setSpacing_(24.0)
    bottom_row.setAlignment_(NSLayoutAttributeTop)
    bottom_row.addView_inGravity_(pie_view, 1)
    bottom_row.addView_inGravity_(grid, 1)
    bottom_row.setTranslatesAutoresizingMaskIntoConstraints_(False)

    grid_h   = grid.fittingSize().height
    bottom_h = max(PieView.PIE_SIZE, grid_h)
    # No heightAnchor here — CollapsibleSection owns this view's height via _content_h_con.

    height_above = (PAD + header_h + GAP_SEC
                    + CollapsibleSection.HEADER_H + GAP_SEC + tl_inner_h
                    + GAP_SEC
                    + CollapsibleSection.HEADER_H + GAP_SEC + bottom_h
                    + GAP_SEC)

    ann_result = build_annotations_table(intervals, tag_index, height_above)
    if ann_result[0] is not None:
        ann_view, ann_h, ann_natural_h = ann_result
    else:
        ann_view, ann_h, ann_natural_h = None, 0.0, 0.0

    sections = []
    sec_ann_ref = [None]

    def _make_resize_cb():
        def _cb(avail_h=None):
            from_window_resize = avail_h is not None
            if sec_ann_ref[0] is not None:
                fixed_h = (PAD + header_h + GAP_SEC
                           + sum(s.total_h + GAP_SEC
                                 for s in sections if s is not sec_ann_ref[0])
                           + CollapsibleSection.HEADER_H + 4.0
                           + GAP_SEC + PAD)
                if not from_window_resize:
                    from AppKit import NSScreen as _NSS
                    screen_h   = _NSS.mainScreen().visibleFrame().size.height
                    CHROME     = 32.0 + 30.0
                    BOTTOM_PAD = 12.0
                    avail_h    = screen_h - CHROME - BOTTOM_PAD
                avail      = avail_h - fixed_h
                avail      = max(avail, 18.0 * 3)
                new_ann_h  = min(ann_natural_h, avail)
                sec_ann_ref[0].set_content_h(new_ann_h)

            new_h = (PAD + header_h + GAP_SEC
                     + sum(s.total_h + GAP_SEC for s in sections)
                     + PAD)
            container.setFrameSize_(NSSize(container.frame().size.width, new_h))
            container_h_con[0].setConstant_(new_h)
            if on_resize and not from_window_resize:
                on_resize(new_h)
        if out_resize_cb is not None:
            out_resize_cb[0] = _cb
        return _cb

    resize_cb = _make_resize_cb()

    sec_tl  = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
        "Timeline", tl_section_inner, tl_inner_h, resize_cb)
    sec_sum = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
        "Summary", bottom_row, bottom_h, resize_cb)
    sections += [sec_tl, sec_sum]

    if ann_view:
        sec_ann = CollapsibleSection.alloc().initWithTitle_contentView_contentH_onResize_(
            "Annotations", ann_view, ann_h, resize_cb)
        sections.append(sec_ann)
        sec_ann_ref[0] = sec_ann

    total_h = (PAD + header_h + GAP_SEC
               + sum(s.total_h + GAP_SEC for s in sections)
               + PAD)

    container = NSView.alloc().initWithFrame_(
        NSRect(NSPoint(0, 0), NSSize(1000, total_h)))

    container_h_con = [container.heightAnchor().constraintEqualToConstant_(total_h)]
    container_h_con[0].setActive_(True)

    header.setTranslatesAutoresizingMaskIntoConstraints_(False)
    container.addSubview_(header)
    for s in sections:
        container.addSubview_(s.outer)

    prev_anchor = header.bottomAnchor()
    constraints = [
        header.topAnchor().constraintEqualToAnchor_constant_(container.topAnchor(), PAD),
        header.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), PAD),
        header.trailingAnchor().constraintEqualToAnchor_constant_(container.trailingAnchor(), -PAD),
    ]
    for s in sections:
        constraints += [
            s.outer.topAnchor().constraintEqualToAnchor_constant_(prev_anchor, GAP_SEC),
            s.outer.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), PAD),
            s.outer.trailingAnchor().constraintEqualToAnchor_constant_(container.trailingAnchor(), -PAD),
        ]
        prev_anchor = s.outer.bottomAnchor()
    NSLayoutConstraint.activateConstraints_(constraints)

    objc.setAssociatedObject(container, b"_sections", sections,
                             objc.OBJC_ASSOCIATION_RETAIN)
    return container, total_h


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

def export_logbook_pdf(start_date, end_date, period_title, intervals,
                       all_tags, tag_index, tag_totals, grand_total,
                       target_secs, rows, hour_start, hour_end, path):
    """Write a multi-page PDF to *path*.

    Page 1 – pie chart (large, centred) + summary table
    Page 2 – timeline + annotations if they fit; otherwise just timeline
    Page 3 – annotations (only when they don't fit on page 2)

    Uses only AppKit/Foundation (NSGraphicsContext PDF mode) so it works in
    the py2app bundle without the separate pyobjc-framework-Quartz package.
    """
    import math
    from Foundation import NSMutableData

    PAGE_W = 792.0   # US Letter landscape pts
    PAGE_H = 612.0
    MARGIN = 36.0

    # ── drawing helpers (AppKit only) ────────────────────────────────────────

    def _ns_color(r, g, b, a=1.0):
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)

    def _fill_rect(x, y, w, h):
        NSBezierPath.fillRect_(NSRect(NSPoint(x, y), NSSize(w, h)))

    def _stroke_line(x0, y0, x1, y1, lw=0.5):
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(lw)
        p.moveToPoint_(NSPoint(x0, y0))
        p.lineToPoint_(NSPoint(x1, y1))
        p.stroke()

    def _stroke_rect(x, y, w, h, lw=0.75):
        p = NSBezierPath.bezierPathWithRect_(NSRect(NSPoint(x, y), NSSize(w, h)))
        p.setLineWidth_(lw)
        p.stroke()

    def _draw_text(text, x, y, size=10.0, bold=False, r=0.0, g=0.0, b=0.0, a=1.0):
        font = (NSFont.boldSystemFontOfSize_(size) if bold
                else NSFont.systemFontOfSize_(size))
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: _ns_color(r, g, b, a),
        }
        NSString.stringWithString_(text).drawAtPoint_withAttributes_(
            NSPoint(x, y), attrs)

    def _text_width(text, size=10.0, bold=False):
        font = (NSFont.boldSystemFontOfSize_(size) if bold
                else NSFont.systemFontOfSize_(size))
        attrs = {NSFontAttributeName: font}
        return NSString.stringWithString_(text).sizeWithAttributes_(attrs).width

    def _tag_rgb(cidx):
        return _rgb(cidx)   # returns (r, g, b)

    # ── page header ──────────────────────────────────────────────────────────

    def _draw_page_header(title, subtitle=""):
        _ns_color(0.15, 0.15, 0.15).set()
        _draw_text(title, MARGIN, PAGE_H - MARGIN - 2, size=14.0, bold=True,
                   r=0.15, g=0.15, b=0.15)
        if subtitle:
            _draw_text(subtitle, MARGIN, PAGE_H - MARGIN - 16, size=9.0,
                       r=0.45, g=0.45, b=0.45)

    # ── PAGE 1: pie + summary table ──────────────────────────────────────────

    def _build_subtitle():
        if target_secs:
            pct = grand_total / target_secs * 100
            return (f"{seconds_to_hm(grand_total)} tracked  /  "
                    f"target {seconds_to_hm(target_secs)}  ({pct:.0f}%)")
        return f"{seconds_to_hm(grand_total)} tracked"

    def _draw_page1():
        _draw_page_header(f"Logbook — {period_title}", subtitle=_build_subtitle())

        PIE_R = 150.0
        pie_cx = MARGIN + PIE_R + 10.0
        pie_cy = PAGE_H / 2.0 - 10.0

        _draw_pie(pie_cx, pie_cy, PIE_R)

        table_x = pie_cx + PIE_R + 30.0
        table_y = PAGE_H - MARGIN - 36.0
        _draw_summary_table(table_x, table_y)

    def _draw_pie(cx, cy, radius):
        tracked = sum(tag_totals.get(t, 0) for t in all_tags)
        denom = max(tracked, target_secs) or 1.0
        remainder = max(0.0, target_secs - tracked)

        angle = 90.0   # 12 o'clock in AppKit degrees (CCW from east)

        for tag in all_tags:
            secs = tag_totals.get(tag, 0)
            if secs == 0:
                continue
            sweep = (secs / denom) * 360.0
            r, g, b = _tag_rgb(tag_index.get(tag, 0))
            _nsbez_pie_slice(cx, cy, radius, angle, sweep, r, g, b, 0.80)
            angle -= sweep

        if remainder > 0:
            sweep = (remainder / denom) * 360.0
            _nsbez_pie_slice(cx, cy, radius, angle, sweep, 0.75, 0.75, 0.75, 0.35)

    def _nsbez_pie_slice(cx, cy, radius, start_deg, sweep_deg, r, g, b, alpha):
        path = NSBezierPath.bezierPath()
        path.moveToPoint_(NSPoint(cx, cy))
        path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            NSPoint(cx, cy), radius, start_deg, start_deg - sweep_deg, True)
        path.closePath()
        _ns_color(r, g, b, alpha).set()
        path.fill()
        _ns_color(r, g, b, min(1.0, alpha + 0.15)).set()
        path.setLineWidth_(0.75)
        path.stroke()

    def _draw_legend_below_pie(cx, bottom_y):
        SWATCH = 10.0
        GAP    = 5.0
        SIZE   = 9.0
        items = []
        for tag in all_tags:
            if tag_totals.get(tag, 0) == 0:
                continue
            items.append((tag, _text_width(tag, size=SIZE) + SWATCH + GAP + 10.0))

        rows_list, row_w = [[]], 0.0
        for tag, w in items:
            if row_w + w > 340.0 and rows_list[-1]:
                rows_list.append([])
                row_w = 0.0
            rows_list[-1].append((tag, w))
            row_w += w

        y = bottom_y
        for row in reversed(rows_list):
            x = cx - sum(w for _, w in row) / 2.0
            for tag, w in row:
                r, g, b = _tag_rgb(tag_index.get(tag, 0))
                _ns_color(r, g, b, 0.85).set()
                _fill_rect(x, y, SWATCH, SWATCH)
                _draw_text(tag, x + SWATCH + GAP, y, size=SIZE,
                           r=0.15, g=0.15, b=0.15)
                x += w
            y -= 14.0

    def _draw_summary_table(x, y):
        ROW_H = 16.0
        sorted_tags = [t for t in sorted(all_tags, key=lambda t: -tag_totals.get(t, 0))
                       if tag_totals.get(t, 0) > 0]

        _draw_text("Tag",      x + 12,  y, size=9.0, bold=True, r=0.2, g=0.2, b=0.2)
        _draw_text("Time",     x + 165, y, size=9.0, bold=True, r=0.2, g=0.2, b=0.2)
        _draw_text("% period", x + 225, y, size=9.0, bold=True, r=0.2, g=0.2, b=0.2)
        if target_secs:
            _draw_text("% target", x + 290, y, size=9.0, bold=True, r=0.2, g=0.2, b=0.2)
        y -= ROW_H * 0.6
        _ns_color(0.7, 0.7, 0.7).set()
        _stroke_line(x, y, x + 360, y)
        y -= ROW_H * 0.9

        for tag in sorted_tags:
            secs  = tag_totals.get(tag, 0)
            pct_p = (secs / grand_total * 100) if grand_total else 0
            r, g, b = _tag_rgb(tag_index.get(tag, 0))
            _ns_color(r, g, b, 1.0).set()
            _fill_rect(x, y + 1, 8, 8)
            _draw_text(tag,                 x + 12,  y, size=9.0, r=0.15, g=0.15, b=0.15)
            _draw_text(seconds_to_hm(secs), x + 165, y, size=9.0, r=0.15, g=0.15, b=0.15)
            _draw_text(f"{pct_p:.1f}%",     x + 225, y, size=9.0, r=0.45, g=0.45, b=0.45)
            if target_secs:
                _draw_text(f"{secs / target_secs * 100:.1f}%",
                           x + 290, y, size=9.0, r=0.45, g=0.45, b=0.45)
            y -= ROW_H

        y -= 4.0
        _ns_color(0.7, 0.7, 0.7).set()
        _stroke_line(x, y + ROW_H * 0.8, x + 360, y + ROW_H * 0.8)
        _draw_text("Total",                    x + 12,  y, size=9.0, bold=True, r=0.15, g=0.15, b=0.15)
        _draw_text(seconds_to_hm(grand_total), x + 165, y, size=9.0, bold=True, r=0.15, g=0.15, b=0.15)
        _draw_text("100%",                     x + 225, y, size=9.0, bold=True, r=0.45, g=0.45, b=0.45)

    # ── PAGE 2: timeline ─────────────────────────────────────────────────────

    _PDF_LABEL_W  = 52.0
    _PDF_PAD_TOP  = 18.0
    _PDF_PAD_BOT  = 6.0
    _PDF_ROW_GAP  = 2.0
    # Available vertical space for all rows (header_h reserved at top)
    _TL_AVAIL     = PAGE_H - MARGIN - 36.0 - _PDF_PAD_TOP - _PDF_PAD_BOT

    def _tl_layout():
        # Count lanes per row (capped at 2 so one busy day can't dominate)
        lane_counts = []
        for _lbl, _day, invs in rows:
            if not invs:
                lane_counts.append(1)
                continue
            sorted_i = sorted(invs, key=lambda i: i["start"])
            lanes = []
            for inv in sorted_i:
                li = 0
                while li < len(lanes) and lanes[li] > inv["start"]:
                    li += 1
                if li == len(lanes):
                    lanes.append(inv["end"])
                else:
                    lanes[li] = inv["end"]
            lane_counts.append(min(len(lanes), 2))   # cap at 2 lanes

        total_units = sum(lane_counts) + _PDF_ROW_GAP * len(rows)
        # Row height that makes everything fit; floor at 4 pt, ceil at 14 pt
        rh = max(4.0, min(14.0, _TL_AVAIL / total_units if total_units else 14.0))
        row_heights = [c * rh for c in lane_counts]
        total = sum(row_heights) + _PDF_ROW_GAP * len(rows)
        return row_heights, _PDF_PAD_TOP + total + _PDF_PAD_BOT, rh

    def _draw_timeline(y_offset):
        row_heights, tl_h, rh = _tl_layout()
        graph_x0    = MARGIN + _PDF_LABEL_W
        graph_w     = PAGE_W - MARGIN - _PDF_LABEL_W - MARGIN
        total_hours = hour_end - hour_start

        def x_for_hour(h):
            return graph_x0 + ((h - hour_start) / total_hours) * graph_w

        def x_for_dt(dt, ts, te):
            span = (te - ts).total_seconds()
            if span <= 0:
                return graph_x0
            return graph_x0 + max(0.0, min(1.0,
                (dt - ts).total_seconds() / span)) * graph_w

        # Hour grid + labels
        for h in range(hour_start, hour_end + 1):
            xh = x_for_hour(h)
            _ns_color(0.8, 0.8, 0.8).set()
            _stroke_line(xh, y_offset - _PDF_PAD_TOP + 2,
                         xh, y_offset - tl_h + _PDF_PAD_BOT, lw=0.4)
            _draw_text(f"{h:02d}", xh - 6, y_offset - _PDF_PAD_TOP + 4,
                       size=7.0, r=0.5, g=0.5, b=0.5)

        tz    = local_tz()
        today = date.today()
        y_cur = y_offset - _PDF_PAD_TOP - 4

        for row_idx, (label_str, day_date, invs) in enumerate(rows):
            rh     = row_heights[row_idx]
            y_base = y_cur - rh

            if day_date == today:
                _ns_color(0.23, 0.51, 0.82, 0.06).set()
                _fill_rect(graph_x0, y_base, graph_w, rh)

            lbl_r, lbl_g, lbl_b = ((0.23, 0.51, 0.82) if day_date == today
                                    else (0.45, 0.45, 0.45))
            _draw_text(label_str, MARGIN, y_base + rh / 2.0 - 4,
                       size=7.0, r=lbl_r, g=lbl_g, b=lbl_b)

            if invs:
                ts = datetime(day_date.year, day_date.month, day_date.day,
                              hour_start, 0, 0, tzinfo=tz)
                te = (datetime(day_date.year, day_date.month, day_date.day,
                               0, 0, 0, tzinfo=tz) + timedelta(hours=hour_end))
                flat = []
                for inv in invs:
                    for tag in inv["tags"] or ["(untagged)"]:
                        flat.append({"start": inv["start"], "end": inv["end"],
                                     "tag": tag})
                flat.sort(key=lambda i: i["start"])
                lanes, lane_data = [], []
                for fi in flat:
                    li = 0
                    while li < len(lanes) and lanes[li] > fi["start"]:
                        li += 1
                    if li == len(lanes):
                        lanes.append(fi["end"])
                    else:
                        lanes[li] = fi["end"]
                    lane_data.append((fi, li))

                lane_h = rh / max(len(lanes), 1)
                for fi, li in lane_data:
                    x0 = x_for_dt(fi["start"], ts, te)
                    x1 = x_for_dt(fi["end"],   ts, te)
                    if x1 - x0 < 2.0:
                        x1 = x0 + 2.0
                    ly   = y_base + li * lane_h + 1.0
                    bh   = lane_h - 1.5
                    r, g, b = _tag_rgb(tag_index.get(fi["tag"], 0))
                    _ns_color(r, g, b, 0.40).set()
                    _fill_rect(x0, ly, x1 - x0, bh)
                    _ns_color(r, g, b, 0.90).set()
                    _stroke_rect(x0, ly, x1 - x0, bh, lw=0.5)

            y_cur = y_base - _PDF_ROW_GAP

        return tl_h

    # ── annotations ──────────────────────────────────────────────────────────

    ANN_ROW_H = 13.0

    def _annotated():
        return sorted([inv for inv in intervals if inv.get("annotation")],
                      key=lambda i: i["start"])

    def _ann_height(ann_list):
        return (len(ann_list) + 1) * ANN_ROW_H + 16.0

    def _draw_annotations(ann_list, x, y_top):
        _draw_text("Annotations", x, y_top, size=10.0, bold=True,
                   r=0.15, g=0.15, b=0.15)
        y = y_top - 14.0
        _draw_text("Ended",      x,       y, size=8.0, bold=True, r=0.3, g=0.3, b=0.3)
        _draw_text("Tags",       x + 90,  y, size=8.0, bold=True, r=0.3, g=0.3, b=0.3)
        _draw_text("Annotation", x + 220, y, size=8.0, bold=True, r=0.3, g=0.3, b=0.3)
        y -= ANN_ROW_H * 0.5
        _ns_color(0.75, 0.75, 0.75).set()
        _stroke_line(x, y, PAGE_W - MARGIN, y, lw=0.4)
        y -= ANN_ROW_H * 0.9

        for inv in ann_list:
            time_str = inv["end"].strftime("%-d %b  %-H:%M")
            tags     = inv.get("tags") or ["(untagged)"]
            first    = tags[0]
            r, g, b  = _tag_rgb(tag_index.get(first, 0))
            _draw_text(time_str,              x,       y, size=8.0, r=0.45, g=0.45, b=0.45)
            _draw_text(f"● {', '.join(tags)}", x + 90,  y, size=8.0, r=r,    g=g,    b=b)
            _draw_text(inv["annotation"],      x + 220, y, size=8.0, r=0.15, g=0.15, b=0.15)
            y -= ANN_ROW_H

    # ── assemble pages via NSGraphicsContext PDF mode ─────────────────────────

    pdf_data = NSMutableData.data()
    attrs = {
        "NSGraphicsContextDestinationAttributeName": pdf_data,
        "NSGraphicsContextRepresentationFormatAttributeName":
            "NSGraphicsContextPDFFormat",
    }
    gc = NSGraphicsContext.graphicsContextWithAttributes_(attrs)
    if gc is None:
        raise RuntimeError("Could not create NSGraphicsContext for PDF output")

    page_bounds = NSRect(NSPoint(0, 0), NSSize(PAGE_W, PAGE_H))

    def _begin_page():
        gc.beginPageWithBounds_(page_bounds)
        NSGraphicsContext.setCurrentContext_(gc)
        NSGraphicsContext.currentContext().setShouldAntialias_(True)

    gc.beginDocumentWithTitle_("Logbook")

    # Page 1 — pie + summary
    _begin_page()
    _draw_page1()
    gc.endPage()

    # Page 2 — timeline (+ annotations if they fit)
    ann_list    = _annotated()
    _, tl_h, _  = _tl_layout()
    header_h    = 36.0
    tl_top      = PAGE_H - MARGIN - header_h
    tl_bottom   = tl_top - tl_h
    ann_gap     = 18.0
    ann_h       = _ann_height(ann_list) if ann_list else 0.0
    fits_on_p2  = ann_list and (tl_bottom - ann_gap - ann_h) >= MARGIN

    _begin_page()
    _draw_page_header(f"Logbook — {period_title}", subtitle="Timeline")
    _draw_timeline(tl_top)
    if fits_on_p2:
        _draw_annotations(ann_list, MARGIN, tl_bottom - ann_gap)
    gc.endPage()

    # Page 3 — annotations only when they didn't fit on page 2
    if ann_list and not fits_on_p2:
        _begin_page()
        _draw_page_header(f"Logbook — {period_title}", subtitle="Annotations")
        _draw_annotations(ann_list, MARGIN, PAGE_H - MARGIN - header_h)
        gc.endPage()

    gc.endDocument()

    # Flush and write to disk
    if not pdf_data.writeToFile_atomically_(path, True):
        raise RuntimeError(f"Could not write PDF to {path}")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ReportWindowController(NSObject):
    def init(self):
        return self.initWithWorkdayHours_showEmptyDays_(7.5, True)

    @objc.signature(b"@@:d")
    def initWithWorkdayHours_(self, workday_hours):
        return self.initWithWorkdayHours_showEmptyDays_(workday_hours, True)

    @objc.signature(b"@@:dB")
    def initWithWorkdayHours_showEmptyDays_(self, workday_hours, show_empty_days):
        self = objc.super(ReportWindowController, self).init()
        if self is None:
            return None
        self._standalone = False  # set to True by main() when run as own process
        self._workday_hours = workday_hours
        self._show_empty_days = bool(show_empty_days)
        # offsets: how many periods back from current (0 = current)
        self._offsets = {"day": 0, "week": 0, "month": 0}
        self._filter_tags = None          # None = show all; set = active filter
        self._all_known_tags = []         # populated in _build_window
        self._filter_checkboxes = {}      # tag -> NSButton
        self._tab_items          = {}  # period -> NSTabViewItem
        self._tab_wrappers       = {}  # period -> persistent NSView wrapper (tab item view)
        self._tab_scrollviews    = {}  # period -> NSScrollView inside wrapper
        self._tab_heights        = {}  # period -> content_h
        self._tab_content_h_cons = {}  # period -> doc_view heightAnchor constraint
        self._tab_resize_cbs     = {}  # period -> _cb(avail_h=) from build_*_view
        self._TAB_CHROME = 32
        self._win_w = 1000.0
        # Custom range state — default to last 30 days
        from datetime import date as _date, timedelta as _td
        self._custom_end   = _date.today()
        self._custom_start = self._custom_end - _td(days=29)
        self._build_window()
        return self

    @objc.python_method
    def _navigate(self, period, delta):
        """Called by Prev/Next buttons. Rebuilds the tab content."""
        self._offsets[period] = self._offsets[period] + delta
        # Cap: don't go into the future
        if self._offsets[period] > 0:
            self._offsets[period] = 0
        self._rebuild_tab(period)

    @objc.python_method
    def _rebuild_tab(self, period):
        on_refresh = lambda p=period: self._rebuild_tab(p)
        on_resize  = lambda new_h, p=period: self._on_section_resize(p, new_h)
        out_cb     = [None]
        if period == "custom":
            content, content_h = build_custom_report_view(
                self._custom_start, self._custom_end,
                on_show=lambda: None,
                workday_hours=self._workday_hours,
                show_empty_days=self._show_empty_days,
                filter_tags=self._filter_tags,
                on_refresh=on_refresh,
                on_resize=on_resize,
                out_resize_cb=out_cb,
            )
        else:
            offset = self._offsets[period]
            content, content_h = build_report_view(
                period, offset,
                on_navigate=lambda delta: self._navigate(period, delta),
                workday_hours=self._workday_hours,
                show_empty_days=self._show_empty_days,
                filter_tags=self._filter_tags,
                on_refresh=on_refresh,
                on_resize=on_resize,
                out_resize_cb=out_cb,
            )
        if out_cb[0] is not None:
            self._tab_resize_cbs[period] = out_cb[0]
        self._tab_heights[period] = content_h

        # Content goes into the scroll view's document view, not the wrapper directly.
        tab_sv  = self._tab_scrollviews[period]
        doc_view = tab_sv.documentView()
        for old in list(doc_view.subviews()):
            old.removeFromSuperview()
        # Keep doc_view frame in sync with the new content height.
        doc_view.setFrameSize_(NSSize(doc_view.frame().size.width, content_h))
        content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        doc_view.addSubview_(content)
        h_con = content.heightAnchor().constraintEqualToConstant_(content_h)
        NSLayoutConstraint.activateConstraints_([
            content.topAnchor().constraintEqualToAnchor_(doc_view.topAnchor()),
            content.leadingAnchor().constraintEqualToAnchor_(doc_view.leadingAnchor()),
            content.trailingAnchor().constraintEqualToAnchor_(doc_view.trailingAnchor()),
            h_con,
        ])
        self._tab_content_h_cons[period] = h_con
        self._resize_for_period(period)

    @objc.python_method
    def _on_section_resize(self, period, new_content_h):
        """Called by CollapsibleSection toggle; resizes the window without rebuilding data."""
        self._tab_heights[period] = new_content_h
        # Update the content-height constraint and doc_view frame so the scroll
        # view knows the document's true height.
        con = self._tab_content_h_cons.get(period)
        if con is not None:
            con.setConstant_(new_content_h)
        tab_sv = self._tab_scrollviews.get(period)
        if tab_sv is not None:
            doc = tab_sv.documentView()
            if doc is not None:
                doc.setFrameSize_(NSSize(doc.frame().size.width, new_content_h))
        self._resize_for_period(period)

    @objc.python_method
    def _resize_for_period(self, period):
        content_h = self._tab_heights.get(period, 400.0)
        if period == "custom":
            content_h += getattr(self, "_custom_picker_h", 32.0)
        win_h = content_h + self._TAB_CHROME + getattr(self, "_FILTER_H", 0.0)
        # frameRectForContentRect_ adds the title bar so setFrame_ gets the
        # correct outer height (content rect != outer window frame).
        target_content_rect = NSRect(NSPoint(0, 0), NSSize(self._win_w, win_h))
        target_frame = self.window.frameRectForContentRect_(target_content_rect)
        frame = self.window.frame()
        screen_top = frame.origin.y + frame.size.height
        new_frame = NSRect(
            NSPoint(frame.origin.x, screen_top - target_frame.size.height),
            NSSize(frame.size.width, target_frame.size.height),
        )
        self.window.setFrame_display_animate_(new_frame, True, True)

    def _build_window(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )

        periods = ["day", "week", "month", "custom"]

        # Collect all known tags from timew so the filter bar is populated
        # regardless of which period is selected.
        raw_tags = run_timew("tags")
        known_tags = []
        tag_col_w = None
        for line in raw_tags.splitlines():
            if not line:
                continue
            if line.lower().startswith("tag ") or line.lower() == "tag":
                continue
            if line.startswith("-"):
                tag_col_w = line.index(" ") if " " in line else len(line)
                continue
            tag = (line[:tag_col_w].strip() if tag_col_w is not None
                   else line.split()[0])
            if tag:
                known_tags.append(tag)
        self._all_known_tags = sorted(known_tags)

        # Recent tags: same logic as the main applet — tags used this month
        raw_recent = run_timew("tags", ":month")
        recent_set = set()
        tag_col_w2 = None
        for line in raw_recent.splitlines():
            if not line:
                continue
            if line.lower().startswith("tag ") or line.lower() == "tag":
                continue
            if line.startswith("-"):
                tag_col_w2 = line.index(" ") if " " in line else len(line)
                continue
            tag = (line[:tag_col_w2].strip() if tag_col_w2 is not None
                   else line.split()[0])
            if tag:
                recent_set.add(tag)
        recent_tags = sorted(t for t in self._all_known_tags if t in recent_set)
        older_tags  = sorted(t for t in self._all_known_tags if t not in recent_set)

        # Build all tab contents first so we know each tab's required height
        built = []
        for period in periods:
            out_cb = [None]
            if period == "custom":
                content, content_h = build_custom_report_view(
                    self._custom_start, self._custom_end,
                    on_show=lambda: None,
                    workday_hours=self._workday_hours,
                    show_empty_days=self._show_empty_days,
                    filter_tags=self._filter_tags,
                    on_refresh=lambda p=period: self._rebuild_tab(p),
                    on_resize=lambda new_h, p=period: self._on_section_resize(p, new_h),
                    out_resize_cb=out_cb,
                )
            else:
                offset = self._offsets[period]
                content, content_h = build_report_view(
                    period, offset,
                    on_navigate=lambda delta, p=period: self._navigate(p, delta),
                    workday_hours=self._workday_hours,
                    show_empty_days=self._show_empty_days,
                    filter_tags=self._filter_tags,
                    on_refresh=lambda p=period: self._rebuild_tab(p),
                    on_resize=lambda new_h, p=period: self._on_section_resize(p, new_h),
                    out_resize_cb=out_cb,
                )
            self._tab_heights[period] = content_h
            if out_cb[0] is not None:
                self._tab_resize_cbs[period] = out_cb[0]
            built.append((period, content))

        # Filter bar height
        FILTER_H = 30.0

        # Use a dummy initial height; we'll correct it after measuring chrome.
        initial_h = self._tab_heights.get("day", 400.0) + self._TAB_CHROME + FILTER_H

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(self._win_w, initial_h)),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_("Arête — Logbook")
        self.window.setContentMinSize_(NSSize(600, 300))
        self.window.center()

        # --- Filter bar: popup button + active-filter label ---
        filter_bar = NSStackView.stackViewWithViews_([])
        filter_bar.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
        filter_bar.setSpacing_(8.0)
        filter_bar.setEdgeInsets_((4.0, 8.0, 4.0, 8.0))
        filter_bar.setTranslatesAutoresizingMaskIntoConstraints_(False)

        # Build the NSMenu for the popup
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        # Index-0 item is the pull-down button title — never dispatched as action
        title_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Filter tags…", None, ""
        )
        menu.addItem_(title_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # Recent tag items
        self._filter_menu_items = {}   # tag -> NSMenuItem
        for tag in recent_tags:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                tag, "filterMenuChanged:", ""
            )
            mi.setTarget_(self)
            mi.setEnabled_(True)
            mi.setState_(NSControlStateValueOff)
            menu.addItem_(mi)
            self._filter_menu_items[tag] = mi

        # Older tags submenu
        if older_tags:
            menu.addItem_(NSMenuItem.separatorItem())
            older_menu = NSMenu.alloc().init()
            older_menu.setAutoenablesItems_(False)
            older_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Older tags", None, ""
            )
            older_parent.setEnabled_(True)
            menu.addItem_(older_parent)
            menu.setSubmenu_forItem_(older_menu, older_parent)
            for tag in older_tags:
                mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    tag, "filterMenuChanged:", ""
                )
                mi.setTarget_(self)
                mi.setEnabled_(True)
                mi.setState_(NSControlStateValueOff)
                older_menu.addItem_(mi)
                self._filter_menu_items[tag] = mi

        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSRect(NSPoint(0, 0), NSSize(160, 22)), True   # pull-down: items dispatch to their own target
        )
        popup.setMenu_(menu)
        popup.setFont_(NSFont.systemFontOfSize_(11))
        popup.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self._filter_popup = popup
        filter_bar.addView_inGravity_(popup, 1)

        # Label showing the active filter tags
        self._filter_label = NSTextField.labelWithString_("")
        self._filter_label.setFont_(NSFont.systemFontOfSize_(11))
        self._filter_label.setTextColor_(NSColor.secondaryLabelColor())
        self._filter_label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        filter_bar.addView_inGravity_(self._filter_label, 1)

        # "Clear filter" button — only visible when a filter is active
        btn_clear = NSButton.buttonWithTitle_target_action_(
            "✕ Clear filter", self, "clearFilter:"
        )
        btn_clear.setBezelStyle_(4)   # NSBezelStyleRounded / inline
        btn_clear.setFont_(NSFont.systemFontOfSize_(11))
        btn_clear.setHidden_(True)
        btn_clear.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self._btn_clear_filter = btn_clear
        filter_bar.addView_inGravity_(btn_clear, 1)

        # "Export PDF…" button — always visible, trailing gravity
        btn_pdf = NSButton.buttonWithTitle_target_action_(
            "⬇ Export PDF…", self, "exportPDF:"
        )
        btn_pdf.setBezelStyle_(4)   # NSBezelStyleRounded / inline
        btn_pdf.setFont_(NSFont.systemFontOfSize_(11))
        btn_pdf.setTranslatesAutoresizingMaskIntoConstraints_(False)
        filter_bar.addView_inGravity_(btn_pdf, 3)   # gravity 3 = trailing

        filter_scroll = NSView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(self._win_w, FILTER_H))
        )
        filter_scroll.setAutoresizingMask_(NSViewWidthSizable)
        filter_scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
        filter_scroll.addSubview_(filter_bar)
        NSLayoutConstraint.activateConstraints_([
            filter_bar.topAnchor().constraintEqualToAnchor_(filter_scroll.topAnchor()),
            filter_bar.bottomAnchor().constraintEqualToAnchor_(filter_scroll.bottomAnchor()),
            filter_bar.leadingAnchor().constraintEqualToAnchor_(filter_scroll.leadingAnchor()),
        ])
        self._filter_scroll = filter_scroll

        # --- Tab view ---
        tab_h = initial_h - FILTER_H
        tab_view = NSTabView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(self._win_w, tab_h))
        )
        tab_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        tab_view.setDelegate_(self)
        self._tab_view = tab_view

        from AppKit import (
            NSDatePicker, NSDatePickerStyleTextField,
            NSDatePickerElementFlagYearMonthDay,
        )
        from Foundation import NSDate

        def _date_to_nsdate(d):
            import datetime as _dt
            dt = _dt.datetime(d.year, d.month, d.day, 12, 0, 0)
            return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())

        labels = {"day": "Day", "week": "Week", "month": "Month", "custom": "Custom"}
        PICKER_H = 32.0
        self._custom_picker_h = PICKER_H

        for period, content in built:
            # wrapper = the NSView that NSTabViewItem owns (fills the tab chrome)
            wrapper = NSView.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0), NSSize(self._win_w, self._tab_heights[period]))
            )
            wrapper.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

            # doc_view = frame-based NSView that NSScrollView uses as its document view.
            # It must NOT use Auto Layout (TAMSIC=True, the default) so the scroll view
            # can set its frame directly.  content lives inside it and can use AL freely.
            content_h = self._tab_heights[period]
            doc_view = NSView.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0), NSSize(self._win_w, content_h))
            )
            # doc_view stays frame-based — do NOT call setTranslatesAutoresizingMaskIntoConstraints_(False)
            content.setTranslatesAutoresizingMaskIntoConstraints_(False)
            doc_view.addSubview_(content)
            h_con = content.heightAnchor().constraintEqualToConstant_(content_h)
            NSLayoutConstraint.activateConstraints_([
                content.topAnchor().constraintEqualToAnchor_(doc_view.topAnchor()),
                content.leadingAnchor().constraintEqualToAnchor_(doc_view.leadingAnchor()),
                content.trailingAnchor().constraintEqualToAnchor_(doc_view.trailingAnchor()),
                h_con,
            ])
            self._tab_content_h_cons[period] = h_con

            # tab_sv = scroll view that fills the wrapper (or the area below picker_bar)
            tab_sv = NSScrollView.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0), NSSize(self._win_w, content_h))
            )
            tab_sv.setHasVerticalScroller_(True)
            tab_sv.setHasHorizontalScroller_(False)
            tab_sv.setAutohidesScrollers_(True)
            tab_sv.setBorderType_(0)   # NSNoBorder
            tab_sv.setDocumentView_(doc_view)
            tab_sv.setTranslatesAutoresizingMaskIntoConstraints_(False)
            self._tab_scrollviews[period] = tab_sv

            if period == "custom":
                dp_from = NSDatePicker.alloc().initWithFrame_(
                    NSRect(NSPoint(0, 0), NSSize(120, 24)))
                dp_from.setDatePickerStyle_(NSDatePickerStyleTextField)
                dp_from.setDatePickerElements_(NSDatePickerElementFlagYearMonthDay)
                dp_from.setDateValue_(_date_to_nsdate(self._custom_start))
                dp_from.setTranslatesAutoresizingMaskIntoConstraints_(False)
                self._dp_from = dp_from

                dp_to = NSDatePicker.alloc().initWithFrame_(
                    NSRect(NSPoint(0, 0), NSSize(120, 24)))
                dp_to.setDatePickerStyle_(NSDatePickerStyleTextField)
                dp_to.setDatePickerElements_(NSDatePickerElementFlagYearMonthDay)
                dp_to.setDateValue_(_date_to_nsdate(self._custom_end))
                dp_to.setTranslatesAutoresizingMaskIntoConstraints_(False)
                self._dp_to = dp_to

                btn_show = NSButton.buttonWithTitle_target_action_(
                    "Show", self, "showCustom:")
                btn_show.setBezelStyle_(1)
                btn_show.setKeyEquivalent_("\r")
                btn_show.setTranslatesAutoresizingMaskIntoConstraints_(False)

                picker_bar = NSStackView.stackViewWithViews_([])
                picker_bar.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
                picker_bar.setSpacing_(8.0)
                picker_bar.setEdgeInsets_((4.0, 12.0, 4.0, 12.0))
                picker_bar.setTranslatesAutoresizingMaskIntoConstraints_(False)
                picker_bar.addView_inGravity_(make_label("From:", size=11, muted=True), 1)
                picker_bar.addView_inGravity_(dp_from, 1)
                picker_bar.addView_inGravity_(make_label("To:", size=11, muted=True), 1)
                picker_bar.addView_inGravity_(dp_to, 1)
                picker_bar.addView_inGravity_(btn_show, 1)

                self._custom_picker_bar = picker_bar
                wrapper.addSubview_(picker_bar)
                wrapper.addSubview_(tab_sv)
                NSLayoutConstraint.activateConstraints_([
                    picker_bar.topAnchor().constraintEqualToAnchor_(wrapper.topAnchor()),
                    picker_bar.leadingAnchor().constraintEqualToAnchor_(wrapper.leadingAnchor()),
                    picker_bar.trailingAnchor().constraintEqualToAnchor_(wrapper.trailingAnchor()),
                    picker_bar.heightAnchor().constraintEqualToConstant_(PICKER_H),
                    tab_sv.topAnchor().constraintEqualToAnchor_constant_(picker_bar.bottomAnchor(), 0),
                    tab_sv.leadingAnchor().constraintEqualToAnchor_(wrapper.leadingAnchor()),
                    tab_sv.trailingAnchor().constraintEqualToAnchor_(wrapper.trailingAnchor()),
                    tab_sv.bottomAnchor().constraintEqualToAnchor_(wrapper.bottomAnchor()),
                ])
            else:
                wrapper.addSubview_(tab_sv)
                NSLayoutConstraint.activateConstraints_([
                    tab_sv.topAnchor().constraintEqualToAnchor_(wrapper.topAnchor()),
                    tab_sv.leadingAnchor().constraintEqualToAnchor_(wrapper.leadingAnchor()),
                    tab_sv.trailingAnchor().constraintEqualToAnchor_(wrapper.trailingAnchor()),
                    tab_sv.bottomAnchor().constraintEqualToAnchor_(wrapper.bottomAnchor()),
                ])

            item = NSTabViewItem.alloc().initWithIdentifier_(period)
            item.setLabel_(labels[period])
            item.setView_(wrapper)
            tab_view.addTabViewItem_(item)
            self._tab_items[period] = item
            self._tab_wrappers[period] = wrapper

        # --- Outer content view: filter bar on top, tab view below ---
        outer = NSView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(self._win_w, initial_h))
        )
        outer.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        outer.addSubview_(filter_scroll)
        outer.addSubview_(tab_view)
        tab_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        NSLayoutConstraint.activateConstraints_([
            filter_scroll.topAnchor().constraintEqualToAnchor_(outer.topAnchor()),
            filter_scroll.leadingAnchor().constraintEqualToAnchor_(outer.leadingAnchor()),
            filter_scroll.trailingAnchor().constraintEqualToAnchor_(outer.trailingAnchor()),
            filter_scroll.heightAnchor().constraintEqualToConstant_(FILTER_H),

            tab_view.topAnchor().constraintEqualToAnchor_(filter_scroll.bottomAnchor()),
            tab_view.leadingAnchor().constraintEqualToAnchor_(outer.leadingAnchor()),
            tab_view.trailingAnchor().constraintEqualToAnchor_(outer.trailingAnchor()),
            tab_view.bottomAnchor().constraintEqualToAnchor_(outer.bottomAnchor()),
        ])

        self._FILTER_H = FILTER_H
        self.window.setContentView_(outer)

        # Measure the real tab chrome
        content_rect = tab_view.contentRect()
        self._TAB_CHROME = tab_view.frame().size.height - content_rect.size.height

        # Resize correctly for the initial "day" tab.
        self._resize_for_period("day")

    @objc.typedSelector(b"v@:@")
    def clearFilter_(self, sender):
        for mi in self._filter_menu_items.values():
            mi.setState_(NSControlStateValueOff)
        self._filter_tags = None
        self._filter_label.setStringValue_("")
        self._btn_clear_filter.setHidden_(True)
        selected = self._tab_view.selectedTabViewItem()
        if selected is not None:
            self._rebuild_tab(selected.identifier())

    @objc.typedSelector(b"v@:@")
    def filterMenuChanged_(self, sender):
        # Toggle this tag's checkmark
        tag = sender.title()
        mi = self._filter_menu_items.get(tag)
        if mi is not None:
            new_state = (NSControlStateValueOff
                         if mi.state() == NSControlStateValueOn
                         else NSControlStateValueOn)
            mi.setState_(new_state)
        # Recompute active set
        active = {t for t, m in self._filter_menu_items.items()
                  if m.state() == NSControlStateValueOn}
        self._filter_tags = active if active else None
        # Update summary label and clear button
        if self._filter_tags:
            self._filter_label.setStringValue_(
                "Showing: " + ", ".join(sorted(self._filter_tags))
            )
            self._btn_clear_filter.setHidden_(False)
        else:
            self._filter_label.setStringValue_("")
            self._btn_clear_filter.setHidden_(True)

        # Rebuild the currently visible tab
        selected = self._tab_view.selectedTabViewItem()
        if selected is not None:
            self._rebuild_tab(selected.identifier())

    @objc.typedSelector(b"v@:@")
    def showCustom_(self, sender):
        """Read the date pickers and rebuild the custom tab."""
        from datetime import date as _date
        import datetime as _dt

        def _nsdate_to_date(nsdate):
            ts = nsdate.timeIntervalSince1970()
            return _dt.datetime.fromtimestamp(ts).date()

        self._custom_start = _nsdate_to_date(self._dp_from.dateValue())
        self._custom_end   = _nsdate_to_date(self._dp_to.dateValue())
        # Clamp: start must not be after end
        if self._custom_start > self._custom_end:
            self._custom_start, self._custom_end = self._custom_end, self._custom_start
        self._rebuild_tab("custom")
        self._resize_for_period("custom")

    @objc.typedSelector(b"v@:@@")
    def tabView_didSelectTabViewItem_(self, tab_view, item):
        period = item.identifier()
        if self._filter_tags is not None:
            self._rebuild_tab(period)
        self._resize_for_period(period)

    # --- Window delegate ---
    @objc.typedSelector(b"v@:@")
    def windowDidResize_(self, notification):
        """Resize annotations section to match available window height."""
        period = self._tab_view.selectedTabViewItem().identifier()
        cb = self._tab_resize_cbs.get(period)
        if cb is None:
            return
        # Available height for the content inside the tab scroll view.
        content_rect = self._tab_view.contentRect()
        avail_h = content_rect.size.height
        if period == "custom":
            avail_h -= getattr(self, "_custom_picker_h", 32.0)
        cb(avail_h=avail_h)

    @objc.typedSelector(b"v@:@")
    def exportPDF_(self, sender):
        """Collect current view data, write a temp PDF, and open it in Preview."""
        import tempfile

        path = os.path.join(
            tempfile.gettempdir(),
            f"Logbook-{date.today().strftime('%Y-%m-%d')}.pdf",
        )

        # Determine active period/range and filter
        selected = self._tab_view.selectedTabViewItem()
        period = selected.identifier() if selected else "day"

        if period == "custom":
            start_date = self._custom_start
            end_date   = self._custom_end
            today_d = date.today()
            if end_date > today_d:
                end_date = today_d
            period_title = (f"{start_date.strftime('%-d %b %Y')} – "
                            f"{end_date.strftime('%-d %b %Y')}")

            def _count_wdays(s, e):
                n, d = 0, s
                while d <= e:
                    if d.weekday() < 5:
                        n += 1
                    d += timedelta(days=1)
                return n

            WORKDAY_SECS = self._workday_hours * 3600
            target_secs = _count_wdays(start_date, end_date) * WORKDAY_SECS
        else:
            offset = self._offsets.get(period, 0)
            start_date, end_date, period_title = date_range_for(period, offset)
            WORKDAY_SECS = self._workday_hours * 3600

            def _count_wdays(s, e):
                n, d = 0, s
                while d <= e:
                    if d.weekday() < 5:
                        n += 1
                    d += timedelta(days=1)
                return n

            if period == "day":
                target_secs = (WORKDAY_SECS
                               if start_date.weekday() < 5 else 0.0)
            else:
                target_secs = _count_wdays(start_date, end_date) * WORKDAY_SECS

        # Fetch and filter intervals (same logic as build_report_view)
        start_str = start_date.strftime("%Y-%m-%dT00:00:00")
        end_str   = (end_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        out = run_timew("export", start_str, "-", end_str)
        intervals = _parse_intervals(out)

        filter_tags = self._filter_tags
        if filter_tags is not None:
            filtered = []
            for inv in intervals:
                matched = [t for t in inv["tags"] if t in filter_tags]
                if matched:
                    filtered.append({**inv, "tags": matched})
            intervals = filtered

        seen_tags, tag_set = [], set()
        for inv in intervals:
            for t in inv["tags"] or ["(untagged)"]:
                if t not in tag_set:
                    seen_tags.append(t)
                    tag_set.add(t)
        all_tags = seen_tags
        tag_index = {t: i for i, t in enumerate(all_tags)}

        tag_totals = defaultdict(float)
        for inv in intervals:
            for t in inv["tags"] or ["(untagged)"]:
                tag_totals[t] += inv["duration"]
        grand_total = sum(tag_totals.values())

        by_date = defaultdict(list)
        for inv in intervals:
            by_date[inv["start"].date()].append(inv)

        today_d = date.today()
        if period == "day":
            rows = [(start_date.strftime("%-d %b"), start_date,
                     by_date.get(start_date, []))]
        else:
            rows = []
            d = start_date
            while d <= end_date:
                day_invs = by_date.get(d, [])
                if day_invs or self._show_empty_days or d == today_d:
                    rows.append((d.strftime("%-d %b"), d, day_invs))
                d += timedelta(days=1)

        if intervals:
            hour_start = min(inv["start"].hour for inv in intervals)
            hour_end   = max(
                inv["end"].hour + (1 if inv["end"].minute or inv["end"].second else 0)
                for inv in intervals
            )
            hour_start = max(0, hour_start)
            hour_end   = min(24, hour_end)
            if hour_end <= hour_start:
                hour_end = min(24, hour_start + 1)
        else:
            hour_start, hour_end = 0, 24

        try:
            export_logbook_pdf(
                start_date, end_date, period_title, intervals,
                all_tags, tag_index, tag_totals, grand_total,
                target_secs, rows, hour_start, hour_end, path,
            )
        except Exception as exc:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("PDF export failed")
            alert.setInformativeText_(str(exc))
            alert.runModal()
            return

        # Open the file in Preview
        import subprocess as _sp
        _sp.Popen(["open", path])

    @objc.typedSelector(b"v@:@")
    def windowWillClose_(self, notification):
        # When run standalone (own process), quit cleanly.
        # When in-process, call the optional close callback so arete.py
        # clears its reference and allows the window to be re-opened fresh.
        if self._standalone:
            NSApplication.sharedApplication().terminate_(None)
        elif callable(getattr(self, "_on_close", None)):
            self._on_close()

    def show(self):
        self.window.setDelegate_(self)
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Inject CFBundleName into the main bundle's info dict so macOS shows
    # "Arête Logbook" as the Dock tooltip and app menu name instead of "Python".
    _info = NSBundle.mainBundle().infoDictionary()
    if _info is not None:
        _info["CFBundleName"] = "Arête Logbook"
        _info["CFBundleDisplayName"] = "Arête Logbook"
    NSProcessInfo.processInfo().setProcessName_("Arête Logbook")

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    # Use the Arête icon instead of the generic Python rocket.
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Arete.icns")
    if os.path.exists(_icon_path):
        _icon = NSImage.alloc().initWithContentsOfFile_(_icon_path)
        if _icon:
            app.setApplicationIconImage_(_icon)

    # Read user preferences from ~/.arete.json so the standalone Logbook app
    # honours the same settings as the menu-bar applet.
    _config_path = os.path.expanduser("~/.arete.json")
    _config = {}
    try:
        import json as _json
        if os.path.exists(_config_path):
            with open(_config_path) as _f:
                _config = _json.load(_f)
    except Exception:
        pass
    _workday_hours  = float(_config.get("workday_hours", 7.5))
    _show_empty_days = bool(_config.get("show_empty_days", True))

    controller = ReportWindowController.alloc().initWithWorkdayHours_showEmptyDays_(
        _workday_hours, _show_empty_days)
    controller._standalone = True
    controller.show()

    app.run()


if __name__ == "__main__":
    main()

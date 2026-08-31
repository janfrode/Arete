#!/usr/bin/env python3
"""Arête — Time Tracker menu bar applet.

Shows currently tracked tags in the menu bar title and lets you
start/stop individual tags via a checklist menu.
"""

import subprocess
import re
import shlex
import rumps
import os
import shutil
import json
import sys
import threading
import urllib.request
import importlib.util
import atexit
from Foundation import NSDistributedNotificationCenter, NSObject, NSTimer, NSString, NSDate as _NSDate
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSBitmapImageRep,
    NSGraphicsContext, NSImage, NSBezierPath, NSColor, NSRect, NSPoint,
    NSSize, NSPNGFileType, NSImageView, NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable, NSWindowStyleMaskResizable, NSBackingStoreBuffered, NSUserInterfaceLayoutOrientationVertical,
    NSControlStateValueOn, NSControlStateValueOff, NSModalResponseOK,
    NSModalResponseCancel, NSStackView, NSTextField, NSButton, NSWindow,
    NSBox, NSGridView, NSGridCell, NSPanel, NSWindowStyleMaskBorderless,
    NSViewWidthSizable, NSViewHeightSizable,
    NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive, NSFontManager, NSView,
    NSCursor,
    NSDatePicker, NSDatePickerStyleTextFieldAndStepper,
    NSDatePickerElementFlagHourMinute, NSDatePickerElementFlagYearMonthDay,
    NSPopUpButton,
    NSMenuItem,
)
from datetime import datetime, timezone, timedelta
import objc


CONFIG_PATH = os.path.expanduser("~/.arete.json")
def _read_version():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "version"), encoding="utf-8") as _f:
        v = _f.read().strip()
    if not v:
        raise RuntimeError("version file is empty")
    return v

VERSION = _read_version()


def _make_menubar_icon(progress=None):
    """Render a 44×44 pixel icon at 22×22 pt logical size for the menu bar.

    If progress is a float (0.0 to 1.0+), a progress ring is drawn around the logo.
    Returns the path to a temp PNG, or None on failure.
    macOS template images must end in 'Template' — rumps handles that when
    template=True is passed, but we set NSImage.setTemplate_ directly instead.
    """
    import tempfile
    SIZE = 44   # pixel size (@2x Retina detail)
    img = NSImage.alloc().initWithSize_(NSSize(SIZE, SIZE))
    img.lockFocus()

    ctx = NSGraphicsContext.currentContext()
    ctx.setShouldAntialias_(True)

    # Three horizontal bars — same proportions as the app icon, but monochrome
    bar_h  = SIZE * 0.14
    gap    = SIZE * 0.11
    radius = bar_h / 2.0
    left   = SIZE * 0.10
    right  = SIZE * 0.90
    cx     = SIZE / 2.0
    cy     = SIZE / 2.0

    bars = [
        (0.00, 0.82, cx + bar_h + gap),   # top
        (0.18, 1.00, cx),                 # middle
        (0.00, 0.52, cx - bar_h - gap),   # bottom (shorter, "live")
    ]
    bar_width = right - left

    NSColor.blackColor().set()
    for x0f, x1f, bar_cy in bars:
        x0 = left + bar_width * x0f
        x1 = left + bar_width * x1f
        r  = NSRect(NSPoint(x0, bar_cy - bar_h / 2), NSSize(x1 - x0, bar_h))
        p  = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, radius, radius)
        p.fill()

    # Draw progress ring if provided
    if progress is not None:
        r_ring = 20.0
        center = NSPoint(cx, cy)
        
        # 1. Background faint track
        bg_track = NSBezierPath.bezierPath()
        bg_track.setLineWidth_(2.0)
        bg_track.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            center, r_ring, 0.0, 360.0, False
        )
        NSColor.blackColor().colorWithAlphaComponent_(0.15).set()
        bg_track.stroke()
        
        # 2. Active progress path
        p_val = min(1.0, max(0.0, progress))
        if p_val > 0.0:
            active_track = NSBezierPath.bezierPath()
            active_track.setLineWidth_(2.0)
            active_track.setLineCapStyle_(1) # NSLineCapStyleRound
            
            # Cocoa/AppKit: 0 is at 3 o'clock. 90 is at 12 o'clock.
            # Clockwise progress starting from 12 o'clock:
            start_angle = 90.0
            end_angle = 90.0 - 360.0 * p_val
            
            active_track.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                center, r_ring, start_angle, end_angle, True
            )
            NSColor.blackColor().set()
            active_track.stroke()

    img.unlockFocus()
    # Set logical size to 22×22 pt so macOS uses exactly one status-item slot.
    # The 44 px pixel data provides Retina sharpness; without this the image
    # would be treated as 44 pt wide and consume twice the menu bar space.
    img.setSize_(NSSize(22, 22))
    img.setTemplate_(True)   # tells macOS to tint for light/dark menu bar

    bm  = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
    png = bm.representationUsingType_properties_(NSPNGFileType, None)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    png.writeToFile_atomically_(tmp.name, True)
    return tmp.name


def get_bundle_path():
    """Return the absolute path of the running .app bundle, or fallback if running as script."""
    path = os.path.abspath(sys.argv[0])
    if ".app/Contents/MacOS" in path:
        return path.split(".app/Contents/MacOS")[0] + ".app"
    # Fallback paths for development/script run
    for fallback in ["dist/Arete.app", "/Applications/Arete.app"]:
        abs_fallback = os.path.abspath(fallback)
        if os.path.exists(abs_fallback):
            return abs_fallback
    return None


def get_changes_path():
    """Return path to Changes.md — bundle Resources first, then dev fallback."""
    app_path = get_bundle_path()
    if app_path:
        p = os.path.join(app_path, "Contents", "Resources", "Changes.md")
        if os.path.exists(p):
            return p
    # Dev / script run: same directory as arete.py
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "Changes.md")
    if os.path.exists(p):
        return p
    return None


def get_icon_path():
    """Return path to Arête.icns or icon.png, prioritizing bundle resources if packaged."""
    app_path = get_bundle_path()
    if app_path:
        # Inside Arete.app/Contents/Resources/Arete.icns
        bundle_icns = os.path.join(app_path, "Contents", "Resources", "Arete.icns")
        if os.path.exists(bundle_icns):
            return bundle_icns
    # Fallback to local files for development
    for p in ["Arete.icns", "icon.png"]:
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def is_login_item_enabled():
    """Check if the app is currently in the macOS login items list."""
    try:
        cmd = 'tell application "System Events" to get count of (every login item whose name is "Arete")'
        result = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True, check=True)
        return result.stdout.strip() != "0"
    except Exception:
        return False


def set_login_item_enabled(enabled, app_path):
    """Add or remove the app from the macOS login items list."""
    if not app_path:
        return
    try:
        # Always clean up existing items first to avoid duplicates
        delete_cmd = 'tell application "System Events" to delete (every login item whose name is "Arete")'
        subprocess.run(["osascript", "-e", delete_cmd], capture_output=True, check=True)
        
        if enabled:
            add_cmd = f'tell application "System Events" to make new login item at end with properties {{path:"{app_path}", name:"Arete", hidden:false}}'
            subprocess.run(["osascript", "-e", add_cmd], capture_output=True, check=True)
    except Exception as e:
        print(f"Error setting login item: {e}")


def load_config():
    """Load configuration from ~/.arete.json."""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(config):
    """Save configuration to ~/.arete.json."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f)
    except Exception:
        pass


class NotificationObserver(NSObject):
    """Observer class for macOS distributed notifications."""

    def initWithApp_(self, app):
        self = objc.super(NotificationObserver, self).init()
        if self is None:
            return None
        self.app = app
        return self

    @objc.typedSelector(b"v@:@")
    def screenLocked_(self, notification):
        self.app.handle_screen_locked()

    @objc.typedSelector(b"v@:@")
    def screenUnlocked_(self, notification):
        self.app.handle_screen_unlocked()



class _CoffeeLinkTarget(NSObject):
    """Action target that opens the Buy Me a Coffee page."""

    @objc.typedSelector(b"v@:@")
    def open_(self, sender):
        subprocess.run(["open", "https://buymeacoffee.com/janfrode"])



class PreferencesWindow(NSObject):
    """Native macOS Preferences Window controller with merged About information."""

    def initWithApp_(self, app):
        self = objc.super(PreferencesWindow, self).init()
        if self is None:
            return None
        self.app = app
        return self

    def show(self):
        style_mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(360, 420)),
            style_mask,
            NSBackingStoreBuffered,
            False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("Arête Preferences & About")
        window.center()

        # Root vertical stack — single column, 24 px side margins
        stack = NSStackView.stackViewWithViews_([])
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(9)   # NSLayoutAttributeLeading — left-align all rows
        stack.setSpacing_(10.0)
        stack.setEdgeInsets_((20.0, 24.0, 20.0, 24.0))

        # ── Header: icon + about text ────────────────────────────────────
        header_stack = NSStackView.stackViewWithViews_([])
        header_stack.setOrientation_(0)  # Horizontal
        header_stack.setAlignment_(9)    # leading
        header_stack.setSpacing_(16.0)

        icon_path = get_icon_path()
        if icon_path:
            logo_img = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if logo_img:
                logo_img.setSize_(NSSize(80, 80))
                logo_view = NSImageView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(80, 80)))
                logo_view.setImage_(logo_img)
                logo_view.setImageScaling_(3)
                logo_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
                logo_view.widthAnchor().constraintEqualToConstant_(80.0).setActive_(True)
                logo_view.heightAnchor().constraintEqualToConstant_(80.0).setActive_(True)
                header_stack.addView_inGravity_(logo_view, 1)

        about_stack = NSStackView.stackViewWithViews_([])
        about_stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        about_stack.setAlignment_(9)  # leading
        about_stack.setSpacing_(2.0)

        lbl_title = NSTextField.labelWithString_("Arête")
        lbl_title.setFont_(NSFont.boldSystemFontOfSize_(17))
        lbl_title.setSelectable_(True)
        about_stack.addView_inGravity_(lbl_title, 1)

        lbl_version = NSTextField.labelWithString_(f"Version {VERSION}")
        lbl_version.setFont_(NSFont.systemFontOfSize_(11))
        lbl_version.setTextColor_(NSColor.secondaryLabelColor())
        lbl_version.setSelectable_(True)
        about_stack.addView_inGravity_(lbl_version, 1)

        lbl_desc = NSTextField.labelWithString_("A macOS menu bar time tracker.")
        lbl_desc.setFont_(NSFont.systemFontOfSize_(12))
        lbl_desc.setSelectable_(True)
        about_stack.addView_inGravity_(lbl_desc, 1)

        for text in (
            "Author: Jan-Frode Myklebust",
            "Email: janfrode@tanso.net",
            "GitHub: https://github.com/janfrode/Arete",
        ):
            lbl = NSTextField.labelWithString_(text)
            lbl.setFont_(NSFont.systemFontOfSize_(11))
            lbl.setTextColor_(NSColor.secondaryLabelColor())
            lbl.setSelectable_(True)
            about_stack.addView_inGravity_(lbl, 1)

        header_stack.addView_inGravity_(about_stack, 1)
        stack.addView_inGravity_(header_stack, 1)

        # ── Quote — full width, below the header ─────────────────────────
        QUOTE = (
            "\u201cWe are what we repeatedly do, therefore, "
            "excellence is not an act, but a habit.\u201d"
            "  \u2014 Aristotle"
        )
        italic_13 = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
            NSFont.systemFontOfSize_(13), 1
        )
        CONTENT_W = 360 - 24 * 2   # 312 px — full content width minus insets
        tf_quote = NSTextField.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(CONTENT_W, 10)))
        tf_quote.setStringValue_(QUOTE)
        tf_quote.setFont_(italic_13)
        tf_quote.setTextColor_(NSColor.labelColor())
        tf_quote.setBezeled_(False)
        tf_quote.setDrawsBackground_(False)
        tf_quote.setEditable_(False)
        tf_quote.setSelectable_(True)
        tf_quote.cell().setWraps_(True)
        tf_quote.setMaximumNumberOfLines_(0)
        tf_quote.setPreferredMaxLayoutWidth_(CONTENT_W)
        tf_quote.setTranslatesAutoresizingMaskIntoConstraints_(False)
        stack.addView_inGravity_(tf_quote, 1)

        # ── Buy Me a Coffee ──────────────────────────────────────────────
        sep1 = NSBox.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(10, 1)))
        sep1.setBoxType_(2)
        stack.addView_inGravity_(sep1, 1)

        self._coffee_target = _CoffeeLinkTarget.alloc().init()
        btn_coffee = NSButton.buttonWithTitle_target_action_(
            "☕  Buy Me a Coffee", self._coffee_target, "open:"
        )
        btn_coffee.setBezelStyle_(1)
        btn_coffee.setFont_(NSFont.boldSystemFontOfSize_(12))
        btn_coffee.setContentTintColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.85, 0.55, 0.05, 1.0)
        )
        stack.addView_inGravity_(btn_coffee, 1)

        # ── Settings ─────────────────────────────────────────────────────
        sep2 = NSBox.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(10, 1)))
        sep2.setBoxType_(2)
        stack.addView_inGravity_(sep2, 1)

        lbl_settings = NSTextField.labelWithString_("Settings")
        lbl_settings.setFont_(NSFont.boldSystemFontOfSize_(12))
        lbl_settings.setTextColor_(NSColor.secondaryLabelColor())
        stack.addView_inGravity_(lbl_settings, 1)

        # Checkboxes
        self.chk_pause = NSButton.buttonWithTitle_target_action_(
            "Pause tracking when screen locked", self, None
        )
        self.chk_pause.setButtonType_(3)
        self.chk_pause.setState_(
            NSControlStateValueOn if self.app._config.get("pause_on_lock", False) else NSControlStateValueOff
        )
        stack.addView_inGravity_(self.chk_pause, 1)

        self.chk_login = NSButton.buttonWithTitle_target_action_(
            "Start at login", self, None
        )
        self.chk_login.setButtonType_(3)
        self.chk_login.setState_(
            NSControlStateValueOn if is_login_item_enabled() else NSControlStateValueOff
        )
        if not get_bundle_path():
            self.chk_login.setEnabled_(False)
        stack.addView_inGravity_(self.chk_login, 1)

        self.chk_empty_days = NSButton.buttonWithTitle_target_action_(
            "Show empty days in logbook", self, None
        )
        self.chk_empty_days.setButtonType_(3)
        self.chk_empty_days.setState_(
            NSControlStateValueOn if self.app._config.get("show_empty_days", True) else NSControlStateValueOff
        )
        stack.addView_inGravity_(self.chk_empty_days, 1)

        self.chk_prompt_stop = NSButton.buttonWithTitle_target_action_(
            "Prompt for annotation when stopping a task", self, None
        )
        self.chk_prompt_stop.setButtonType_(3)
        self.chk_prompt_stop.setState_(
            NSControlStateValueOn if self.app._config.get("prompt_on_stop", False) else NSControlStateValueOff
        )
        stack.addView_inGravity_(self.chk_prompt_stop, 1)

        # Form rows: label (fixed 200 px wide, right-aligned) + field
        LABEL_W = 210

        def make_label(text):
            lbl = NSTextField.labelWithString_(text)
            lbl.setFont_(NSFont.systemFontOfSize_(13))
            lbl.setAlignment_(1)  # NSTextAlignmentRight
            lbl.setTranslatesAutoresizingMaskIntoConstraints_(False)
            lbl.widthAnchor().constraintEqualToConstant_(float(LABEL_W)).setActive_(True)
            return lbl

        # Row: Recent tags range
        recent_val = self.app._config.get("recent_range", ":fortnight")
        display_val = recent_val[1:] if recent_val.startswith(":") else recent_val
        self.txt_range = NSTextField.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(100, 22)))
        self.txt_range.setStringValue_(display_val)

        row_range = NSStackView.stackViewWithViews_([])
        row_range.setOrientation_(0)
        row_range.setAlignment_(8)  # NSLayoutAttributeCenterY
        row_range.setSpacing_(8.0)
        row_range.addView_inGravity_(make_label("Recent tags range:"), 1)
        row_range.addView_inGravity_(self.txt_range, 1)
        stack.addView_inGravity_(row_range, 1)

        # Row: Daily work target
        self.txt_target = NSTextField.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(60, 22)))
        self.txt_target.setStringValue_(str(self.app._config.get("workday_hours", 7.5)))

        row_target = NSStackView.stackViewWithViews_([])
        row_target.setOrientation_(0)
        row_target.setAlignment_(8)  # NSLayoutAttributeCenterY
        row_target.setSpacing_(8.0)
        row_target.addView_inGravity_(make_label("Daily work target (hours):"), 1)
        row_target.addView_inGravity_(self.txt_target, 1)
        stack.addView_inGravity_(row_target, 1)

        # ── Save / Cancel ────────────────────────────────────────────────
        sep3 = NSBox.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(10, 1)))
        sep3.setBoxType_(2)
        stack.addView_inGravity_(sep3, 1)

        btn_stack = NSStackView.stackViewWithViews_([])
        btn_stack.setOrientation_(0)
        btn_stack.setSpacing_(8.0)

        btn_cancel = NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        btn_cancel.setKeyEquivalent_("\x1b")

        btn_save = NSButton.buttonWithTitle_target_action_("Save", self, "save:")
        btn_save.setKeyEquivalent_("\r")

        btn_stack.addView_inGravity_(btn_cancel, 3)
        btn_stack.addView_inGravity_(btn_save, 3)
        stack.addView_inGravity_(btn_stack, 3)

        window.setContentView_(stack)
        self.window = window

        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.typedSelector(b"v@:@")
    def cancel_(self, sender):
        self.window.close()
        self.app._pref_controller = None

    @objc.typedSelector(b"v@:@")
    def save_(self, sender):
        # Read states
        pause_state = self.chk_pause.state() == NSControlStateValueOn
        self.app._config["pause_on_lock"] = pause_state

        if self.chk_login and self.chk_login.isEnabled():
            login_state = self.chk_login.state() == NSControlStateValueOn
            app_path = get_bundle_path()
            set_login_item_enabled(login_state, app_path)

        user_val = self.txt_range.stringValue().strip()
        if user_val and not user_val.startswith(":") and " " not in user_val:
            user_val = ":" + user_val
        self.app._config["recent_range"] = user_val

        try:
            workday_hours = float(self.txt_target.stringValue().strip())
            if workday_hours < 0:
                workday_hours = 0.0
        except (ValueError, AttributeError):
            workday_hours = 7.5
        self.app._config["workday_hours"] = workday_hours

        self.app._config["show_empty_days"] = (
            self.chk_empty_days.state() == NSControlStateValueOn
        )

        self.app._config["prompt_on_stop"] = (
            self.chk_prompt_stop.state() == NSControlStateValueOn
        )

        save_config(self.app._config)

        self.window.close()

        # Refresh app and rebuild menu
        self.app._build_menu()
        self.app._update_state()

        self.app._pref_controller = None


class NewTagWindow(NSObject):
    """Small dialog for typing a new tag name and starting it immediately."""

    def initWithApp_(self, app):
        self = objc.super(NewTagWindow, self).init()
        if self is None:
            return None
        self.app = app
        return self

    def show(self):
        style_mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(320, 110)),
            style_mask,
            NSBackingStoreBuffered,
            False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("Start New Tag")
        window.center()

        stack = NSStackView.stackViewWithViews_([])
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setSpacing_(12.0)
        stack.setEdgeInsets_((20.0, 20.0, 20.0, 20.0))

        # Label
        lbl = NSTextField.labelWithString_("Tag name:")
        lbl.setFont_(NSFont.systemFontOfSize_(13))
        stack.addView_inGravity_(lbl, 1)

        # Text field
        self.txt_tag = NSTextField.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(280, 24)))
        self.txt_tag.setPlaceholderString_("e.g. mytask")
        stack.addView_inGravity_(self.txt_tag, 1)

        # Buttons
        btn_stack = NSStackView.stackViewWithViews_([])
        btn_stack.setSpacing_(8.0)

        btn_cancel = NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        btn_cancel.setKeyEquivalent_("\x1b")

        btn_start = NSButton.buttonWithTitle_target_action_("Start", self, "start:")
        btn_start.setKeyEquivalent_("\r")

        btn_stack.addView_inGravity_(btn_cancel, 3)
        btn_stack.addView_inGravity_(btn_start, 3)
        stack.addView_inGravity_(btn_stack, 3)

        window.setContentView_(stack)
        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        # Focus the text field immediately
        window.makeFirstResponder_(self.txt_tag)

    @objc.typedSelector(b"v@:@")
    def cancel_(self, sender):
        self.window.close()
        self.app._new_tag_controller = None

    @objc.typedSelector(b"v@:@")
    def start_(self, sender):
        tag = self.txt_tag.stringValue().strip()
        self.window.close()
        self.app._new_tag_controller = None
        if tag:
            start_tag(tag)
            self.app._update_state()


class AddPastTaskPreviewView(NSView):
    """Draws a live timeline preview of a single day, showing existing tasks
    as grey/blue bars, and the new task as a green (or red on overlap) bar.
    """
    def initWithFrame_(self, frame):
        self = objc.super(AddPastTaskPreviewView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._intervals = []  # list of dicts with local start/end times
        self._new_start = None
        self._new_end = None
        self._hour_start = 8
        self._hour_end = 18
        self.window_controller = None
        self.setAutoresizingMask_(NSViewWidthSizable)
        return self

    def acceptsFirstResponder(self):
        return True

    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._drag_start_x = pt.x
        self._drag_current_x = pt.x
        self._is_dragging = True
        self._update_times_from_drag()

    def mouseDragged_(self, event):
        if not getattr(self, "_is_dragging", False):
            return
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._drag_current_x = pt.x
        self._update_times_from_drag()

    def mouseUp_(self, event):
        self._is_dragging = False

    @objc.python_method
    def _update_times_from_drag(self):
        if getattr(self, "window_controller", None) is None:
            return
        
        bounds = self.bounds()
        width = bounds.size.width
        pad_left = 15.0
        pad_right = 15.0
        graph_w = width - pad_left - pad_right
        if graph_w <= 0:
            return
            
        total_hours = self._hour_end - self._hour_start
        
        def x_to_time(x):
            fraction = (x - pad_left) / graph_w
            fraction = max(0.0, min(1.0, fraction))
            delta_hours = fraction * total_hours
            total_minutes = int(round(delta_hours * 60))
            h = self._hour_start + (total_minutes // 60)
            m = total_minutes % 60
            if h >= 24:
                h = 23
                m = 59
            return h, m
            
        sh, sm = x_to_time(min(self._drag_start_x, self._drag_current_x))
        eh, em = x_to_time(max(self._drag_start_x, self._drag_current_x))
        
        if sh == eh and sm == em:
            # Min duration 15 minutes
            eh, em = x_to_time(min(self._drag_start_x, self._drag_current_x) + (15.0 * graph_w / (total_hours * 60.0)))
            
        self.window_controller.previewDraggedToStartHour_startMinute_endHour_endMinute_(sh, sm, eh, em)

    @objc.python_method
    def updateData_newStart_newEnd_(self, intervals, new_start, new_end):
        self._intervals = intervals
        self._new_start = new_start
        self._new_end = new_end
        
        hours = [8, 18]
        if new_start:
            hours.append(new_start.hour)
        if new_end:
            hours.append(new_end.hour)
        for inv in intervals:
            hours.append(inv["start"].hour)
            hours.append(inv["end"].hour)
            
        self._hour_start = max(0, min(hours) - 1)
        self._hour_end = min(23, max(hours) + 1)
        
        if (self._hour_end - self._hour_start) < 8:
            diff = 8 - (self._hour_end - self._hour_start)
            self._hour_start = max(0, self._hour_start - diff // 2)
            self._hour_end = min(23, self._hour_start + 8)
            if (self._hour_end - self._hour_start) < 8:
                self._hour_start = max(0, self._hour_end - 8)
                
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty_rect):
        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height
        
        NSColor.windowBackgroundColor().set()
        NSBezierPath.fillRect_(bounds)
        NSGraphicsContext.currentContext().setShouldAntialias_(True)
        
        pad_left = 15.0
        pad_right = 15.0
        pad_top = 18.0
        pad_bottom = 8.0
        
        graph_w = width - pad_left - pad_right
        graph_h = height - pad_top - pad_bottom
        
        total_hours = self._hour_end - self._hour_start
        if total_hours <= 0:
            total_hours = 1
            
        def get_x(dt):
            if not dt:
                return pad_left
            delta_hours = (dt.hour - self._hour_start) + (dt.minute / 60.0) + (dt.second / 3600.0)
            fraction = delta_hours / total_hours
            fraction = max(0.0, min(1.0, fraction))
            return pad_left + fraction * graph_w

        font = NSFont.systemFontOfSize_(8.0)
        text_color = NSColor.secondaryLabelColor()
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: text_color
        }
        
        line_path = NSBezierPath.bezierPath()
        line_path.setLineWidth_(0.5)
        line_path.moveToPoint_(NSPoint(pad_left, height - pad_top))
        line_path.lineToPoint_(NSPoint(width - pad_right, height - pad_top))
        NSColor.separatorColor().set()
        line_path.stroke()
        
        step = 1 if total_hours <= 12 else 2
        for h in range(self._hour_start, self._hour_end + 1, step):
            fraction = (h - self._hour_start) / total_hours
            x = pad_left + fraction * graph_w
            
            grid_path = NSBezierPath.bezierPath()
            grid_path.setLineWidth_(0.5)
            grid_path.moveToPoint_(NSPoint(x, height - pad_top))
            grid_path.lineToPoint_(NSPoint(x, pad_bottom))
            NSColor.separatorColor().set()
            grid_path.stroke()
            
            label = NSString.stringWithString_(f"{h:02d}")
            label_size = label.sizeWithAttributes_(attrs)
            label.drawAtPoint_withAttributes_(
                NSPoint(x - label_size.width / 2.0, height - pad_top + 2),
                attrs
            )

        lane_height = graph_h
        y = pad_bottom + 1.0
        
        for inv in self._intervals:
            x_start = get_x(inv["start"])
            x_end = get_x(inv["end"])
            if x_end - x_start < 2.0:
                x_end = x_start + 2.0
                
            bar_rect = NSRect(NSPoint(x_start, y), NSSize(x_end - x_start, lane_height - 2.0))
            bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, (lane_height - 2.0) / 4.0, (lane_height - 2.0) / 4.0
            )
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.5, 0.35).set()
            bar_path.fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.5, 0.6).set()
            bar_path.setLineWidth_(0.75)
            bar_path.stroke()

        if self._new_start and self._new_end:
            has_overlap = False
            for inv in self._intervals:
                overlap_start = max(inv["start"], self._new_start)
                overlap_end = min(inv["end"], self._new_end)
                if overlap_start < overlap_end:
                    has_overlap = True
                    break
                    
            x_start = get_x(self._new_start)
            x_end = get_x(self._new_end)
            if x_end - x_start < 2.0:
                x_end = x_start + 2.0
                
            bar_rect = NSRect(NSPoint(x_start, y), NSSize(x_end - x_start, lane_height - 2.0))
            bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, (lane_height - 2.0) / 4.0, (lane_height - 2.0) / 4.0
            )
            
            if has_overlap:
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.92, 0.26, 0.21, 0.35).set()
                bar_path.fill()
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.92, 0.26, 0.21, 0.95).set()
                bar_path.setLineWidth_(1.5)
                bar_path.stroke()
            else:
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.67, 0.38, 0.35).set()
                bar_path.fill()
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.67, 0.38, 0.95).set()
                bar_path.setLineWidth_(1.5)
                bar_path.stroke()


class AddPastTaskWindow(NSObject):
    """Dialog for tracking a past task (creating a new past interval)."""

    def initWithApp_(self, app):
        self = objc.super(AddPastTaskWindow, self).init()
        if self is None:
            return None
        self.app = app
        self._pref_start = None
        self._pref_end = None
        self._on_save = None
        return self

    @objc.typedSelector(b"@@:@@@@")
    def initWithApp_startDt_endDt_onSave_(self, app, start_dt, end_dt, on_save):
        self = objc.super(AddPastTaskWindow, self).init()
        if self is None:
            return None
        self.app = app
        self._pref_start = start_dt
        self._pref_end = end_dt
        self._on_save = on_save
        return self

    def show(self):
        style_mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(420, 310)),
            style_mask,
            NSBackingStoreBuffered,
            False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("Register past task")
        window.center()

        stack = NSStackView.stackViewWithViews_([])
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setSpacing_(10.0)
        stack.setEdgeInsets_((16.0, 20.0, 16.0, 20.0))

        LABEL_W = 80
        FIELD_W = 280

        def _row(label_text, view):
            row = NSStackView.stackViewWithViews_([])
            row.setOrientation_(0)
            row.setAlignment_(8)  # Center Y
            row.setSpacing_(8.0)
            lbl = NSTextField.labelWithString_(label_text)
            lbl.setFont_(NSFont.systemFontOfSize_(12))
            lbl.setAlignment_(1)  # Right
            lbl.setTranslatesAutoresizingMaskIntoConstraints_(False)
            lbl.widthAnchor().constraintEqualToConstant_(float(LABEL_W)).setActive_(True)
            row.addView_inGravity_(lbl, 1)
            row.addView_inGravity_(view, 1)
            return row

        # ── tags dropdown menu ───────────────────────────────────────────────
        self.popup_tags = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSRect(NSPoint(0, 0), NSSize(FIELD_W, 24)), False
        )
        
        config = self.app._config if (self.app is not None) else load_config()
        recent_range = config.get("recent_range", ":month")
        recent_tags = get_recent_tags(recent_range)
        all_tags = get_all_tags()

        pinned_tags = config.get("pinned_tags", [])
        all_tags_set = set(all_tags)
        pinned_exist = sorted([t for t in pinned_tags if t in all_tags_set])
        pinned_set = set(pinned_exist)
        
        main_tags = sorted(list(set(recent_tags)))
        unpinned_main = [t for t in main_tags if t not in pinned_set]
        older_tags = [t for t in all_tags if t not in main_tags and t not in pinned_set]

        menu = self.popup_tags.menu()
        menu.removeAllItems()
        
        has_added = False
        if pinned_exist:
            for tag in pinned_exist:
                menu.addItemWithTitle_action_keyEquivalent_(tag, None, "")
            has_added = True
            
        if unpinned_main:
            if has_added:
                menu.addItem_(NSMenuItem.separatorItem())
            for tag in unpinned_main:
                menu.addItemWithTitle_action_keyEquivalent_(tag, None, "")
            has_added = True
            
        if older_tags:
            if has_added:
                menu.addItem_(NSMenuItem.separatorItem())
            for tag in older_tags:
                menu.addItemWithTitle_action_keyEquivalent_(tag, None, "")
            has_added = True
            
        if not has_added:
            menu.addItemWithTitle_action_keyEquivalent_("(no tags)", None, "")

        self.popup_tags.setTarget_(self)
        self.popup_tags.setAction_("tagSelectionChanged:")
        stack.addView_inGravity_(_row("Tags:", self.popup_tags), 1)

        # ── date picker ──────────────────────────────────────────────────────
        self.dp_date = NSDatePicker.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(120, 24)))
        self.dp_date.setDatePickerStyle_(NSDatePickerStyleTextFieldAndStepper)
        self.dp_date.setDatePickerElements_(NSDatePickerElementFlagYearMonthDay)
        
        now = datetime.now()
        start_default = now - timedelta(hours=1)
        end_default = now
        
        if getattr(self, "_pref_start", None) is not None:
            start_default = self._pref_start
        if getattr(self, "_pref_end", None) is not None:
            end_default = self._pref_end

        ns_date = _NSDate.dateWithTimeIntervalSince1970_(start_default.timestamp())
        self.dp_date.setDateValue_(ns_date)
        stack.addView_inGravity_(_row("Date:", self.dp_date), 1)

        # ── start time picker ────────────────────────────────────────────────
        self.dp_start = NSDatePicker.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(100, 24)))
        self.dp_start.setDatePickerStyle_(NSDatePickerStyleTextFieldAndStepper)
        self.dp_start.setDatePickerElements_(NSDatePickerElementFlagHourMinute)
        
        ns_start = _NSDate.dateWithTimeIntervalSince1970_(start_default.timestamp())
        self.dp_start.setDateValue_(ns_start)
        stack.addView_inGravity_(_row("Start Time:", self.dp_start), 1)

        # ── end time picker ──────────────────────────────────────────────────
        self.dp_end = NSDatePicker.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(100, 24)))
        self.dp_end.setDatePickerStyle_(NSDatePickerStyleTextFieldAndStepper)
        self.dp_end.setDatePickerElements_(NSDatePickerElementFlagHourMinute)
        
        ns_end = _NSDate.dateWithTimeIntervalSince1970_(end_default.timestamp())
        self.dp_end.setDateValue_(ns_end)
        stack.addView_inGravity_(_row("End Time:", self.dp_end), 1)

        # ── live preview view ────────────────────────────────────────────────
        self.preview_view = AddPastTaskPreviewView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(FIELD_W, 50))
        )
        self.preview_view.window_controller = self
        self.preview_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.preview_view.heightAnchor().constraintEqualToConstant_(42.0).setActive_(True)
        self.preview_view.widthAnchor().constraintEqualToConstant_(float(FIELD_W)).setActive_(True)
        stack.addView_inGravity_(_row("Preview:", self.preview_view), 1)

        self.dp_date.setTarget_(self)
        self.dp_date.setAction_("dateTimeChanged:")
        self.dp_start.setTarget_(self)
        self.dp_start.setAction_("dateTimeChanged:")
        self.dp_end.setTarget_(self)
        self.dp_end.setAction_("dateTimeChanged:")

        # ── chk_adjust ───────────────────────────────────────────────────────
        self.chk_adjust = NSButton.buttonWithTitle_target_action_(
            "Automatically fix overlaps (:adjust)", self, None
        )
        self.chk_adjust.setButtonType_(3)
        self.chk_adjust.setState_(NSControlStateValueOn)  # default to On for track command
        self.chk_adjust.setFont_(NSFont.systemFontOfSize_(11))
        
        row_chk = NSStackView.stackViewWithViews_([])
        row_chk.setOrientation_(0)
        row_chk.setSpacing_(8.0)
        # Empty placeholder spacer to align with the labels
        spacer = NSView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(LABEL_W, 1)))
        spacer.setTranslatesAutoresizingMaskIntoConstraints_(False)
        spacer.widthAnchor().constraintEqualToConstant_(float(LABEL_W)).setActive_(True)
        row_chk.addView_inGravity_(spacer, 1)
        row_chk.addView_inGravity_(self.chk_adjust, 1)
        stack.addView_inGravity_(row_chk, 1)

        # ── error label ──────────────────────────────────────────────────────
        self.lbl_error = NSTextField.labelWithString_("")
        self.lbl_error.setTextColor_(NSColor.systemRedColor())
        self.lbl_error.setFont_(NSFont.systemFontOfSize_(11))
        
        row_err = NSStackView.stackViewWithViews_([])
        row_err.setOrientation_(0)
        row_err.setSpacing_(8.0)
        row_err.addView_inGravity_(spacer, 1)
        row_err.addView_inGravity_(self.lbl_error, 1)
        stack.addView_inGravity_(row_err, 1)

        # ── buttons ──────────────────────────────────────────────────────────
        btn_stack = NSStackView.stackViewWithViews_([])
        btn_stack.setSpacing_(8.0)
        btn_cancel = NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        btn_cancel.setKeyEquivalent_("\x1b")
        btn_save = NSButton.buttonWithTitle_target_action_("Add Task", self, "add:")
        btn_save.setKeyEquivalent_("\r")
        btn_stack.addView_inGravity_(btn_cancel, 3)
        btn_stack.addView_inGravity_(btn_save, 3)
        
        row_btn = NSStackView.stackViewWithViews_([])
        row_btn.setOrientation_(0)
        row_btn.setSpacing_(8.0)
        row_btn.addView_inGravity_(spacer, 1)
        row_btn.addView_inGravity_(btn_stack, 1)
        stack.addView_inGravity_(row_btn, 1)

        window.setContentView_(stack)
        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        window.makeFirstResponder_(self.popup_tags)

        # Initial preview draw
        self.dateTimeChanged_(None)

    @objc.python_method
    def previewDraggedToStartHour_startMinute_endHour_endMinute_(self, sh, sm, eh, em):
        try:
            picked_date = datetime.fromtimestamp(self.dp_date.dateValue().timeIntervalSince1970())
            start_dt = picked_date.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end_dt = picked_date.replace(hour=eh, minute=em, second=0, microsecond=0)
            
            ns_start = _NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp())
            ns_end = _NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp())
            
            self.dp_start.setDateValue_(ns_start)
            self.dp_end.setDateValue_(ns_end)
            self.dateTimeChanged_(None)
        except Exception as e:
            print(f"Error updating pickers from drag: {e}")

    @objc.typedSelector(b"v@:@")
    def tagSelectionChanged_(self, sender):
        self.dateTimeChanged_(None)

    @objc.typedSelector(b"v@:@")
    def dateTimeChanged_(self, sender):
        try:
            picked_date = datetime.fromtimestamp(self.dp_date.dateValue().timeIntervalSince1970())
            picked_start = datetime.fromtimestamp(self.dp_start.dateValue().timeIntervalSince1970())
            picked_end = datetime.fromtimestamp(self.dp_end.dateValue().timeIntervalSince1970())

            start_dt = picked_date.replace(hour=picked_start.hour, minute=picked_start.minute, second=0, microsecond=0).astimezone()
            end_dt = picked_date.replace(hour=picked_end.hour, minute=picked_end.minute, second=0, microsecond=0).astimezone()

            intervals = get_intervals_for_date(picked_date.date())
            self.preview_view.updateData_newStart_newEnd_(intervals, start_dt, end_dt)

            has_overlap = False
            for inv in intervals:
                overlap_start = max(inv["start"], start_dt)
                overlap_end = min(inv["end"], end_dt)
                if overlap_start < overlap_end:
                    has_overlap = True
                    break

            if has_overlap:
                self.lbl_error.setTextColor_(NSColor.systemRedColor())
                self.lbl_error.setStringValue_("⚠️ Overlaps with an existing task!")
            else:
                self.lbl_error.setStringValue_("")
        except Exception as e:
            print(f"Error updating preview: {e}")

    @objc.typedSelector(b"v@:@")
    def cancel_(self, sender):
        self.window.close()
        if self.app is not None:
            self.app._add_past_controller = None

    @objc.typedSelector(b"v@:@")
    def add_(self, sender):
        tag = self.popup_tags.titleOfSelectedItem()
        if not tag or tag == "(no tags)":
            self.lbl_error.setStringValue_("At least one tag is required.")
            return

        tags = [tag]

        # 1. Parse date from date picker
        picked_date = datetime.fromtimestamp(self.dp_date.dateValue().timeIntervalSince1970())
        # 2. Parse start/end times
        picked_start = datetime.fromtimestamp(self.dp_start.dateValue().timeIntervalSince1970())
        picked_end = datetime.fromtimestamp(self.dp_end.dateValue().timeIntervalSince1970())

        # Construct start/end datetimes matching the picked date
        start_dt = picked_date.replace(hour=picked_start.hour, minute=picked_start.minute, second=0, microsecond=0)
        end_dt = picked_date.replace(hour=picked_end.hour, minute=picked_end.minute, second=0, microsecond=0)

        if end_dt <= start_dt:
            self.lbl_error.setStringValue_("End time must be after start time.")
            return

        def _dt_to_timew(dt):
            return dt.astimezone().strftime("%Y%m%dT%H%M%S%z")

        adjust = self.chk_adjust.state() == NSControlStateValueOn
        hints = [":adjust"] if adjust else []

        try:
            run_checked("track", _dt_to_timew(start_dt), "-", _dt_to_timew(end_dt), *(tags + hints))
        except Exception as e:
            self.lbl_error.setStringValue_(str(e))
            return

        self.window.close()
        if self.app is not None:
            self.app._add_past_controller = None
            self.app._update_state()
        if getattr(self, "_on_save", None) is not None:
            self._on_save()


class WhatsNewWindow(NSObject):
    """Scrollable window that displays Changes.md with basic Markdown rendering.

    ## headings are bold+large, - bullet lines are indented, **text** is bold.
    Falls back to plain text if the file is missing.
    """

    def initWithApp_(self, app):
        self = objc.super(WhatsNewWindow, self).init()
        if self is None:
            return None
        self.app = app
        return self

    def show(self):
        from AppKit import (
            NSTextView, NSScrollView, NSFont as _NSFont,
            NSAttributedString, NSMutableParagraphStyle,
            NSFontAttributeName as _FA,
            NSForegroundColorAttributeName as _CA,
            NSParagraphStyleAttributeName as _PA,
        )
        from Foundation import NSMutableAttributedString as _MAS

        # Read file
        path = get_changes_path()
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    raw = f.read()
            except Exception:
                raw = "(Could not read Changes.md)"
        else:
            raw = "(Changes.md not found)"

        W, H = 580, 540
        style_mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                      | NSWindowStyleMaskResizable)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(W, H)),
            style_mask, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("What's New in Arête")
        window.center()

        sv = NSScrollView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        sv.setHasVerticalScroller_(True)
        sv.setHasHorizontalScroller_(False)
        sv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        sv.setAutohidesScrollers_(True)

        tv = NSTextView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setAutoresizingMask_(NSViewWidthSizable)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.textContainer().setContainerSize_(NSSize(W - 24, 1e7))
        tv.setTextContainerInset_(NSSize(14, 14))

        full = _MAS.alloc().init()

        h1_font    = _NSFont.boldSystemFontOfSize_(15)
        h2_font    = _NSFont.boldSystemFontOfSize_(13)
        body_font  = _NSFont.systemFontOfSize_(12)
        bold_font  = _NSFont.boldSystemFontOfSize_(12)
        label_col  = NSColor.labelColor()
        muted_col  = NSColor.secondaryLabelColor()

        def _para(indent=0, space_before=0, space_after=4):
            p = NSMutableParagraphStyle.alloc().init()
            p.setHeadIndent_(float(indent))
            p.setFirstLineHeadIndent_(float(indent))
            p.setParagraphSpacingBefore_(float(space_before))
            p.setParagraphSpacing_(float(space_after))
            return p

        def _append(text, font, color, para):
            attrs = {_FA: font, _CA: color, _PA: para}
            full.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            )

        def _append_inline(line, font, color, para):
            """Append a line supporting **bold** spans, ending with newline."""
            parts = line.split("**")
            for i, part in enumerate(parts):
                f = bold_font if i % 2 == 1 else font
                if part:
                    attrs = {_FA: f, _CA: color, _PA: para}
                    full.appendAttributedString_(
                        NSAttributedString.alloc().initWithString_attributes_(part, attrs)
                    )
            attrs = {_FA: font, _CA: color, _PA: para}
            full.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_("\n", attrs)
            )

        for line in raw.splitlines():
            stripped = line.rstrip()
            if stripped.startswith("## "):
                _append(stripped[3:] + "\n", h2_font, label_col,
                        _para(space_before=10, space_after=4))
            elif stripped.startswith("# "):
                _append(stripped[2:] + "\n", h1_font, label_col,
                        _para(space_before=14, space_after=6))
            elif stripped.startswith("- "):
                _append_inline("\u2022 " + stripped[2:],
                               body_font, muted_col, _para(indent=14, space_after=2))
            elif stripped == "":
                _append("\n", body_font, muted_col, _para(space_after=0))
            else:
                _append_inline(stripped, body_font, muted_col, _para(space_after=2))

        tv.textStorage().setAttributedString_(full)
        sv.setDocumentView_(tv)
        window.setContentView_(sv)
        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        tv.scrollRangeToVisible_((0, 0))





class UpdateAvailableWindow(NSObject):
    """Scrollable window shown when a newer version is available.

    Displays the remote changelog so the user can decide whether to download,
    with Download and Cancel buttons in a bottom bar.
    """

    def initWithCurrentVersion_remoteVersion_changelog_(self, current, remote, changelog):
        self = objc.super(UpdateAvailableWindow, self).init()
        if self is None:
            return None
        self._current = current
        self._remote = remote
        self._changelog = changelog
        return self

    def show(self):
        from AppKit import (
            NSTextView, NSScrollView, NSFont as _NSFont,
            NSAttributedString, NSMutableParagraphStyle,
            NSFontAttributeName as _FA,
            NSForegroundColorAttributeName as _CA,
            NSParagraphStyleAttributeName as _PA,
        )
        from Foundation import NSMutableAttributedString as _MAS

        raw = self._changelog if self._changelog else "(Changelog not available)"

        W, H = 580, 540
        BTN_H = 44  # height of the button bar at the bottom

        style_mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                      | NSWindowStyleMaskResizable)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(W, H)),
            style_mask, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_(
            "Update available — version %s (you have %s)"
            % (self._remote, self._current)
        )
        window.center()

        content = NSView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        # ── scroll + text view ──────────────────────────────────────────────
        sv_h = H - BTN_H
        sv = NSScrollView.alloc().initWithFrame_(
            NSRect(NSPoint(0, BTN_H), NSSize(W, sv_h))
        )
        sv.setHasVerticalScroller_(True)
        sv.setHasHorizontalScroller_(False)
        sv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        sv.setAutohidesScrollers_(True)

        tv = NSTextView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, sv_h)))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setAutoresizingMask_(NSViewWidthSizable)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.textContainer().setContainerSize_(NSSize(W - 24, 1e7))
        tv.setTextContainerInset_(NSSize(14, 14))

        full = _MAS.alloc().init()
        h1_font   = _NSFont.boldSystemFontOfSize_(15)
        h2_font   = _NSFont.boldSystemFontOfSize_(13)
        body_font = _NSFont.systemFontOfSize_(12)
        bold_font = _NSFont.boldSystemFontOfSize_(12)
        label_col = NSColor.labelColor()
        muted_col = NSColor.secondaryLabelColor()

        def _para(indent=0, space_before=0, space_after=4):
            p = NSMutableParagraphStyle.alloc().init()
            p.setHeadIndent_(float(indent))
            p.setFirstLineHeadIndent_(float(indent))
            p.setParagraphSpacingBefore_(float(space_before))
            p.setParagraphSpacing_(float(space_after))
            return p

        def _append(text, font, color, para):
            attrs = {_FA: font, _CA: color, _PA: para}
            full.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            )

        def _append_inline(line, font, color, para):
            parts = line.split("**")
            for i, part in enumerate(parts):
                f = bold_font if i % 2 == 1 else font
                if part:
                    attrs = {_FA: f, _CA: color, _PA: para}
                    full.appendAttributedString_(
                        NSAttributedString.alloc().initWithString_attributes_(part, attrs)
                    )
            attrs = {_FA: font, _CA: color, _PA: para}
            full.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_("\n", attrs)
            )

        for line in raw.splitlines():
            stripped = line.rstrip()
            if stripped.startswith("## "):
                _append(stripped[3:] + "\n", h2_font, label_col,
                        _para(space_before=10, space_after=4))
            elif stripped.startswith("# "):
                _append(stripped[2:] + "\n", h1_font, label_col,
                        _para(space_before=14, space_after=6))
            elif stripped.startswith("- "):
                _append_inline("\u2022 " + stripped[2:],
                               body_font, muted_col, _para(indent=14, space_after=2))
            elif stripped == "":
                _append("\n", body_font, muted_col, _para(space_after=0))
            else:
                _append_inline(stripped, body_font, muted_col, _para(space_after=2))

        tv.textStorage().setAttributedString_(full)
        sv.setDocumentView_(tv)

        # ── button bar ──────────────────────────────────────────────────────
        btn_bar = NSView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(W, BTN_H))
        )
        btn_bar.setAutoresizingMask_(NSViewWidthSizable)

        sep = NSBox.alloc().initWithFrame_(
            NSRect(NSPoint(0, BTN_H - 1), NSSize(W, 1))
        )
        sep.setBoxType_(2)  # NSBoxSeparator
        sep.setAutoresizingMask_(NSViewWidthSizable)
        btn_bar.addSubview_(sep)

        btn_download = NSButton.alloc().initWithFrame_(
            NSRect(NSPoint(W - 210, 8), NSSize(100, 28))
        )
        btn_download.setTitle_("Download")
        btn_download.setBezelStyle_(1)  # NSBezelStyleRounded
        btn_download.setKeyEquivalent_("\r")
        btn_download.setTarget_(self)
        btn_download.setAction_("downloadClicked:")
        btn_download.setAutoresizingMask_(64)  # NSViewMinXMargin

        btn_cancel = NSButton.alloc().initWithFrame_(
            NSRect(NSPoint(W - 104, 8), NSSize(90, 28))
        )
        btn_cancel.setTitle_("Cancel")
        btn_cancel.setBezelStyle_(1)
        btn_cancel.setKeyEquivalent_("\x1b")
        btn_cancel.setTarget_(self)
        btn_cancel.setAction_("cancelClicked:")
        btn_cancel.setAutoresizingMask_(64)  # NSViewMinXMargin

        btn_bar.addSubview_(btn_download)
        btn_bar.addSubview_(btn_cancel)

        content.addSubview_(sv)
        content.addSubview_(btn_bar)
        window.setContentView_(content)

        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        tv.scrollRangeToVisible_((0, 0))

    @objc.typedSelector(b"v@:@")
    def downloadClicked_(self, sender):
        import subprocess as _sp
        _sp.Popen(["open", UPDATE_DOWNLOAD_URL])
        self.window.close()
        self.window = None

    @objc.typedSelector(b"v@:@")
    def cancelClicked_(self, sender):
        self.window.close()
        self.window = None


class HelpWindow(NSObject):
    """Scrollable help window explaining every menu item and UI feature."""

    _HELP = [
        # (heading, body)
        ('Menu bar title',
         'When tracking is active the title shows the running tag(s), e.g. "dmi" or "dmi,ruv". '
         'When idle the Arête icon is shown instead.'),
        ('Timeline (top of menu)',
         'A mini timeline of today\'s tracked intervals. '
         'The vertical blue line marks the current time.'),
        ('Tag list',
         'Each recent tag appears as a checkable item. '
         'Click an unchecked tag to switch to it (stops any currently running tag). '
         'Click a checked tag to stop tracking it. '
         'If it is the last active tag and "Prompt for annotation when stopping" is enabled, '
         'an annotation dialog will appear.'),
        ('Older tags (submenu)',
         'Tags used outside the recent range (default: this month). '
         'Behaves exactly like the main tag list.'),
        ('New tag\u2026',
         'Opens a small dialog to type a new tag name and start tracking it immediately.'),
        ('Register past task\u2026',
         'Opens a dialog to track a task that occurred in the past. '
         'You can select the date, specify the start and end times, type the tags, and optionally automatically resolve overlaps.'),
        ('Add tag\u2026 (submenu)',
         'Only visible while tracking. '
         'Lists all tags not currently active. '
         'Click one to add it alongside the running tag(s) without stopping them. '
         'Recent tags appear at the top; older tags are in the "Older tags" submenu.'),
        ('Stop all tracking',
         'Stops all active tracking immediately. '
         'If "Prompt for annotation when stopping" is enabled, an annotation dialog appears.'),
        ('Annotate active task',
         'Only enabled while tracking. '
         'Opens a dialog to attach a short note to the currently running interval. '
         'The note is stored inside Timewarrior and shown in reports and tooltips.'),
        ('Refresh tags',
         'Re-reads all tags from Timewarrior and rebuilds the menu. '
         'Useful after adding tags directly on the command line.'),
        ('Logbook',
         'Opens the Logbook with Day / Week / Month / Custom tabs. '
         'Each tab shows a timeline, pie chart, time summary table, and \u2014 when present \u2014 '
         'an annotations table.\n\n'
         '\u2022 Filter tags: use the \u201cFilter tags\u2026\u201d pull-down to narrow the view to specific tags. '
         'Click \u201c\u2715 Clear filter\u201d to reset.\n'
         '\u2022 Prev / Next (\u25c4 \u25ba): navigate to earlier or later periods.\n'
         '\u2022 Drag an interval edge: hover near the left or right edge of any bar until the '
         'cursor changes to a resize arrow, then drag to move the start or end time. '
         'Times snap to the nearest minute. '
         'If the change would overlap an adjacent interval, a prompt asks whether to adjust it automatically.\n'
         '\u2022 Right-click a bar: opens a context menu with Annotate\u2026 and Edit interval\u2026 options.\n'
         '\u2022 Edit interval\u2026: opens a window with a draggable day-view scrubber for precise '
         'start/end adjustment, plus a tags field.\n'
         '\u2022 Right-click the summary table: copy data as CSV.'),
        ('Logbook \u2014 editing intervals',
         'There are two ways to adjust an interval\u2019s times:\n\n'
         '1. Drag directly in the timeline \u2014 hover near the left edge of a bar to move its '
         'start time, or near the right edge to move its end time. '
         'The cursor becomes a left\u2013right resize arrow. '
         'Drag and release; the time label floats above the drag line as you move. '
         'If the new position overlaps a neighbouring interval, you will be asked whether to '
         'adjust it automatically (:adjust) or cancel the change.\n\n'
         '2. Right-click \u2192 Edit interval\u2026 \u2014 opens a dedicated window showing a '
         'full-day scrubber bar. Drag the start handle (left circle) or end handle (right circle) '
         'to set the times precisely. The exact timestamp is shown below the scrubber and '
         'updates live. Use the Tags field to rename or reassign tags, and tick '
         '\u201cAutomatically fix overlaps (:adjust)\u201d to resolve conflicts on save.'),
        ('Preferences\u2026',
         'Opens the combined Preferences & About window.\n\n'
         '\u2022 Pause tracking when screen locked \u2014 automatically stops the active tag when the '
         'screen locks and resumes it on unlock.\n'
         '\u2022 Prompt for annotation when stopping \u2014 opens an annotation dialog every time a tag '
         'is stopped or switched.\n'
         '\u2022 Start at login \u2014 adds Ar\u00eate to macOS login items.\n'
         '\u2022 Show empty days in logbook \u2014 includes days with no tracked time in week/month views.\n'
         '\u2022 Recent tags range \u2014 how far back "recent" tags extend (default: month).\n'
         '\u2022 Daily work target \u2014 used for the % of target column and pie chart remainder.'),
        ('Annotations in logbook',
         'Any interval with an annotation shows its text inside the timeline bar (when the bar '
         'is wide enough) and in a tooltip on hover. '
         'A separate "Annotations" table below the pie chart lists every annotated interval '
         'for the period, sorted by end time. Right-click it to copy as CSV.'),
    ]

    def initWithApp_(self, app):
        self = objc.super(HelpWindow, self).init()
        if self is None:
            return None
        self.app = app
        return self

    def show(self):
        from AppKit import (
            NSTextView, NSScrollView, NSFont as _NSFont,
            NSMutableAttributedString, NSAttributedString,
            NSParagraphStyle, NSMutableParagraphStyle,
            NSFontAttributeName as _FA,
            NSForegroundColorAttributeName as _CA,
        )
        from Foundation import NSMutableAttributedString as _MAS

        W, H = 560, 520
        style_mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(W, H)),
            style_mask, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("Arête — Help")
        window.center()

        sv = NSScrollView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        sv.setHasVerticalScroller_(True)
        sv.setHasHorizontalScroller_(False)
        sv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        sv.setAutohidesScrollers_(True)

        tv = NSTextView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(W, H)))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setAutoresizingMask_(NSViewWidthSizable)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.textContainer().setContainerSize_(NSSize(W - 24, 1e7))
        tv.setTextContainerInset_(NSSize(12, 12))

        heading_font = _NSFont.boldSystemFontOfSize_(13)
        body_font    = _NSFont.systemFontOfSize_(12)
        muted_color  = NSColor.secondaryLabelColor()
        label_color  = NSColor.labelColor()

        para = NSMutableParagraphStyle.alloc().init()
        para.setParagraphSpacing_(6.0)
        para.setParagraphSpacingBefore_(10.0)

        body_para = NSMutableParagraphStyle.alloc().init()
        body_para.setParagraphSpacing_(2.0)
        from AppKit import NSParagraphStyleAttributeName
        full = _MAS.alloc().init()

        for heading, body in self._HELP:
            h_attrs = {
                _FA: heading_font,
                _CA: label_color,
                NSParagraphStyleAttributeName: para,
            }
            b_attrs = {
                _FA: body_font,
                _CA: muted_color,
                NSParagraphStyleAttributeName: body_para,
            }
            full.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    heading + "\n", h_attrs
                )
            )
            full.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    body + "\n", b_attrs
                )
            )

        tv.textStorage().setAttributedString_(full)
        sv.setDocumentView_(tv)

        window.setContentView_(sv)
        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        # Scroll to top
        tv.scrollRangeToVisible_((0, 0))



class AnnotateWindow(NSObject):
    """Dialog for adding or editing an annotation on a timew interval.

    interval_id : int — the timew @id to annotate (1 = most recent)
    existing    : str — pre-fill text (empty string for new annotations)
    on_save     : optional callable() invoked after a successful save
    tags_hint   : optional str shown in the subtitle (e.g. "dmi, work")
    """

    def initWithApp_intervalId_existing_onSave_tagsHint_(
            self, app, interval_id, existing, on_save, tags_hint):
        self = objc.super(AnnotateWindow, self).init()
        if self is None:
            return None
        self.app = app
        self._interval_id = interval_id
        self._existing = existing or ""
        self._on_save = on_save
        self._tags_hint = tags_hint or ""
        return self

    def show(self):
        style_mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(380, 140)),
            style_mask,
            NSBackingStoreBuffered,
            False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("Annotate Interval")
        window.center()

        stack = NSStackView.stackViewWithViews_([])
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setSpacing_(10.0)
        stack.setEdgeInsets_((20.0, 20.0, 20.0, 20.0))

        # Label
        lbl_text = f"Annotation for @{self._interval_id}"
        if self._tags_hint:
            lbl_text += f"  ({self._tags_hint})"
        lbl = NSTextField.labelWithString_(lbl_text)
        lbl.setFont_(NSFont.systemFontOfSize_(13))
        stack.addView_inGravity_(lbl, 1)

        # Text field
        self.txt_annotation = NSTextField.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(340, 24))
        )
        self.txt_annotation.setPlaceholderString_("Describe what you worked on…")
        self.txt_annotation.setStringValue_(self._existing)
        stack.addView_inGravity_(self.txt_annotation, 1)

        # Buttons
        btn_stack = NSStackView.stackViewWithViews_([])
        btn_stack.setSpacing_(8.0)

        btn_cancel = NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        btn_cancel.setKeyEquivalent_("\x1b")

        btn_save = NSButton.buttonWithTitle_target_action_("Save", self, "save:")
        btn_save.setKeyEquivalent_("\r")

        btn_stack.addView_inGravity_(btn_cancel, 3)
        btn_stack.addView_inGravity_(btn_save, 3)
        stack.addView_inGravity_(btn_stack, 3)

        window.setContentView_(stack)
        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        window.makeFirstResponder_(self.txt_annotation)

    @objc.typedSelector(b"v@:@")
    def cancel_(self, sender):
        self.window.close()
        self._cleanup()

    @objc.typedSelector(b"v@:@")
    def save_(self, sender):
        text = self.txt_annotation.stringValue().strip()
        self.window.close()
        self._cleanup()
        # Always write if text is non-empty; also write (clears) if it was
        # non-empty before and the user blanked it out.
        if text or self._existing:
            run("annotate", f"@{self._interval_id}", text)
        if self._on_save:
            self._on_save()

    def _cleanup(self):
        if self.app is not None:
            self.app._annotate_controller = None




# ---------------------------------------------------------------------------
# Draggable time-scrubber view used by EditIntervalWindow
# ---------------------------------------------------------------------------

class TimeScrubberView(NSView):
    """A horizontal bar spanning the day with draggable start/end handles.

    The track represents the hours visible_start_h … visible_end_h (default
    0–24).  Two circular handles mark the start and end of the interval.
    Dragging a handle snaps to the nearest minute and updates a live time
    label.  The view calls self._on_change(start_dt, end_dt) whenever a
    handle is released.
    """

    TRACK_H    = 8.0    # height of the grey track bar
    HANDLE_R   = 9.0    # radius of the drag handle circle
    VIEW_H     = 56.0   # total view height
    PAD_X      = 16.0   # horizontal padding inside the view

    def initWithStartDt_endDt_isActive_onChange_(
            self, start_dt, end_dt, is_active, on_change):
        frame = NSRect(NSPoint(0, 0), NSSize(440.0, self.VIEW_H))
        self = objc.super(TimeScrubberView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._start_dt  = start_dt
        self._end_dt    = end_dt      # None when interval is still active
        self._is_active = is_active
        self._on_change = on_change   # callable(start_dt, end_dt_or_None)
        self._dragging  = None        # "start" | "end" | None
        self.setAutoresizingMask_(NSViewWidthSizable)
        return self

    # ── geometry helpers ────────────────────────────────────────────────────

    def _track_rect(self):
        w  = self.bounds().size.width
        cx = self.VIEW_H / 2.0
        return (self.PAD_X, cx - self.TRACK_H / 2.0,
                w - 2 * self.PAD_X, self.TRACK_H)

    def _frac_for_dt(self, dt):
        """Fraction [0, 1] of dt within the day of _start_dt."""
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return max(0.0, min(1.0,
            (dt - day_start).total_seconds() / 86400.0))

    def _x_for_dt(self, dt):
        tx, _ty, tw, _th = self._track_rect()
        return tx + self._frac_for_dt(dt) * tw

    def _dt_for_x(self, x):
        """Snap x position to nearest minute within the same day."""
        tx, _ty, tw, _th = self._track_rect()
        frac = max(0.0, min(1.0, (x - tx) / tw))
        day_start = self._start_dt.replace(
            hour=0, minute=0, second=0, microsecond=0)
        total_mins = round(frac * 1440)   # snap to minute
        return day_start + timedelta(minutes=total_mins)

    # ── drawing ─────────────────────────────────────────────────────────────

    def drawRect_(self, dirty_rect):
        import math
        NSColor.windowBackgroundColor().set()
        NSBezierPath.fillRect_(self.bounds())
        NSGraphicsContext.currentContext().setShouldAntialias_(True)

        tx, ty, tw, th = self._track_rect()
        cx = ty + th / 2.0          # vertical centre of the track

        # ── background track ────────────────────────────────────────────────
        track_rect = NSRect(NSPoint(tx, ty), NSSize(tw, th))
        r = th / 2.0
        bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            track_rect, r, r)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.75, 0.75, 0.75, 0.4).set()
        bg_path.fill()

        # ── filled interval bar ─────────────────────────────────────────────
        x0 = self._x_for_dt(self._start_dt)
        x1 = self._x_for_dt(self._end_dt) if self._end_dt else (tx + tw)
        bar_rect = NSRect(NSPoint(x0, ty), NSSize(max(2.0, x1 - x0), th))
        bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bar_rect, r, r)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.23, 0.51, 0.82, 0.85).set()
        bar_path.fill()

        # ── hour tick marks ─────────────────────────────────────────────────
        small_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(8.0),
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
        }
        tick_y_top = ty + th + 3.0
        for h in range(0, 25, 3):
            frac = h / 24.0
            x    = tx + frac * tw
            tick = NSBezierPath.bezierPath()
            tick.setLineWidth_(0.5)
            tick.moveToPoint_(NSPoint(x, tick_y_top))
            tick.lineToPoint_(NSPoint(x, tick_y_top + 4.0))
            NSColor.secondaryLabelColor().set()
            tick.stroke()
            lbl = NSString.stringWithString_(f"{h:02d}")
            lbl_sz = lbl.sizeWithAttributes_(small_attrs)
            lbl.drawAtPoint_withAttributes_(
                NSPoint(x - lbl_sz.width / 2.0, tick_y_top + 5.0),
                small_attrs)

        # ── handles ─────────────────────────────────────────────────────────
        def draw_handle(x, label, is_end=False):
            hc = NSPoint(x, cx)
            hr = self.HANDLE_R
            circle = NSBezierPath.bezierPathWithOvalInRect_(
                NSRect(NSPoint(hc.x - hr, hc.y - hr), NSSize(hr * 2, hr * 2))
            )
            NSColor.whiteColor().set()
            circle.fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.23, 0.51, 0.82, 1.0).set()
            circle.setLineWidth_(2.0)
            circle.stroke()
            # time label above the handle
            lbl_attrs = {
                NSFontAttributeName: NSFont.boldSystemFontOfSize_(9.5),
                NSForegroundColorAttributeName: NSColor.labelColor(),
            }
            ns_lbl = NSString.stringWithString_(label)
            sz = ns_lbl.sizeWithAttributes_(lbl_attrs)
            ns_lbl.drawAtPoint_withAttributes_(
                NSPoint(hc.x - sz.width / 2.0, hc.y + hr + 1.0),
                lbl_attrs)

        draw_handle(x0, self._start_dt.strftime("%-H:%M"))
        if self._end_dt:
            draw_handle(x1, self._end_dt.strftime("%-H:%M"), is_end=True)

    # ── mouse interaction ────────────────────────────────────────────────────

    def _hit_handle(self, pt):
        """Return "start", "end", or None depending on which handle is hit."""
        x0 = self._x_for_dt(self._start_dt)
        x1 = self._x_for_dt(self._end_dt) if self._end_dt else None
        _, ty, _, th = self._track_rect()
        cy = ty + th / 2.0
        r  = self.HANDLE_R + 4.0   # generous hit area
        import math
        if math.hypot(pt.x - x0, pt.y - cy) <= r:
            return "start"
        if x1 is not None and math.hypot(pt.x - x1, pt.y - cy) <= r:
            return "end"
        return None

    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._dragging = self._hit_handle(pt)
        if self._dragging:
            NSCursor.closedHandCursor().push()

    def mouseDragged_(self, event):
        if not self._dragging:
            return
        pt  = self.convertPoint_fromView_(event.locationInWindow(), None)
        new_dt = self._dt_for_x(pt.x)
        if self._dragging == "start":
            # Don't let start cross end
            if self._end_dt and new_dt >= self._end_dt:
                new_dt = self._end_dt - timedelta(minutes=1)
            self._start_dt = new_dt
        else:
            # Don't let end cross start
            if new_dt <= self._start_dt:
                new_dt = self._start_dt + timedelta(minutes=1)
            self._end_dt = new_dt
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        if self._dragging:
            NSCursor.pop()
            self._dragging = None
            if self._on_change:
                self._on_change(self._start_dt, self._end_dt)

    def resetCursorRects(self):
        # Show an open-hand cursor over the handles
        x0 = self._x_for_dt(self._start_dt)
        x1 = self._x_for_dt(self._end_dt) if self._end_dt else None
        _, ty, _, th = self._track_rect()
        cy = ty + th / 2.0
        r  = self.HANDLE_R + 4.0
        for x in filter(None, [x0, x1]):
            self.addCursorRect_cursor_(
                NSRect(NSPoint(x - r, cy - r), NSSize(r * 2, r * 2)),
                NSCursor.openHandCursor(),
            )

    def acceptsFirstResponder(self):
        return True

    @objc.python_method
    def updateStartDt_endDt_(self, start_dt, end_dt):
        """Update displayed datetimes from outside (e.g. clock picker) and redraw."""
        self._start_dt = start_dt
        self._end_dt   = end_dt
        self.setNeedsDisplay_(True)


# ---------------------------------------------------------------------------
# Edit Interval dialog
# ---------------------------------------------------------------------------

class EditIntervalWindow(NSObject):
    """Dialog for editing the start/end times and tags of a timew interval.

    interval_id : int   — timew @id
    start_dt    : datetime (local) — pre-filled start
    end_dt      : datetime (local) — pre-filled end (None if active/open)
    tags        : list[str]        — current tags
    on_save     : callable()       — called after a successful save
    """

    _TIME_FMT = "%Y-%m-%d %H:%M"

    def initWithIntervalId_startDt_endDt_tags_onSave_(
            self, interval_id, start_dt, end_dt, tags, on_save):
        self = objc.super(EditIntervalWindow, self).init()
        if self is None:
            return None
        self._interval_id = interval_id
        self._start_dt    = start_dt
        self._end_dt      = end_dt
        self._orig_tags   = list(tags)
        self._on_save     = on_save
        return self

    def show(self):
        style_mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(480, 280)),
            style_mask, NSBackingStoreBuffered, False
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_(f"Edit Interval @{self._interval_id}")
        window.center()

        stack = NSStackView.stackViewWithViews_([])
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setSpacing_(10.0)
        stack.setEdgeInsets_((16.0, 20.0, 16.0, 20.0))

        # ── helpers: datetime <-> NSDate ─────────────────────────────────────
        def _dt_to_nsdate(dt):
            return _NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())

        # ── time scrubber ────────────────────────────────────────────────────
        def _on_scrub_change(s_dt, e_dt):
            # Keep datetimes in sync so save_() can read them directly
            self._start_dt = s_dt
            self._end_dt   = e_dt
            # Update the clock pickers
            self.dp_start.setDateValue_(_dt_to_nsdate(s_dt))
            if e_dt:
                self.dp_end.setDateValue_(_dt_to_nsdate(e_dt))

        is_active = self._end_dt is None
        self.scrubber = TimeScrubberView.alloc(
        ).initWithStartDt_endDt_isActive_onChange_(
            self._start_dt,
            self._end_dt,
            is_active,
            _on_scrub_change,
        )
        self.scrubber.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.scrubber.heightAnchor().constraintEqualToConstant_(
            TimeScrubberView.VIEW_H).setActive_(True)
        stack.addView_inGravity_(self.scrubber, 1)

        # ── clock pickers for start / end ─────────────────────────────────
        LABEL_W = 46

        def _make_clock_picker(dt_or_none):
            dp = NSDatePicker.alloc().initWithFrame_(
                NSRect(NSPoint(0, 0), NSSize(90, 24)))
            dp.setDatePickerStyle_(NSDatePickerStyleTextFieldAndStepper)
            dp.setDatePickerElements_(NSDatePickerElementFlagHourMinute)
            if dt_or_none:
                dp.setDateValue_(_dt_to_nsdate(dt_or_none))
            dp.setEnabled_(dt_or_none is not None)
            return dp

        def _picker_row(prefix, dp):
            row = NSStackView.stackViewWithViews_([])
            row.setOrientation_(0)
            row.setAlignment_(8)
            row.setSpacing_(6.0)
            key_lbl = NSTextField.labelWithString_(prefix)
            key_lbl.setFont_(NSFont.systemFontOfSize_(11))
            key_lbl.setTextColor_(NSColor.secondaryLabelColor())
            key_lbl.setAlignment_(1)
            key_lbl.setTranslatesAutoresizingMaskIntoConstraints_(False)
            key_lbl.widthAnchor().constraintEqualToConstant_(float(LABEL_W)).setActive_(True)
            row.addView_inGravity_(key_lbl, 1)
            row.addView_inGravity_(dp, 1)
            return row

        self.dp_start = _make_clock_picker(self._start_dt)
        self.dp_start.setTarget_(self)
        self.dp_start.setAction_("pickerStartChanged:")

        self.dp_end = _make_clock_picker(self._end_dt)
        self.dp_end.setTarget_(self)
        self.dp_end.setAction_("pickerEndChanged:")

        stack.addView_inGravity_(_picker_row("Start:", self.dp_start), 1)
        stack.addView_inGravity_(_picker_row("End:",   self.dp_end),   1)

        # ── thin separator ───────────────────────────────────────────────────
        sep = NSBox.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(10, 1)))
        sep.setBoxType_(2)
        stack.addView_inGravity_(sep, 1)

        # ── tags field ───────────────────────────────────────────────────────
        FIELD_W = 380
        tags_row = NSStackView.stackViewWithViews_([])
        tags_row.setOrientation_(0)
        tags_row.setAlignment_(8)
        tags_row.setSpacing_(8.0)
        tags_lbl = NSTextField.labelWithString_("Tags:")
        tags_lbl.setFont_(NSFont.systemFontOfSize_(12))
        tags_lbl.setAlignment_(1)
        tags_lbl.setTranslatesAutoresizingMaskIntoConstraints_(False)
        tags_lbl.widthAnchor().constraintEqualToConstant_(float(LABEL_W)).setActive_(True)
        self.txt_tags = NSTextField.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(FIELD_W - LABEL_W - 8, 22))
        )
        self.txt_tags.setStringValue_(
            " ".join(shlex.quote(t) if " " in t else t for t in self._orig_tags)
        )
        self.txt_tags.setPlaceholderString_("space-separated tags")
        tags_row.addView_inGravity_(tags_lbl, 1)
        tags_row.addView_inGravity_(self.txt_tags, 1)
        stack.addView_inGravity_(tags_row, 1)

        # ── :adjust checkbox ────────────────────────────────────────────────
        self.chk_adjust = NSButton.buttonWithTitle_target_action_(
            "Automatically fix overlaps (:adjust)", self, None
        )
        self.chk_adjust.setButtonType_(3)
        self.chk_adjust.setState_(NSControlStateValueOff)
        self.chk_adjust.setFont_(NSFont.systemFontOfSize_(11))
        stack.addView_inGravity_(self.chk_adjust, 1)

        # ── error label ──────────────────────────────────────────────────────
        self.lbl_error = NSTextField.labelWithString_("")
        self.lbl_error.setTextColor_(NSColor.systemRedColor())
        self.lbl_error.setFont_(NSFont.systemFontOfSize_(11))
        stack.addView_inGravity_(self.lbl_error, 1)

        # ── buttons ──────────────────────────────────────────────────────────
        btn_stack = NSStackView.stackViewWithViews_([])
        btn_stack.setSpacing_(8.0)
        btn_cancel = NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        btn_cancel.setKeyEquivalent_("\x1b")
        btn_save = NSButton.buttonWithTitle_target_action_("Save", self, "save:")
        btn_save.setKeyEquivalent_("\r")
        btn_stack.addView_inGravity_(btn_cancel, 3)
        btn_stack.addView_inGravity_(btn_save, 3)
        stack.addView_inGravity_(btn_stack, 3)

        window.setContentView_(stack)
        self.window = window
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.typedSelector(b"v@:@")
    def pickerStartChanged_(self, sender):
        """Called when the start clock picker value changes."""
        picked = datetime.fromtimestamp(sender.dateValue().timeIntervalSince1970())
        new_dt = self._start_dt.replace(
            hour=picked.hour, minute=picked.minute, second=0, microsecond=0)
        # Don't let start cross end
        if self._end_dt and new_dt >= self._end_dt:
            new_dt = self._end_dt - timedelta(minutes=1)
            self.dp_start.setDateValue_(
                _NSDate.dateWithTimeIntervalSince1970_(new_dt.timestamp()))
        self._start_dt = new_dt
        self.scrubber.updateStartDt_endDt_(self._start_dt, self._end_dt)

    @objc.typedSelector(b"v@:@")
    def pickerEndChanged_(self, sender):
        """Called when the end clock picker value changes."""
        if self._end_dt is None:
            return
        picked = datetime.fromtimestamp(sender.dateValue().timeIntervalSince1970())
        new_dt = self._end_dt.replace(
            hour=picked.hour, minute=picked.minute, second=0, microsecond=0)
        # Don't let end cross start
        if new_dt <= self._start_dt:
            new_dt = self._start_dt + timedelta(minutes=1)
            self.dp_end.setDateValue_(
                _NSDate.dateWithTimeIntervalSince1970_(new_dt.timestamp()))
        self._end_dt = new_dt
        self.scrubber.updateStartDt_endDt_(self._start_dt, self._end_dt)

    @objc.typedSelector(b"v@:@")
    def cancel_(self, sender):
        self.window.close()

    @objc.typedSelector(b"v@:@")
    def save_(self, sender):
        tags_str = self.txt_tags.stringValue().strip()

        def _dt_to_timew(dt):
            return dt.astimezone().strftime("%Y%m%dT%H%M%S%z")

        iid    = f"@{self._interval_id}"
        adjust = self.chk_adjust.state() == NSControlStateValueOn
        hints  = [":adjust"] if adjust else []

        try:
            run_checked("modify", "start", iid, _dt_to_timew(self._start_dt), *hints)

            if self._end_dt is not None:
                run_checked("modify", "end", iid, _dt_to_timew(self._end_dt), *hints)

            # Update tags
            try:
                new_tags = set(t for t in shlex.split(tags_str) if t)
            except ValueError:
                new_tags = set(t for t in tags_str.split() if t)
            orig_tags = set(self._orig_tags)
            to_remove = orig_tags - new_tags
            to_add    = new_tags - orig_tags
            if to_remove:
                run_checked("untag", iid, *sorted(to_remove))
            if to_add:
                run_checked("tag", iid, *sorted(to_add))

        except RuntimeError as e:
            self.lbl_error.setStringValue_(str(e))
            return

        self.window.close()
        if self._on_save:
            self._on_save()


def find_timew_path():
    """Find the path to the timew executable.

    Search order:
    1. Common system/user install locations (PATH, Homebrew, Nix, …)
    2. Bundled timew inside the .app (fallback for users without a separate install)
    """
    # 1. PATH
    path = shutil.which("timew")
    if path:
        return path

    # 2. Common install locations not always on PATH
    common_paths = [
        "/opt/homebrew/bin/timew",
        "/usr/local/bin/timew",
        os.path.expanduser("~/.nix-profile/bin/timew"),
        "/run/current-system/sw/bin/timew",
        os.path.expanduser("~/.local/bin/timew"),
    ]
    for p in common_paths:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p

    # 3. Bundled binary shipped inside the .app bundle (built by build_dmg.sh)
    bundle = get_bundle_path()
    if bundle:
        bundled = os.path.join(bundle, "Contents", "Resources", "timew")
        if os.path.exists(bundled) and os.access(bundled, os.X_OK):
            return bundled

    # 4. Last resort: hope "timew" appears on PATH at runtime
    return "timew"


TIMEW = find_timew_path()
REFRESH_INTERVAL = 5  # seconds


def _init_timewarrior():
    """Answer 'yes' to timew's first-run setup prompt if needed.

    On first run timew asks interactively whether to create its config and
    database directories.  Inside a .app bundle there is no stdin, so it
    hangs forever.  We detect the prompt by running timew with input='yes\\n'
    and stdin=PIPE; subsequent runs are unaffected because the directories
    already exist.
    """
    try:
        subprocess.run(
            [TIMEW],
            input="yes\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        pass


def run(*args):
    """Run a timew sub-command and return stdout (stripped)."""
    try:
        result = subprocess.run(
            [TIMEW] + list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def run_checked(*args):
    """Like run() but raises RuntimeError on non-zero exit, with stderr as message."""
    result = subprocess.run(
        [TIMEW] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout).strip()
        raise RuntimeError(msg or f"timew {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def parse_tags_output(out):
    """Parse output from `timew tags` or similar command and return sorted tag list."""
    tags = []
    tag_col_width = None
    past_separator = False
    for line in out.splitlines():
        if not line:
            continue
        # Check header (exact match to avoid skipping tags like "tag1", "tag2")
        if line.lower().startswith("tag ") or line.lower() == "tag":
            continue
        # Check separator
        if line.startswith("-"):
            tag_col_width = line.index(" ") if " " in line else len(line)
            past_separator = True
            continue
        # Only parse data lines that follow the separator — ignore any
        # freeform messages like "No data found." or "There is no active…"
        if not past_separator:
            continue

        tag = line[:tag_col_width].strip() if tag_col_width is not None else line.split()[0]
        if tag:
            tags.append(tag)
    return sorted(tags)


def get_all_tags():
    """Return sorted list of all known tags from `timew tags`."""
    return parse_tags_output(run("tags"))


def get_recent_tags(range_arg=":month"):
    """Return sorted list of tags from `timew tags <range_arg>`."""
    if not range_arg:
        return parse_tags_output(run("tags"))
    # Split by whitespace to support multi-word ranges (e.g., ":month" or "from 2026-08-01")
    args = range_arg.split()
    return parse_tags_output(run("tags", *args))


def get_intervals_for_date(day_date):
    """Return tracked intervals for any date (date/datetime) as list of dicts with local start/end times."""
    date_str = day_date.strftime("%Y-%m-%d")
    out = run("export", date_str)
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []

    intervals = []
    local_tz = datetime.now().astimezone().tzinfo

    for item in data:
        start_str = item.get("start")
        if not start_str:
            continue

        try:
            clean_start = start_str.replace("T", "").replace("Z", "+0000")
            start_utc = datetime.strptime(clean_start, "%Y%m%d%H%M%S%z")
        except Exception:
            continue

        start_local = start_utc.astimezone(local_tz)

        end_str = item.get("end")
        if end_str:
            try:
                clean_end = end_str.replace("T", "").replace("Z", "+0000")
                end_utc = datetime.strptime(clean_end, "%Y%m%d%H%M%S%z")
                end_local = end_utc.astimezone(local_tz)
            except Exception:
                end_local = datetime.now(local_tz)
        else:
            end_local = datetime.now(local_tz)

        # Make sure the interval belongs to the requested day (sometimes timew exports adjacent days)
        if start_local.date() == day_date or end_local.date() == day_date:
            intervals.append({
                "start": start_local,
                "end": end_local,
                "tags": item.get("tags", [])
            })

    return intervals


def get_today_intervals():
    """Return today's tracked intervals as list of dicts with local start/end times."""
    from datetime import date
    today_str = date.today().strftime("%Y-%m-%d")
    out = run("export", today_str)
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []

    intervals = []
    local_tz = datetime.now().astimezone().tzinfo

    for item in data:
        start_str = item.get("start")
        if not start_str:
            continue

        try:
            clean_start = start_str.replace("T", "").replace("Z", "+0000")
            start_utc = datetime.strptime(clean_start, "%Y%m%d%H%M%S%z")
        except Exception:
            continue

        start_local = start_utc.astimezone(local_tz)

        end_str = item.get("end")
        if end_str:
            try:
                clean_end = end_str.replace("T", "").replace("Z", "+0000")
                end_utc = datetime.strptime(clean_end, "%Y%m%d%H%M%S%z")
                end_local = end_utc.astimezone(local_tz)
            except Exception:
                end_local = datetime.now(local_tz)
        else:
            end_local = datetime.now(local_tz)

        intervals.append({
            "start": start_local,
            "end": end_local,
            "tags": item.get("tags", [])
        })

    return intervals


def draw_timeline(intervals):
    """Draw a daily timeline graph representing tracked intervals."""
    # Split multi-tag intervals into separate single-tag intervals for distinct lanes
    flat_intervals = []
    for inv in intervals:
        tags = inv.get("tags", [])
        if not tags:
            flat_intervals.append(inv)
        else:
            for tag in tags:
                flat_intervals.append({
                    "start": inv["start"],
                    "end": inv["end"],
                    "tag": tag
                })
    intervals = flat_intervals

    width = 320
    height = 50
    img = NSImage.alloc().initWithSize_(NSSize(width, height))
    img.lockFocus()

    context = NSGraphicsContext.currentContext()
    context.setShouldAntialias_(True)

    # Calculate scale
    now = datetime.now().astimezone()
    if intervals:
        start_hour = min(i["start"].hour for i in intervals)
        end_hour = max(i["end"].hour for i in intervals)
        end_hour = max(end_hour, now.hour)
    else:
        start_hour = max(0, now.hour - 4)
        end_hour = min(23, now.hour + 4)

    # Ensure minimum span of 8 hours
    if (end_hour - start_hour) < 8:
        diff = 8 - (end_hour - start_hour)
        start_hour = max(0, start_hour - diff // 2)
        end_hour = min(23, start_hour + 8)
        if (end_hour - start_hour) < 8:
            start_hour = max(0, end_hour - 8)

    total_hours = end_hour - start_hour

    pad_left = 15
    pad_right = 15
    pad_top = 18
    pad_bottom = 8

    graph_w = width - pad_left - pad_right
    graph_h = height - pad_top - pad_bottom

    def get_x(dt):
        delta_hours = (dt.hour - start_hour) + (dt.minute / 60.0) + (dt.second / 3600.0)
        fraction = delta_hours / total_hours
        fraction = max(0.0, min(1.0, fraction))
        return pad_left + fraction * graph_w

    # 1. Draw Hour Labels and vertical tick lines
    font = NSFont.systemFontOfSize_(9.0)
    text_color = NSColor.secondaryLabelColor()
    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: text_color
    }

    # Horizontal baseline
    line_path = NSBezierPath.bezierPath()
    line_path.setLineWidth_(0.5)
    line_path.moveToPoint_(NSPoint(pad_left, height - pad_top))
    line_path.lineToPoint_(NSPoint(width - pad_right, height - pad_top))
    NSColor.separatorColor().set()
    line_path.stroke()

    for h in range(start_hour, end_hour + 1):
        fraction = (h - start_hour) / total_hours
        x = pad_left + fraction * graph_w

        # Draw vertical grid line
        grid_path = NSBezierPath.bezierPath()
        grid_path.setLineWidth_(0.5)
        grid_path.moveToPoint_(NSPoint(x, height - pad_top))
        grid_path.lineToPoint_(NSPoint(x, pad_bottom))
        NSColor.separatorColor().set()
        grid_path.stroke()

        # Draw hour label
        from Foundation import NSString
        label = NSString.stringWithString_(f"{h:02d}")
        label_size = label.sizeWithAttributes_(attrs)
        label.drawAtPoint_withAttributes_(
            NSPoint(x - label_size.width / 2.0, height - pad_top + 3),
            attrs
        )

    # 2. Lane allocation & Interval Drawing
    sorted_intervals = sorted(intervals, key=lambda x: x["start"])
    lanes = []  # List of end times for each lane

    interval_draw_data = []
    for inv in sorted_intervals:
        x_start = get_x(inv["start"])
        x_end = get_x(inv["end"])

        if x_end - x_start < 2.0:
            x_end = x_start + 2.0

        lane_idx = 0
        while lane_idx < len(lanes):
            if lanes[lane_idx] <= inv["start"]:
                break
            lane_idx += 1

        if lane_idx == len(lanes):
            lanes.append(inv["end"])
        else:
            lanes[lane_idx] = inv["end"]

        interval_draw_data.append((x_start, x_end, lane_idx))

    # Draw bars
    num_lanes = max(len(lanes), 1)
    lane_height = min(12.0, graph_h / num_lanes)

    for x_start, x_end, lane_idx in interval_draw_data:
        y = height - pad_top - (lane_idx + 1) * lane_height + 2.0
        bar_rect = NSRect(
            NSPoint(x_start, y),
            NSSize(x_end - x_start, lane_height - 3.0)
        )
        bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bar_rect,
            (lane_height - 3.0) / 2.0,
            (lane_height - 3.0) / 2.0
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.51, 0.82, 0.4).set()
        bar_path.fill()

        bar_path.setLineWidth_(1.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23, 0.51, 0.82, 0.9).set()
        bar_path.stroke()

    # 3. Draw Vertical Current Time indicator line
    if start_hour <= now.hour <= end_hour:
        now_x = get_x(now)
        now_path = NSBezierPath.bezierPath()
        now_path.setLineWidth_(1.5)
        now_path.moveToPoint_(NSPoint(now_x, height - 3))
        now_path.lineToPoint_(NSPoint(now_x, pad_bottom - 2))

        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.5, 1.0, 1.0).set()
        now_path.stroke()

        dot_radius = 2.5
        dot_path = NSBezierPath.bezierPathWithOvalInRect_(NSRect(
            NSPoint(now_x - dot_radius, height - 3 - dot_radius),
            NSSize(dot_radius * 2, dot_radius * 2)
        ))
        dot_path.fill()

    img.unlockFocus()
    return img


def get_active_tracking_info():
    """Return tuple of (active_tags_set, duration_string).
    
    If not tracking, returns (set(), None).
    """
    out = run("get", "dom.active")
    if out.strip() == "0":
        return set(), None
    status = run()
    # Parse tags (using shlex to preserve quoted tags with spaces)
    match_tags = re.search(r"^Tracking\s+(.+)$", status, re.MULTILINE)
    active_tags = set(shlex.split(match_tags.group(1))) if match_tags else set()
    # Parse duration (hours and minutes only)
    match_duration = re.search(r"Total\s+(\d+:\d+):\d+", status)
    duration = match_duration.group(1) if match_duration else None
    return active_tags, duration


def get_active_tags():
    """Return set of tags currently being tracked (empty if not tracking)."""
    active_tags, _ = get_active_tracking_info()
    return active_tags


def start_tag(tag, add_to_active=False):
    """Start tracking a tag.

    By default switches to *only* this tag (stops any other active tags).
    Pass add_to_active=True to append the tag to currently running tags instead.
    """
    if add_to_active:
        active = get_active_tags()
        active.add(tag)
        run("start", *sorted(active))
    else:
        run("start", tag)


def stop_tag(tag):
    """Stop tracking a tag, keeping other active tags running."""
    active = get_active_tags()
    active.discard(tag)
    if active:
        run("start", *sorted(active))
    else:
        run("stop")


class SplashWindow(NSObject):
    """Borderless splash panel shown briefly at startup, with fade in/out."""

    SHOW_DURATION  = 2.2   # seconds visible before fade-out begins
    FADE_IN_SECS   = 0.35
    FADE_OUT_SECS  = 0.45

    QUOTE = (
        "\u201cWe are what we repeatedly do, therefore, "
        "excellence is not an act, but a habit.\u201d"
        "\n\u2014 Aristotle"
    )

    def show(self):
        from AppKit import NSAnimationContext

        PAD     = 36
        ICON    = 120
        W       = 400
        NAME_H  = 34
        VER_H   = 18
        SUB_H   = 18
        QUOTE_H = 72
        GAP1    = 16   # below icon
        GAP2    = 4    # between name and version
        GAP3    = 6    # between version and subtitle
        GAP4    = 18   # between subtitle and quote
        H = PAD + ICON + GAP1 + NAME_H + GAP2 + VER_H + GAP3 + SUB_H + GAP4 + QUOTE_H + PAD

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(W, H)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False
        )
        panel.setReleasedWhenClosed_(False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(3)   # NSFloatingWindowLevel
        panel.setAlphaValue_(0.0)
        panel.center()

        # ── frosted-glass card ───────────────────────────────────────────────
        vfx = NSVisualEffectView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(W, H))
        )
        vfx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        vfx.setState_(NSVisualEffectStateActive)
        vfx.setWantsLayer_(True)
        vfx.layer().setCornerRadius_(20.0)
        vfx.layer().setMasksToBounds_(True)
        panel.setContentView_(vfx)

        content_w = W - 2 * PAD

        # Helper: AppKit y-up from top-down screen cursor
        def appkit_y(y_screen, h):
            return H - y_screen - h

        y = PAD   # top-down cursor

        # ── icon ────────────────────────────────────────────────────────────
        icon_path = get_icon_path()
        if icon_path:
            logo_img = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if logo_img:
                logo_img.setSize_(NSSize(ICON, ICON))
                iv = NSImageView.alloc().initWithFrame_(
                    NSRect(NSPoint((W - ICON) / 2, appkit_y(y, ICON)), NSSize(ICON, ICON))
                )
                iv.setImage_(logo_img)
                iv.setImageScaling_(3)
                vfx.addSubview_(iv)
        y += ICON + GAP1

        # ── app name ─────────────────────────────────────────────────────────
        lbl_name = NSTextField.labelWithString_("Ar\u00eate")
        lbl_name.setFont_(NSFont.boldSystemFontOfSize_(26))
        lbl_name.setAlignment_(1)   # centre
        lbl_name.setFrame_(NSRect(NSPoint(PAD, appkit_y(y, NAME_H)), NSSize(content_w, NAME_H)))
        vfx.addSubview_(lbl_name)
        y += NAME_H + GAP2

        # ── version ──────────────────────────────────────────────────────────
        lbl_ver = NSTextField.labelWithString_(f"Version {VERSION}")
        lbl_ver.setFont_(NSFont.systemFontOfSize_(12))
        lbl_ver.setTextColor_(NSColor.secondaryLabelColor())
        lbl_ver.setAlignment_(1)
        lbl_ver.setFrame_(NSRect(NSPoint(PAD, appkit_y(y, VER_H)), NSSize(content_w, VER_H)))
        vfx.addSubview_(lbl_ver)
        y += VER_H + GAP3

        # ── "Starting…" ───────────────────────────────────────────────────────
        lbl_sub = NSTextField.labelWithString_("Starting\u2026")
        lbl_sub.setFont_(NSFont.systemFontOfSize_(13))
        lbl_sub.setTextColor_(NSColor.tertiaryLabelColor())
        lbl_sub.setAlignment_(1)
        lbl_sub.setFrame_(NSRect(NSPoint(PAD, appkit_y(y, SUB_H)), NSSize(content_w, SUB_H)))
        vfx.addSubview_(lbl_sub)
        y += SUB_H + GAP4

        # ── thin separator ───────────────────────────────────────────────────
        sep_h = 1
        sep = NSBox.alloc().initWithFrame_(
            NSRect(NSPoint(PAD, appkit_y(y, sep_h)), NSSize(content_w, sep_h))
        )
        sep.setBoxType_(2)   # NSBoxSeparator
        vfx.addSubview_(sep)
        y += sep_h + 10

        # ── quote ────────────────────────────────────────────────────────────
        italic_11 = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
            NSFont.systemFontOfSize_(11), 1
        )
        q_h = QUOTE_H - 10   # leave some breathing room
        tf = NSTextField.alloc().initWithFrame_(
            NSRect(NSPoint(PAD, appkit_y(y, q_h)), NSSize(content_w, q_h))
        )
        tf.setStringValue_(self.QUOTE)
        tf.setFont_(italic_11)
        tf.setTextColor_(NSColor.secondaryLabelColor())
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
        tf.setAlignment_(1)
        tf.cell().setWraps_(True)
        tf.setMaximumNumberOfLines_(0)
        vfx.addSubview_(tf)

        self._panel = panel
        panel.makeKeyAndOrderFront_(None)

        # ── fade in ──────────────────────────────────────────────────────────
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(self.FADE_IN_SECS)
        panel.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

        # Schedule fade-out start
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self.SHOW_DURATION, self, "fadeOut:", None, False
        )

    @objc.typedSelector(b"v@:@")
    def fadeOut_(self, timer):
        from AppKit import NSAnimationContext
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(self.FADE_OUT_SECS)
        self._panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self.FADE_OUT_SECS + 0.05, self, "dismiss:", None, False
        )

    @objc.typedSelector(b"v@:@")
    def dismiss_(self, timer):
        self._panel.close()




UPDATE_VERSION_URL = "https://tanso.net/Arete/version"
UPDATE_CHANGES_URL = "https://tanso.net/Arete/Changes.md"
UPDATE_DOWNLOAD_URL = "https://tanso.net/Arete/Arete.dmg"


class UpdateChecker(NSObject):
    """Fetches the remote version string in a background thread and shows an
    NSAlert on the main thread with the result."""

    def initWithCurrentVersion_(self, current_version):
        self = objc.super(UpdateChecker, self).init()
        if self is None:
            return None
        self._current = current_version
        return self

    def check(self):
        """Spawn background thread — call this once."""
        t = threading.Thread(target=self._fetch, daemon=True)
        t.start()

    def _fetch(self):
        headers = {"User-Agent": "Arete/" + self._current}
        try:
            with urllib.request.urlopen(
                urllib.request.Request(UPDATE_VERSION_URL, headers=headers), timeout=5
            ) as resp:
                remote = resp.read(64).decode("utf-8").strip()
        except Exception as exc:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "showError:", str(exc), False
            )
            return
        # Fetch changelog; fall back silently if unavailable
        try:
            with urllib.request.urlopen(
                urllib.request.Request(UPDATE_CHANGES_URL, headers=headers), timeout=5
            ) as resp:
                self._remote_changelog = resp.read(128 * 1024).decode("utf-8")
        except Exception:
            self._remote_changelog = ""
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "showResult:", remote, False
        )

    @objc.typedSelector(b"v@:@")
    def showResult_(self, remote_version):
        if remote_version != self._current:
            # Open a scrollable window showing the remote changelog + Download button
            changelog = getattr(self, "_remote_changelog", "")
            win = UpdateAvailableWindow.alloc(
            ).initWithCurrentVersion_remoteVersion_changelog_(
                self._current, remote_version, changelog
            )
            # Store on self so GC doesn't collect it while the window is open
            self._update_win = win
            win.show()
        else:
            from AppKit import NSAlert
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Arête is up to date")
            alert.setInformativeText_("You are running version %s." % self._current)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            alert.runModal()

    @objc.typedSelector(b"v@:@")
    def showError_(self, error_message):
        from AppKit import NSAlert
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Could not check for updates")
        alert.setInformativeText_(
            "An error occurred while checking for updates:\n%s" % error_message
        )
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        alert.runModal()



class TimeBar(rumps.App):
    def __init__(self):
        super().__init__("", quit_button=None)
        _init_timewarrior()
        self._tag_items = {}
        self._active_tags_cache = set()
        self._config = load_config()
        self._locked_active_tags = set()
        self._current_progress_icon_path = None
        self._add_past_controller = None
        # Pre-render the monochrome template icon for the idle menu bar state
        self._menubar_icon_path = _make_menubar_icon()
        if self._menubar_icon_path:
            self.icon = self._menubar_icon_path
            self.template = True

        atexit.register(self._cleanup_temp_files)

        # Show splash while the first timew calls are in flight
        self._splash = SplashWindow.alloc().init()
        self._splash.show()

        # Set up observer for macOS screen lock/unlock notifications
        self._observer = NotificationObserver.alloc().initWithApp_(self)
        nc = NSDistributedNotificationCenter.defaultCenter()
        nc.addObserver_selector_name_object_(
            self._observer,
            "screenLocked:",
            "com.apple.screenIsLocked",
            None
        )
        nc.addObserver_selector_name_object_(
            self._observer,
            "screenUnlocked:",
            "com.apple.screenIsUnlocked",
            None
        )

        self._build_menu()
        self._update_state()

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _build_menu(self, active_tags=None):
        self.menu.clear()
        self._tag_items = {}

        if active_tags is None:
            active_tags = get_active_tags()

        # Add timeline graph at the very top of the menu using custom NSImageView
        self._timeline_item = None
        try:
            intervals = get_today_intervals()
            img = draw_timeline(intervals)
            
            self._timeline_item = rumps.MenuItem("")
            
            # Create high-quality custom image view inside the NSMenuItem
            image_view = NSImageView.alloc().initWithFrame_(NSRect(NSPoint(0, 0), NSSize(320, 50)))
            image_view.setImage_(img)
            self._timeline_item._menuitem.setView_(image_view)
            
            self.menu.add(self._timeline_item)
            self.menu.add(rumps.separator)
        except Exception as e:
            print(f"Error drawing timeline: {e}")

        recent_range = self._config.get("recent_range", ":month")
        recent_tags = get_recent_tags(recent_range)
        all_tags = get_all_tags()

        pinned_tags = self._config.get("pinned_tags", [])
        all_tags_set = set(all_tags)
        pinned_exist = sorted([t for t in pinned_tags if t in all_tags_set])
        pinned_set = set(pinned_exist)
        
        # Promote any active tags to the main menu
        main_tags = sorted(list(set(recent_tags) | active_tags))
        unpinned_main = [t for t in main_tags if t not in pinned_set]
        older_tags = [t for t in all_tags if t not in main_tags and t not in pinned_set]

        if not pinned_exist and not unpinned_main and not older_tags:
            self.menu.add(rumps.MenuItem("(no tags found)"))
        else:
            # 1. Pinned tags (always on top)
            if pinned_exist:
                for tag in pinned_exist:
                    item = rumps.MenuItem(tag, callback=self._toggle_tag)
                    item.tag_name = tag
                    self._tag_items[tag] = item
                    self.menu.add(item)
                
                # Separator if there are other unpinned tags to show below
                if unpinned_main or older_tags:
                    self.menu.add(rumps.separator)

            # 2. Main unpinned tags (recent + active)
            if unpinned_main:
                for tag in unpinned_main:
                    item = rumps.MenuItem(tag, callback=self._toggle_tag)
                    item.tag_name = tag
                    self._tag_items[tag] = item
                    self.menu.add(item)

            # 3. Older tags submenu
            if older_tags:
                if unpinned_main:
                    self.menu.add(rumps.separator)

                older_menu = rumps.MenuItem("Older tags")
                for tag in older_tags:
                    item = rumps.MenuItem(tag, callback=self._toggle_tag)
                    item.tag_name = tag
                    self._tag_items[tag] = item
                    older_menu.add(item)
                self.menu.add(older_menu)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Start new tag", callback=self._new_tag))
        self.menu.add(rumps.MenuItem("Register past task", callback=self._add_past_task))

        # Build Pin/Unpin tags submenu
        if all_tags:
            pin_menu = rumps.MenuItem("Pin / unpin tags")
            for tag in all_tags:
                item = rumps.MenuItem(tag, callback=self._toggle_pin)
                item.tag_name = tag
                item.state = tag in pinned_set
                pin_menu.add(item)
            self.menu.add(pin_menu)

        # "Add additional tag" and "Annotate active task" — only shown when tracking
        if active_tags:
            recent_tags_set = set(recent_tags)
            add_recent = [t for t in recent_tags if t not in active_tags]
            add_older  = [t for t in all_tags if t not in active_tags and t not in recent_tags_set]

            if add_recent or add_older:
                add_menu = rumps.MenuItem("Add additional tag")
                self._add_tag_menu = add_menu

                for tag in add_recent:
                    item = rumps.MenuItem(tag, callback=self._add_tag_clicked)
                    item.tag_name = tag
                    add_menu.add(item)

                if add_older:
                    if add_recent:
                        add_menu.add(rumps.separator)
                    older_sub = rumps.MenuItem("Older tags")
                    for tag in add_older:
                        item = rumps.MenuItem(tag, callback=self._add_tag_clicked)
                        item.tag_name = tag
                        older_sub.add(item)
                    add_menu.add(older_sub)

                self.menu.add(add_menu)

            self.menu.add(rumps.MenuItem("Annotate active task", callback=self._annotate_active))
            self.menu.add(rumps.MenuItem("Stop all tracking", callback=self._stop_all))

        self.menu.add(rumps.MenuItem("Refresh tags", callback=self._refresh_tags))
        self.menu.add(rumps.MenuItem("Logbook", callback=self._show_reports))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Preferences", callback=self._preferences))
        self.menu.add(rumps.MenuItem("What's New", callback=self._show_whats_new))
        self.menu.add(rumps.MenuItem("Check for updates", callback=self._check_for_updates))
        self.menu.add(rumps.MenuItem("Help", callback=self._show_help))
        self.menu.add(rumps.MenuItem("Exit Arête", callback=rumps.quit_application))

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _new_tag(self, _):
        if getattr(self, "_new_tag_controller", None):
            self._new_tag_controller.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        self._new_tag_controller = NewTagWindow.alloc().initWithApp_(self)
        self._new_tag_controller.show()

    def _add_past_task(self, _):
        if getattr(self, "_add_past_controller", None):
            self._add_past_controller.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        self._add_past_controller = AddPastTaskWindow.alloc().initWithApp_(self)
        self._add_past_controller.show()

    def _toggle_tag(self, sender):
        tag = getattr(sender, "tag_name", sender.title)
        if sender.state:
            # Tag is active — stop it
            active = get_active_tags()
            active.discard(tag)
            if active:
                # Other tags remain: re-start them (closes current interval → @2)
                run("start", *sorted(active))
                self._update_state()
                self._prompt_annotate_if_needed("@2")
            else:
                self._stop_with_optional_prompt()
        else:
            had_active = bool(get_active_tags())
            start_tag(tag)
            self._update_state()
            if had_active:
                self._prompt_annotate_if_needed("@2")

    def _add_tag_clicked(self, sender):
        """Add the selected tag to the currently active set."""
        tag = getattr(sender, "tag_name", sender.title)
        start_tag(tag, add_to_active=True)
        self._update_state()

    def _toggle_tag_by_name(self, tag):
        active = get_active_tags()
        if tag in active:
            active.discard(tag)
            if active:
                run("start", *sorted(active))
                self._update_state()
            else:
                self._stop_with_optional_prompt()
        else:
            start_tag(tag)
            self._update_state()

    def _toggle_pin(self, sender):
        tag = getattr(sender, "tag_name", sender.title)
        pinned = self._config.setdefault("pinned_tags", [])
        if tag in pinned:
            pinned.remove(tag)
        else:
            pinned.append(tag)
        save_config(self._config)
        self._build_menu()
        self._update_state()

    def _annotate_active(self, _):
        """Open annotation dialog for the currently active interval (@1)."""
        active = get_active_tags()
        if not active:
            return
        if getattr(self, "_annotate_controller", None):
            self._annotate_controller.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        tags_hint = ", ".join(sorted(active))
        # Fetch existing annotation from the active interval (@1)
        existing = ""
        try:
            out = run("export", "@1")
            if out:
                data = json.loads(out)
                if data:
                    existing = data[0].get("annotation", "")
        except Exception:
            pass
        self._annotate_controller = AnnotateWindow.alloc(
        ).initWithApp_intervalId_existing_onSave_tagsHint_(
            self, 1, existing, self._update_state, tags_hint
        )
        self._annotate_controller.show()


    def _show_whats_new(self, _):
        if getattr(self, "_whats_new_controller", None):
            self._whats_new_controller.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        self._whats_new_controller = WhatsNewWindow.alloc().initWithApp_(self)
        self._whats_new_controller.show()

    def _check_for_updates(self, _):
        checker = UpdateChecker.alloc().initWithCurrentVersion_(VERSION)
        # Keep a strong reference so the ObjC object isn't GC'd before callbacks fire
        self._update_checker = checker
        checker.check()

    def _show_help(self, _):
        if getattr(self, "_help_controller", None):
            self._help_controller.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        self._help_controller = HelpWindow.alloc().initWithApp_(self)
        self._help_controller.show()

    def _preferences(self, _):
        # If preferences window is already open, bring it to front
        if getattr(self, "_pref_controller", None):
            self._pref_controller.window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return

        self._pref_controller = PreferencesWindow.alloc().initWithApp_(self)
        self._pref_controller.show()

    def _show_reports(self, _):
        """Open the report window in-process — fast, no subprocess overhead."""
        # If window already exists, just bring it forward.
        if getattr(self, "_report_controller", None) is not None:
            self._report_controller.show()
            return

        # Lazy-load timereport.py once per process — PyObjC forbids redefining
        # ObjC classes, so exec_module must only ever run once.
        tr = getattr(TimeBar, "_timereport_module", None)
        if tr is None:
            here = os.path.dirname(os.path.abspath(__file__))
            spec = importlib.util.spec_from_file_location(
                "timereport", os.path.join(here, "timereport.py")
            )
            tr = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tr)
            TimeBar._timereport_module = tr

        workday_hours = self._config.get("workday_hours", 7.5)
        show_empty_days = self._config.get("show_empty_days", True)
        controller = tr.ReportWindowController.alloc().initWithWorkdayHours_showEmptyDays_(
            workday_hours, show_empty_days
        )

        # Switch to Regular so the reports window appears in Cmd-Tab.
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyRegular
        )

        def _on_reports_close():
            setattr(self, "_report_controller", None)
            # Switch back to accessory (no Dock icon, no Cmd-Tab entry) only
            # when no other report-style windows are still open.
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )

        controller._on_close = _on_reports_close
        controller.show()
        # Retain on self so GC doesn't collect it while the window is open.
        self._report_controller = controller

    def handle_screen_locked(self):
        if not self._config.get("pause_on_lock", False):
            return
        active = get_active_tags()
        if active:
            self._locked_active_tags = active
            run("stop")
            self._update_state()

    def handle_screen_unlocked(self):
        if not self._config.get("pause_on_lock", False):
            return
        if self._locked_active_tags:
            run("start", *sorted(self._locked_active_tags))
            self._locked_active_tags = set()
            self._update_state()

    def _stop_with_optional_prompt(self):
        """Stop all tracking; optionally prompt for annotation afterwards."""
        run("stop")
        self._update_state()
        self._prompt_annotate_if_needed("@1")

    def _prompt_annotate_if_needed(self, interval_ref):
        """Open annotation dialog for *interval_ref* if prompt_on_stop is set."""
        if not self._config.get("prompt_on_stop", False):
            return
        if getattr(self, "_annotate_controller", None):
            return
        existing = ""
        tags_hint = ""
        interval_id = 1
        try:
            out = run("export", interval_ref)
            if out:
                data = json.loads(out)
                if data:
                    existing = data[0].get("annotation", "")
                    tags_hint = ", ".join(data[0].get("tags", []))
                    interval_id = data[0].get("id", 1)
        except Exception:
            pass
        self._annotate_controller = AnnotateWindow.alloc(
        ).initWithApp_intervalId_existing_onSave_tagsHint_(
            self, interval_id, existing, None, tags_hint
        )
        self._annotate_controller.show()

    def _stop_all(self, _):
        self._stop_with_optional_prompt()

    def _refresh_tags(self, _):
        self._build_menu()
        self._update_state()

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    @rumps.timer(REFRESH_INTERVAL)
    def _tick(self, _):
        self._update_state()

    # ------------------------------------------------------------------
    # State sync
    # ------------------------------------------------------------------

    def _cleanup_temp_files(self):
        # Clean up the main static icon
        if getattr(self, "_menubar_icon_path", None) and os.path.exists(self._menubar_icon_path):
            try:
                os.unlink(self._menubar_icon_path)
            except Exception:
                pass
        # Clean up the dynamic progress icon
        if getattr(self, "_current_progress_icon_path", None) and os.path.exists(self._current_progress_icon_path):
            try:
                os.unlink(self._current_progress_icon_path)
            except Exception:
                pass

    def _update_state(self):
        active, duration = get_active_tracking_info()
        
        if active != getattr(self, "_active_tags_cache", None):
            self._active_tags_cache = active
            self._build_menu(active_tags=active)
        else:
            # If the menu was not completely rebuilt, just regenerate and refresh the timeline view at the top!
            if getattr(self, "_timeline_item", None):
                try:
                    intervals = get_today_intervals()
                    img = draw_timeline(intervals)
                    image_view = self._timeline_item._menuitem.view()
                    if image_view:
                        image_view.setImage_(img)
                        image_view.setNeedsDisplay_(True)
                except Exception as e:
                    print(f"Error updating timeline: {e}")

        for tag, item in self._tag_items.items():
            item.state = tag in active
            if tag in active and duration:
                item.title = f"{tag} ({duration})"
            else:
                item.title = tag

        workday_hours = self._config.get("workday_hours", 7.5)
        
        # Calculate dynamic progress if workday target is set
        progress = None
        if workday_hours > 0.0:
            try:
                intervals = get_today_intervals()
                total_seconds = sum((inv["end"] - inv["start"]).total_seconds() for inv in intervals)
                progress = total_seconds / (workday_hours * 3600.0)
            except Exception as e:
                print(f"Error calculating progress: {e}")

        # Generate the appropriate icon path for this state
        icon_to_show = None
        if progress is not None:
            # Render icon with progress ring
            try:
                new_icon_path = _make_menubar_icon(progress)
                # Clean up the previous dynamic progress icon if one exists
                old_icon_path = getattr(self, "_current_progress_icon_path", None)
                if old_icon_path and os.path.exists(old_icon_path):
                    try:
                        os.unlink(old_icon_path)
                    except Exception:
                        pass
                self._current_progress_icon_path = new_icon_path
                icon_to_show = new_icon_path
            except Exception as e:
                print(f"Error drawing progress icon: {e}")
                icon_to_show = self._menubar_icon_path
        else:
            # No workday target: use the static standard icon (and clean up any existing dynamic progress icon)
            icon_to_show = self._menubar_icon_path
            old_icon_path = getattr(self, "_current_progress_icon_path", None)
            if old_icon_path and os.path.exists(old_icon_path):
                try:
                    os.unlink(old_icon_path)
                except Exception:
                    pass
            self._current_progress_icon_path = None

        if active:
            # Show the active tags as text next to the icon
            # Truncate to 20 chars to avoid overflowing the menu bar.
            title = ",".join(sorted(active))
            if len(title) > 20:
                title = title[:19] + "…"
            self.title = title
            
            if icon_to_show:
                self.icon = icon_to_show
                self.template = True
        else:
            # Idle: show only the logo, no text
            self.title = ""
            if icon_to_show:
                self.icon = icon_to_show
                self.template = True


if __name__ == "__main__":
    # Prevent the Python rocket icon from showing in the Dock when running directly
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    
    TimeBar().run()

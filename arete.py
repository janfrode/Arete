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
import importlib.util
from Foundation import NSDistributedNotificationCenter, NSObject, NSTimer, NSString
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSBitmapImageRep,
    NSGraphicsContext, NSImage, NSBezierPath, NSColor, NSRect, NSPoint,
    NSSize, NSPNGFileType, NSImageView, NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable, NSBackingStoreBuffered, NSUserInterfaceLayoutOrientationVertical,
    NSControlStateValueOn, NSControlStateValueOff, NSModalResponseOK,
    NSModalResponseCancel, NSStackView, NSTextField, NSButton, NSWindow,
    NSBox, NSGridView, NSGridCell, NSPanel, NSWindowStyleMaskBorderless,
    NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive, NSFontManager, NSView
)
from datetime import datetime, timezone, timedelta
import objc


CONFIG_PATH = os.path.expanduser("~/.arete.json")
VERSION = "1.0.8"


def _make_menubar_icon():
    """Render a 44×44 monochrome template icon (three bars) for the menu bar.

    Returns the path to a temp PNG, or None on failure.
    macOS template images must end in 'Template' — rumps handles that when
    template=True is passed, but we set NSImage.setTemplate_ directly instead.
    """
    import tempfile
    SIZE = 44   # points @2x → shown at 22 pt in the menu bar
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

    img.unlockFocus()
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
            "Show empty days in reports", self, None
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
        recent_val = self.app._config.get("recent_range", ":month")
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
            timeout=10,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


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


def get_today_intervals():
    """Return today's tracked intervals as list of dicts with local start/end times."""
    out = run("export", ":day")
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
    """Borderless splash panel shown briefly at startup."""

    DURATION = 1.5  # seconds before auto-dismiss

    QUOTE = (
        "\u201cWe are what we repeatedly do, therefore, "
        "excellence is not an act, but a habit.\u201d"
        "\n\u2014 Aristotle"
    )

    def show(self):
        PAD      = 28   # outer padding
        ICON     = 96   # icon size
        W        = 340
        QUOTE_H  = 100  # generous — ~3 wrapped lines at 12pt needs ~54 px
        SUB_H    = 18
        NAME_H   = 26
        H = PAD + ICON + 14 + NAME_H + 6 + SUB_H + 10 + QUOTE_H + PAD

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSRect(NSPoint(0, 0), NSSize(W, H)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False
        )
        panel.setReleasedWhenClosed_(False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(3)  # NSFloatingWindowLevel
        panel.center()

        # Frosted-glass background
        vfx = NSVisualEffectView.alloc().initWithFrame_(
            NSRect(NSPoint(0, 0), NSSize(W, H))
        )
        vfx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        vfx.setState_(NSVisualEffectStateActive)
        vfx.setWantsLayer_(True)
        vfx.layer().setCornerRadius_(16.0)
        vfx.layer().setMasksToBounds_(True)
        panel.setContentView_(vfx)

        italic_12 = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
            NSFont.systemFontOfSize_(12), 1
        )
        content_w = W - 2 * PAD

        # Layout top-down in flipped (screen) coordinates: y=0 at top of panel.
        # We place each element from the top, incrementing y downward.
        # AppKit view coords have y=0 at bottom, so each frame y = H - y_screen - height.
        y_screen = PAD   # screen-space cursor from top

        # Icon — centred horizontally at the top
        icon_path = get_icon_path()
        if icon_path:
            logo_img = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if logo_img:
                logo_img.setSize_(NSSize(ICON, ICON))
                icon_x = (W - ICON) / 2
                icon_y = H - y_screen - ICON   # convert to AppKit y-up
                logo_view = NSImageView.alloc().initWithFrame_(
                    NSRect(NSPoint(icon_x, icon_y), NSSize(ICON, ICON))
                )
                logo_view.setImage_(logo_img)
                logo_view.setImageScaling_(3)
                vfx.addSubview_(logo_view)
        y_screen += ICON + 14

        # App name
        name_y = H - y_screen - NAME_H
        lbl_name = NSTextField.labelWithString_("Arête")
        lbl_name.setFont_(NSFont.boldSystemFontOfSize_(20))
        lbl_name.setFrame_(NSRect(NSPoint(PAD, name_y), NSSize(content_w, NAME_H)))
        vfx.addSubview_(lbl_name)
        y_screen += NAME_H + 6

        # "Starting…" subtitle
        sub_y = H - y_screen - SUB_H
        lbl_sub = NSTextField.labelWithString_("Starting\u2026")
        lbl_sub.setFont_(NSFont.systemFontOfSize_(13))
        lbl_sub.setTextColor_(NSColor.secondaryLabelColor())
        lbl_sub.setFrame_(NSRect(NSPoint(PAD, sub_y), NSSize(content_w, SUB_H)))
        vfx.addSubview_(lbl_sub)
        y_screen += SUB_H + 10

        # Quote — wrapping NSTextField (not labelWithString which is single-line)
        quote_y = H - y_screen - QUOTE_H
        tf = NSTextField.alloc().initWithFrame_(
            NSRect(NSPoint(PAD, quote_y), NSSize(content_w, QUOTE_H))
        )
        tf.setStringValue_(self.QUOTE)
        tf.setFont_(italic_12)
        tf.setTextColor_(NSColor.secondaryLabelColor())
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
        tf.cell().setWraps_(True)
        tf.setMaximumNumberOfLines_(0)
        vfx.addSubview_(tf)

        self._panel = panel
        panel.makeKeyAndOrderFront_(None)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self.DURATION, self, "dismiss:", None, False
        )

    @objc.typedSelector(b"v@:@")
    def dismiss_(self, timer):
        self._panel.close()


class TimeBar(rumps.App):
    def __init__(self):
        super().__init__("", quit_button=None)
        _init_timewarrior()
        self._tag_items = {}
        self._active_tags_cache = set()
        self._config = load_config()
        self._locked_active_tags = set()
        # Pre-render the monochrome template icon for the idle menu bar state
        self._menubar_icon_path = _make_menubar_icon()
        if self._menubar_icon_path:
            self.icon = self._menubar_icon_path
            self.template = True

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
        
        # Promote any active tags to the main menu
        main_tags = sorted(list(set(recent_tags) | active_tags))
        older_tags = [t for t in all_tags if t not in main_tags]

        if not main_tags and not older_tags:
            self.menu.add(rumps.MenuItem("(no tags found)"))
        else:
            for tag in main_tags:
                item = rumps.MenuItem(tag, callback=self._toggle_tag)
                item.tag_name = tag
                self._tag_items[tag] = item
                self.menu.add(item)

            if older_tags:
                if main_tags:
                    self.menu.add(rumps.separator)
                
                older_menu = rumps.MenuItem("Older tags")
                for tag in older_tags:
                    item = rumps.MenuItem(tag, callback=self._toggle_tag)
                    item.tag_name = tag
                    self._tag_items[tag] = item
                    older_menu.add(item)
                self.menu.add(older_menu)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("New tag…", callback=self._new_tag))
        self.menu.add(rumps.MenuItem("Stop all", callback=self._stop_all))
        annotate_item = rumps.MenuItem("Annotate active…", callback=self._annotate_active)
        self._annotate_active_item = annotate_item
        self.menu.add(annotate_item)
        self.menu.add(rumps.MenuItem("Refresh tags", callback=self._refresh_tags))
        self.menu.add(rumps.MenuItem("Show Reports...", callback=self._show_reports))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Preferences...", callback=self._preferences))
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

    def _toggle_tag(self, sender):
        from AppKit import NSEvent, NSEventModifierFlagOption
        tag = getattr(sender, "tag_name", sender.title)
        if sender.state:
            active = get_active_tags()
            active.discard(tag)
            if active:
                run("start", *sorted(active))
                self._update_state()
            else:
                self._stop_with_optional_prompt()
        else:
            modifiers = NSEvent.modifierFlags()
            add_to_active = bool(modifiers & NSEventModifierFlagOption)
            start_tag(tag, add_to_active=add_to_active)
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
        if self._config.get("prompt_on_stop", False):
            if getattr(self, "_annotate_controller", None):
                return
            # @1 is now the interval that was just stopped
            existing = ""
            tags_hint = ""
            try:
                out = run("export", "@1")
                if out:
                    data = json.loads(out)
                    if data:
                        existing = data[0].get("annotation", "")
                        tags_hint = ", ".join(data[0].get("tags", []))
            except Exception:
                pass
            self._annotate_controller = AnnotateWindow.alloc(
            ).initWithApp_intervalId_existing_onSave_tagsHint_(
                self, 1, existing, None, tags_hint
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
                except Exception as e:
                    print(f"Error updating timeline: {e}")

        # Enable/disable "Annotate active…"
        annotate_item = getattr(self, "_annotate_active_item", None)
        if annotate_item:
            annotate_item._menuitem.setEnabled_(bool(active))

        for tag, item in self._tag_items.items():
            item.state = tag in active
            if tag in active and duration:
                item.title = f"{tag} ({duration})"
            else:
                item.title = tag

        if active:
            # Show the active tags as text; hide the icon so text stands alone
            self.icon = None
            self.title = ",".join(sorted(active))
        else:
            # Idle: show only the logo, no text
            self.title = ""
            if self._menubar_icon_path:
                self.icon = self._menubar_icon_path
                self.template = True


if __name__ == "__main__":
    # Prevent the Python rocket icon from showing in the Dock when running directly
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    
    TimeBar().run()

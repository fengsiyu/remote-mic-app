"""Command-line entry point for the SayAll Linux client."""

from __future__ import annotations

import argparse
import signal
import sys

from . import __version__
from .application import SayAllApplication


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="sayall-linux", description="SayAll Linux desktop client")
    parser.add_argument("--headless", action="store_true", help="run without the GTK window")
    parser.add_argument("--minimized", action="store_true", help="start in the status indicator when available")
    parser.add_argument("--verbose", action="store_true", help="echo sanitized runtime logs to stderr")
    parser.add_argument("--version", action="version", version="SayAll %s" % __version__)
    return parser.parse_args(argv)


def run_gui(args) -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError):
        print("sayall-linux requires python3-gi and gir1.2-gtk-3.0", file=sys.stderr)
        return 2
    app = SayAllApplication(headless=False, echo_logs=args.verbose)
    app.bind_glib(GLib)
    quitting = False

    def quit_app() -> None:
        nonlocal quitting
        if quitting:
            return
        quitting = True
        app.shutdown()
        Gtk.main_quit()

    window = None
    try:
        from .ui import SayAllWindow

        window = SayAllWindow(app, quit_app)
    except Exception as error:
        app.logger.write("APP UI phase=start result=failed error=%s" % type(error).__name__)
        print("Unable to start the SayAll window: %s" % type(error).__name__, file=sys.stderr)
        app.shutdown()
        return 2
    app.start()
    if not args.minimized or not window._tray_available:
        window.show()

    def quit_signal(*_args):
        quit_app()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_signal)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_signal)
    try:
        Gtk.main()
    finally:
        if not quitting:
            app.shutdown()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.headless:
        return SayAllApplication(headless=True, echo_logs=args.verbose).run_headless()
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())

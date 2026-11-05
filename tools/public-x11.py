#!/usr/bin/env python3
"""Real rendering proofs on a disposable, loopback-only X server."""
import contextlib
import os
import pathlib
import select
import signal
import subprocess
import sys
import time
from collections import Counter

from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest


@contextlib.contextmanager
def private_x11():
    """Yield a ready private DISPLAY, never connecting to an inherited display."""
    assert os.access('/var/lib/xkb', os.W_OK | os.X_OK), 'XKB output must not fall back to /tmp'
    read_fd, write_fd = os.pipe()
    with open('xvfb.log', 'wb') as log:
        server = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0',
                                   '800x600x24', '-nolock', '-nolisten', 'unix',
                                   '-nolisten', 'local', '-listen', 'tcp', '-ac'],
                                  pass_fds=(write_fd,), stdout=log, stderr=log,
                                  start_new_session=True)
        os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 10)
            assert ready, 'Xvfb readiness timed out'
            number = os.read(read_fd, 64).decode().strip()
            assert number.isdecimal() and server.poll() is None, 'Xvfb failed to start'
            name = '127.0.0.1:' + number
            connection = xdisplay.Display(name)
            connection.sync()
            connection.close()
            yield name
        finally:
            os.close(read_fd)
            stop_process(server)


def stop_process(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


@contextlib.contextmanager
def application(name, display_name, *args):
    home = pathlib.Path.cwd() / 'home'
    home.mkdir(exist_ok=True)
    env = dict(os.environ, DISPLAY=display_name, HOME=str(home), LC_ALL='C',
               XDG_CONFIG_HOME=str(home / '.config'), XDG_CACHE_HOME=str(home / '.cache'),
               DBUS_SESSION_BUS_ADDRESS='unix:path='+str(home / 'nonexistent-bus'),
               SDL_VIDEODRIVER='x11', GDK_BACKEND='x11', NO_AT_BRIDGE='1')
    env.pop('WAYLAND_DISPLAY', None)
    with open(name+'.log', 'wb') as log:
        process = subprocess.Popen([name, *args], env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
        try:
            yield process
        finally:
            stop_process(process)


def wait_for(check, process, description):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        assert process.poll() is None, (description, 'application exited', process.returncode)
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(description+' timed out')


def visible_windows(connection):
    for window in connection.screen().root.query_tree().children:
        if window.get_attributes().map_state == X.IsViewable:
            yield window


def visible_window(connection, title):
    for window in visible_windows(connection):
        name = window.get_wm_name() or ''
        if title.lower() in name.lower():
            return window
    return None


def pixels(connection, window, evidence_name):
    geometry = window.get_geometry()
    image = window.get_image(0, 0, geometry.width, geometry.height, X.ZPixmap, 0xffffffff)
    assert image.depth == 24 and connection.display.info.image_byte_order == X.LSBFirst
    # The approved Xvfb screen has depth 24 and 32-bit little-endian storage.
    raw = image.data
    assert len(raw) == geometry.width * geometry.height * 4
    rgb = bytes(channel for offset in range(0, len(raw), 4)
                for channel in (raw[offset+2], raw[offset+1], raw[offset]))
    pathlib.Path(evidence_name+'.ppm').write_bytes(
        f'P6\n{geometry.width} {geometry.height}\n255\n'.encode()+rgb)
    counts = Counter(tuple(rgb[offset:offset+3]) for offset in range(0, len(rgb), 3))
    return counts, geometry.width * geometry.height


def key(connection, window, symbol, *modifiers):
    window.set_input_focus(X.RevertToParent, X.CurrentTime)
    codes = [connection.keysym_to_keycode(XK.string_to_keysym(name))
             for name in (*modifiers, symbol)]
    assert all(codes)
    for code in codes:
        xtest.fake_input(connection, X.KeyPress, code)
    for code in reversed(codes):
        xtest.fake_input(connection, X.KeyRelease, code)
    connection.sync()


def click(connection, button):
    xtest.fake_input(connection, X.ButtonPress, button)
    xtest.fake_input(connection, X.ButtonRelease, button)
    connection.sync()


def prove_lcdtest(display_name):
    connection = xdisplay.Display(display_name)
    try:
        with application('lcdtest', display_name, '--window', '-x', '640', '-y', '480') as process:
            window = wait_for(lambda: visible_window(connection, ''), process, 'lcdtest window')
            key(connection, window, 's')
            key(connection, window, 'r')
            def solid(color, label):
                counts, size = pixels(connection, window, label)
                return counts[color] == size
            wait_for(lambda: solid((255, 0, 0), 'lcdtest-red'), process, 'solid red render')
            key(connection, window, 'g')
            wait_for(lambda: solid((0, 255, 0), 'lcdtest-green'), process, 'solid green render')
            key(connection, window, 'h')
            def lines():
                counts, size = pixels(connection, window, 'lcdtest-horizontal')
                return counts[(0, 255, 0)] == size // 32 and counts[(0, 0, 0)] == size-size//32
            wait_for(lines, process, '32-pixel horizontal green line pattern')
            key(connection, window, 'q')
            assert process.wait(timeout=3) == 0
            print('lcdtest: red, green, exact horizontal line pixels; keyboard quit')
    finally:
        connection.close()


def prove_screentest(display_name):
    connection = xdisplay.Display(display_name)
    try:
        with application('screentest', display_name) as process:
            window = wait_for(lambda: visible_window(connection, 'Screen test'), process, 'screentest window')
            window.configure(width=800, height=600)
            connection.sync()
            def rendered():
                counts, size = pixels(connection, window, 'screentest-basic')
                # Actual color bars and background, not a blank or error-dialog window.
                return (all(counts[c] > 100 for c in [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
                        and counts[(0, 0, 0)] > size//4)
            wait_for(rendered, process, 'basic RGB test-pattern rendering')
            before, _ = pixels(connection, window, 'screentest-before-color-cycle')
            window.warp_pointer(20, 20)
            click(connection, 2)
            def changed():
                after, _ = pixels(connection, window, 'screentest-after-color-cycle')
                return after != before and after[(255, 255, 255)] < before[(255, 255, 255)]
            wait_for(changed, process, 'middle-button foreground color cycling')
            # GtkBuilder registers this accelerator on the popup's own window.
            type_property = connection.intern_atom('_NET_WM_WINDOW_TYPE')
            popup_type = connection.intern_atom('_NET_WM_WINDOW_TYPE_POPUP_MENU')
            def popup_window():
                for candidate in visible_windows(connection):
                    types = candidate.get_full_property(type_property, X.AnyPropertyType)
                    if types is not None and popup_type in types.value:
                        return candidate
            click(connection, 3)
            popup = wait_for(popup_window, process, 'right-click popup menu')
            pixels(connection, connection.screen().root, 'screentest-quit-menu')
            key(connection, popup, 'q', 'Control_L')
            assert process.wait(timeout=3) == 0
            print('screentest: RGB pattern, real middle-button color cycle, Ctrl-Q popup-menu exit')
    finally:
        connection.close()


def prove_caffeinate(display_name):
    """Observe real XTest pointer motion on the caller's disposable X server."""
    connection = xdisplay.Display(display_name)
    process = None
    try:
        screen = connection.screen()
        root = screen.root
        origin = (screen.width_in_pixels // 2, screen.height_in_pixels // 2)
        root.change_attributes(event_mask=X.PointerMotionMask)
        root.warp_pointer(*origin)
        connection.sync()
        while connection.pending_events():
            connection.next_event()
        process = subprocess.Popen(['caffeinate'], env=dict(os.environ, DISPLAY=display_name),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        expected = [(origin[0] + 1, origin[1] + 1), origin]
        actual = []
        deadline = time.monotonic() + 5
        while len(actual) < len(expected):
            remaining = deadline - time.monotonic()
            assert remaining > 0, ('missing caffeinate pointer events', actual)
            if not connection.pending_events():
                assert select.select([connection.fileno()], [], [], remaining)[0], actual
            event = connection.next_event()
            if event.type == X.MotionNotify:
                actual.append((event.root_x, event.root_y))
        assert actual == expected, (actual, expected)
        assert process.poll() is None, 'caffeinate exited before SIGINT'
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0 and not stdout and not stderr, (process.returncode, stdout, stderr)
        final = root.query_pointer()
        assert (final.root_x, final.root_y) == origin
        print('caffeinate: real +1/+1 then -1/-1 pointer motion; clean SIGINT exit')
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        connection.close()


if __name__ == '__main__':
    proofs = {'lcdtest': prove_lcdtest, 'screentest-git': prove_screentest,
              'caffeinate': prove_caffeinate}
    if len(sys.argv) != 2 or sys.argv[1] not in proofs:
        raise SystemExit('expected an enrolled X11 proof name')
    with private_x11() as private_display:
        proofs[sys.argv[1]](private_display)

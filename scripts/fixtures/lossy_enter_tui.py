"""A stand-in for an agent CLI's composer that LOSES the first N Enter presses.

Test fixture for spawn_guards.inject_prompt (scripts/test_spawn_guards.py). It draws a transcript
and a Claude-style composer line ("❯ <text>"); Enter moves the composer text into the transcript,
except that the first DROP_ENTERS presses are ignored and the text stays unsent in the box, which is
what a new user's first gm showed (operator report, 2026-10-09).

    python3 lossy_enter_tui.py <drop_enters>
"""
import os
import sys
import termios
import tty

drop = int(sys.argv[1]) if len(sys.argv) > 1 else 0
transcript = ["Welcome to the fake CLI"]
buf = ""


def draw():
    out = "\x1b[2J\x1b[H" + "\r\n".join(transcript) + "\r\n\r\n\x1b[39m❯\xa0" + buf + "\r\n  ? for shortcuts"
    os.write(1, out.encode())


fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    draw()
    while True:
        ch = os.read(fd, 1)
        if not ch or ch == b"\x03":
            break
        if ch in (b"\r", b"\n"):
            if drop > 0:
                drop -= 1
            elif buf:
                transcript.append("❯ " + buf)
                transcript.append("● working on it")
                buf = ""
        elif ch == b"\x7f":
            buf = buf[:-1]
        else:
            buf += ch.decode("utf-8", "replace")
        draw()
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)

#!/usr/bin/env python3
"""Drive the Alpine qemu guest over its serial console (expect-like).

    vmrun.py CMDFILE [--sock PATH] [--log PATH] [--boot-timeout N] [--keep]

Every line of CMDFILE is a shell command; the output of each command is
printed.  The guest logs in as root (the live ISO has no root password).

The guest boots the Alpine "lts" kernel, the only Alpine flavour that ships
CONFIG_HFSPLUS_FS=m - the build needs it to mount the HFS+ volume.
"""
import argparse
import re
import socket
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("cmdfile")
ap.add_argument("--sock", required=True)
ap.add_argument("--log", required=True)
ap.add_argument("--prompt", default=r"localhost:~#")
ap.add_argument("--boot-timeout", type=float, default=900)
ap.add_argument("--keep", action="store_true", help="do not power the guest off at the end")
args = ap.parse_args()

sock = args.sock

deadline = time.time() + args.boot_timeout
s = None
while time.time() < deadline:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock)
        break
    except OSError:
        if s:
            s.close()
        s = None
        time.sleep(0.5)
if s is None:
    sys.exit("cannot connect to the qemu serial socket %s" % sock)

buf = b""
log = open(args.log, "wb")


def read_until(pattern, timeout):
    global buf
    rx = re.compile(pattern.encode())
    end = time.time() + timeout
    while True:
        m = rx.search(buf)
        if m:
            out = buf[: m.end()]
            buf = buf[m.end():]
            return out
        if time.time() > end:
            out, buf = buf, b""
            return out
        s.settimeout(1.0)
        try:
            d = s.recv(65536)
            if not d:
                break
            buf += d
            log.write(d)
            log.flush()
        except socket.timeout:
            pass
        except OSError:
            break
    return buf


def send(text):
    s.sendall(text.encode())


def login():
    """Log in as root.

    The guest may be at the login prompt (fresh boot) or already at a shell
    (reconnect), so probe with a newline first and then follow whatever
    prompt shows up.
    """
    end = time.time() + args.boot_timeout
    send("\n")
    while time.time() < end:
        out = read_until(r"(login:|Password:|localhost:~#)", 60)
        txt = out.decode("utf-8", "replace")
        if "localhost:~#" in txt:
            return True
        if "Password:" in txt:
            send("\n")
            continue
        if "login:" in txt:
            send("root\n")
            continue
    return False


if not login():
    sys.exit("could not log in to the guest")

with open(args.cmdfile) as f:
    cmds = [l.rstrip("\n") for l in f if l.strip() and not l.startswith("#")]

for c in cmds:
    sys.stdout.write("\n$ %s\n" % c)
    sys.stdout.flush()
    send(c + "\n")
    out = read_until(args.prompt, 3600)
    text = out.decode("utf-8", "replace")
    text = text.replace(c + "\r\n", "", 1).replace(c + "\n", "", 1)
    sys.stdout.write(text)
    sys.stdout.flush()

if not args.keep:
    send("poweroff\n")
    time.sleep(5)
log.close()

#!/usr/bin/env python3
import curses
import curses.ascii
import os
import pwd
import grp
import shutil
import stat
import subprocess
import mimetypes
import json
import time
import tarfile
import zipfile
import math
import select

APP_NAME = "Server Manager"
APP_VERSION = "0.6"
MIN_H = 24
MIN_W = 90
STATE_FILE = os.path.expanduser("~/.server_manager_state.json")
TRASH_DIR = os.path.expanduser("~/.server_manager_trash")
BOOKMARKS = ["/home", "/root", "/etc", "/var/log", "/usr/local", "/tmp"]


def run(cmd):
    p = subprocess.run(
        cmd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return p.returncode, p.stdout


def run_list(cmd_list):
    try:
        p = subprocess.run(
            cmd_list,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return p.returncode, p.stdout
    except Exception as e:
        return 1, str(e)



def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def wrap_text(text, width):
    width = max(1, width)
    out = []
    for raw in (str(text).splitlines() or [""]):
        if raw == "":
            out.append("")
            continue
        while len(raw) > width:
            out.append(raw[:width])
            raw = raw[width:]
        out.append(raw)
    return out


def human_size(num):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return str(int(value)) + " " + unit
            return "{:.1f} {}".format(value, unit)
        value /= 1024
    return str(num) + " B"

def human_rate(num):
    return human_size(num) + "/s"


def sparkline(values, width=20, vmin=0.0, vmax=None):
    chars = " .:-=+*#%@"
    if width <= 0:
        return ""
    if not values:
        return " " * width

    vals = list(values)[-width:]
    if len(vals) < width:
        vals = ([vals[0]] * (width - len(vals))) + vals

    if vmax is None:
        vmax = max(max(vals), 1.0)
    if vmax <= vmin:
        vmax = vmin + 1.0

    out = []
    steps = len(chars) - 1
    for v in vals:
        try:
            x = float(v)
        except Exception:
            x = 0.0
        x = max(vmin, min(vmax, x))
        ratio = (x - vmin) / (vmax - vmin)
        idx = int(round(ratio * steps))
        idx = max(0, min(steps, idx))
        out.append(chars[idx])
    return "".join(out)

def ascii_bar(label, pct, width=24):
    try:
        pct = float(pct)
    except Exception:
        pct = 0.0
    pct = max(0.0, min(100.0, pct))
    fill = int((pct / 100.0) * width)
    return "{:<8} [{}{}] {:5.1f}%".format(label, "#" * fill, "-" * (width - fill), pct)


def safe_addstr(win, y, x, text, attr=0):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        max_len = max(0, w - x - 1)
        if max_len <= 0:
            return
        win.addstr(y, x, str(text)[:max_len], attr)
    except Exception:
        pass


def safe_addch(win, y, x, ch, attr=0):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        win.addch(y, x, ch, attr)
    except Exception:
        pass


def center_window(stdscr, height, width):
    h, w = stdscr.getmaxyx()
    height = min(height, max(4, h - 2))
    width = min(width, max(24, w - 2))
    y = max(1, (h - height) // 2)
    x = max(1, (w - width) // 2)
    return curses.newwin(height, width, y, x)


def cpair(n):
    try:
        return curses.color_pair(n)
    except Exception:
        return 0


def init_colors():
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
    except Exception:
        pass
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_GREEN, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(8, curses.COLOR_YELLOW, -1)
    curses.init_pair(9, curses.COLOR_WHITE, -1)
    curses.init_pair(10, curses.COLOR_RED, -1)
    curses.init_pair(11, curses.COLOR_GREEN, curses.COLOR_BLACK)


def is_text_file(path):
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("text/"):
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        return b"\x00" not in chunk
    except Exception:
        return False


def can_extract(path):
    lower = path.lower()
    return (
        lower.endswith(".zip")
        or lower.endswith(".tar")
        or lower.endswith(".tar.gz")
        or lower.endswith(".tgz")
        or lower.endswith(".tar.bz2")
        or lower.endswith(".tbz2")
    )


def can_archive(path):
    return os.path.exists(path)


def file_label(path):
    try:
        if os.path.isdir(path):
            return "[DIR]", cpair(4)
        if os.path.islink(path):
            return "[LNK]", cpair(6)
        if os.access(path, os.X_OK):
            return "[EXE]", cpair(5)
        return "[FIL]", cpair(9)
    except Exception:
        return "[???]", cpair(9)


def get_owner_group(path):
    try:
        st = os.stat(path)
        return pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name
    except Exception:
        return "?", "?"


def read_preview(path, max_lines=6):
    lines = []
    try:
        st = os.stat(path)
        owner, group = get_owner_group(path)
        lines.append("Path : " + path)
        lines.append("Type : " + file_label(path)[0])
        lines.append("Perm : " + stat.filemode(st.st_mode))
        lines.append("Owner: " + owner + ":" + group)
        lines.append("Size : " + human_size(st.st_size))
        lines.append("")
        if os.path.isdir(path):
            try:
                lines.append("Items: " + str(len(os.listdir(path))))
            except Exception:
                lines.append("Items: ?")
        elif is_text_file(path):
            lines.append("Preview:")
            lines.append("--------")
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for _ in range(max_lines):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line.rstrip("\n"))
        else:
            lines.append("No text preview available")
    except Exception as e:
        lines.append("Error: " + str(e))
    return lines


class PlatformProfile:
    def __init__(self):
        self.name = "Unknown Linux"
        self.family = "unknown"
        self.auth_log = "/var/log/auth.log"
        self.syslog = "/var/log/syslog"
        self.ssh_service = "ssh"
        self.pkg_update = None
        self.pkg_upgrade = None
        self.pkg_list_upgrades = None
        self.firewall_tool = None
        self.detect()

    def detect(self):
        data = {}
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k] = v.strip().strip('"')
        except Exception:
            pass

        os_id = data.get("ID", "unknown").lower()
        like = data.get("ID_LIKE", "").lower()
        self.name = data.get("PRETTY_NAME", os_id)

        if os_id in {"debian", "ubuntu", "linuxmint"} or "debian" in like:
            self.family = "debian"
            self.auth_log = "/var/log/auth.log"
            self.syslog = "/var/log/syslog"
            self.ssh_service = "ssh"
            self.pkg_update = "apt update"
            self.pkg_upgrade = "apt upgrade -y"
            self.pkg_list_upgrades = "apt list --upgradable 2>/dev/null"
        elif os_id in {"rhel", "centos", "rocky", "almalinux", "fedora"} or any(
            x in like for x in ["rhel", "fedora", "centos"]
        ):
            self.family = "rhel"
            self.auth_log = "/var/log/secure"
            self.syslog = "/var/log/messages"
            self.ssh_service = "sshd"
            pkg = "dnf" if shutil.which("dnf") else "yum"
            self.pkg_update = pkg + " makecache"
            self.pkg_upgrade = pkg + " upgrade -y"
            self.pkg_list_upgrades = pkg + " check-update || true"

        if shutil.which("ufw"):
            self.firewall_tool = "ufw"
        elif shutil.which("firewall-cmd"):
            self.firewall_tool = "firewalld"


class UI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.status = ""

    def set_status(self, text):
        self.status = text

    def draw_too_small(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        msgs = [
            "Terminal too small",
            "Resize to at least {}x{}".format(MIN_W, MIN_H),
            "Press Q or F10 to quit",
        ]
        for i, msg in enumerate(msgs):
            safe_addstr(
                self.stdscr,
                max(0, h // 2 - 1 + i),
                max(0, (w - len(msg)) // 2),
                msg,
                curses.A_BOLD if i == 0 else 0,
            )
        self.stdscr.refresh()

    def message(self, text, pause=1.0, title="Message"):
        lines = wrap_text(text, 70)
        h, w = self.stdscr.getmaxyx()
        win_h = min(len(lines) + 4, max(6, h - 2))
        win_w = min(max(max(len(x) for x in lines) + 4, len(title) + 6, 30), max(24, w - 2))
        win = center_window(self.stdscr, win_h, win_w)
        win.bkgd(" ", cpair(7))
        win.box()
        safe_addstr(win, 0, 2, " " + title + " ", cpair(7) | curses.A_BOLD)
        for i, line in enumerate(lines[: win_h - 2], start=1):
            safe_addstr(win, i, 2, line)
        win.refresh()
        time.sleep(pause)

    def confirm(self, text, title="Confirm"):
        lines = wrap_text(text, 78) + ["", "[Y]es   [N]o   [Esc/Left] Cancel"]
        h, w = self.stdscr.getmaxyx()
        win_h = min(len(lines) + 4, max(6, h - 2))
        win_w = min(max(max(len(x) for x in lines) + 4, 42), max(24, w - 2))
        win = center_window(self.stdscr, win_h, win_w)
        win.bkgd(" ", cpair(7))
        win.keypad(True)
        win.box()
        safe_addstr(win, 0, 2, " " + title + " ", cpair(7) | curses.A_BOLD)
        for i, line in enumerate(lines[: win_h - 2], start=1):
            safe_addstr(win, i, 2, line, cpair(8) if "Esc" in line else cpair(7))
        win.refresh()
        while True:
            ch = win.getch()
            if ch in (ord("y"), ord("Y"), curses.KEY_RIGHT, 10, 13):
                return True
            if ch in (ord("n"), ord("N"), curses.KEY_LEFT, 27):
                return False

    def prompt(self, title, default=""):
        h, w = self.stdscr.getmaxyx()
        win_w = min(max(72, len(title) + 10), max(24, w - 2))
        win_h = 8
        win = center_window(self.stdscr, win_h, win_w)
        win.bkgd(" ", cpair(7))
        win.keypad(True)
        win.box()
        safe_addstr(win, 0, 2, " " + title + " ", cpair(7) | curses.A_BOLD)
        safe_addstr(win, 1, 2, "Enter/Right = OK   Esc/Left = Cancel", cpair(8))

        field_y = 3
        field_x = 2
        field_w = max(1, win_w - 4)
        text = list(default)
        cursor = 0
        replace_mode = True

        while True:
            display = "".join(text)
            safe_addstr(win, field_y - 1, 2, "Input:")
            safe_addstr(
                win,
                field_y,
                field_x,
                display[:field_w].ljust(field_w),
                cpair(9) | curses.A_REVERSE,
            )
            try:
                win.move(field_y, min(field_x + cursor, field_x + field_w - 1))
            except Exception:
                pass
            win.refresh()
            ch = win.getch()

            if ch in (27, curses.KEY_LEFT):
                return None
            if ch in (10, 13, curses.KEY_RIGHT):
                return "".join(text).strip()
            if ch == curses.KEY_HOME:
                cursor = 0
                replace_mode = False
            elif ch == curses.KEY_END:
                cursor = len(text)
                replace_mode = False
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    del text[cursor - 1]
                    cursor -= 1
                replace_mode = False
            elif ch == curses.KEY_DC:
                if cursor < len(text):
                    del text[cursor]
                replace_mode = False
            elif ch == curses.KEY_LEFT:
                if cursor > 0:
                    cursor -= 1
                replace_mode = False
            elif ch == curses.KEY_RIGHT:
                if cursor < len(text):
                    cursor += 1
                replace_mode = False
            elif curses.ascii.isprint(ch):
                c = chr(ch)
                if replace_mode:
                    text = [c]
                    cursor = 1
                    replace_mode = False
                else:
                    text.insert(cursor, c)
                    cursor += 1

    def view_text(self, title, content, start_pos=0):
        lines = content if isinstance(content, list) else (str(content).splitlines() or [""])
        pos = max(0, start_pos)
        while True:
            self.stdscr.erase()
            h, w = self.stdscr.getmaxyx()
            safe_addstr(self.stdscr, 0, 0, title.ljust(w - 1), cpair(1) | curses.A_BOLD)
            visible = max(1, h - 2)
            for i in range(visible - 1):
                idx = pos + i
                if idx >= len(lines):
                    break
                safe_addstr(self.stdscr, i + 1, 0, "{:04d} {}".format(idx + 1, lines[idx]))
            safe_addstr(
                self.stdscr,
                h - 1,
                0,
                "Up/Down line | PgUp/PgDn/Right page | Left/Q/Esc back | Home/End".ljust(w - 1),
                cpair(2),
            )
            self.stdscr.noutrefresh()
            curses.doupdate()
            ch = self.stdscr.getch()
            page = max(1, visible - 2)
            if ch == curses.KEY_UP and pos > 0:
                pos -= 1
            elif ch == curses.KEY_DOWN and pos < max(0, len(lines) - 1):
                pos += 1
            elif ch in (curses.KEY_NPAGE, curses.KEY_RIGHT):
                pos = min(max(0, len(lines) - 1), pos + page)
            elif ch == curses.KEY_PPAGE:
                pos = max(0, pos - page)
            elif ch == curses.KEY_HOME:
                pos = 0
            elif ch == curses.KEY_END:
                pos = max(0, len(lines) - 1)
            elif ch in (curses.KEY_LEFT, ord("q"), ord("Q"), 27):
                return pos

    def view_file_at_line(self, path, line_no=1, title_prefix="View"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception as e:
            self.message("Error: " + str(e), 1.2, "Error")
            return

        start_pos = max(0, int(line_no) - 3)
        self.view_text("{}: {} @ line {}".format(title_prefix, path, line_no), lines, start_pos)

class ListBrowser:
    def __init__(self, ui, title, lines, default_index=0, default_top=0):
        self.ui = ui
        self.title = title
        self.lines = lines
        self.selected = default_index
        self.top = default_top
        self.filter_text = ""

    def run(self):
        while True:
            stdscr = self.ui.stdscr
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            visible = max(1, h - 2)

            if self.filter_text:
                q = self.filter_text.lower()
                filtered = [(i, s) for i, s in enumerate(self.lines) if q in s.lower()]
            else:
                filtered = list(enumerate(self.lines))

            if not filtered:
                filtered = [(-1, "<no results>")]

            self.selected = min(self.selected, max(0, len(filtered) - 1))
            if self.selected < self.top:
                self.top = self.selected
            elif self.selected >= self.top + visible - 1:
                self.top = self.selected - visible + 2

            title = self.title + ((" | filter: " + self.filter_text) if self.filter_text else "")
            safe_addstr(stdscr, 0, 0, title.ljust(w - 1), cpair(1) | curses.A_BOLD)

            for row in range(1, visible):
                idx = self.top + row - 1
                if idx >= len(filtered):
                    break
                original_index, text = filtered[idx]
                prefix = "----" if original_index < 0 else "{:04d}".format(idx + 1)
                attr = (cpair(3) | curses.A_BOLD) if idx == self.selected else cpair(9)
                safe_addstr(stdscr, row, 1, prefix + " " + text, attr)

            safe_addstr(
                stdscr,
                h - 1,
                0,
                "Up/Down | Right/Enter select | Left/Q/Esc back | / filter | Home/End | PgUp/PgDn".ljust(w - 1),
                cpair(2),
            )
            stdscr.noutrefresh()
            curses.doupdate()

            ch = stdscr.getch()
            page = max(1, visible - 2)
            if ch == curses.KEY_UP and self.selected > 0:
                self.selected -= 1
            elif ch == curses.KEY_DOWN and self.selected < len(filtered) - 1:
                self.selected += 1
            elif ch == curses.KEY_PPAGE:
                self.selected = max(0, self.selected - page)
            elif ch == curses.KEY_NPAGE:
                self.selected = min(len(filtered) - 1, self.selected + page)
            elif ch == curses.KEY_HOME:
                self.selected = 0
            elif ch == curses.KEY_END:
                self.selected = max(0, len(filtered) - 1)
            elif ch == ord("/"):
                value = self.ui.prompt("Filter list", self.filter_text)
                if value is not None:
                    self.filter_text = value.strip()
                    self.selected = 0
                    self.top = 0
            elif ch in (10, 13, curses.KEY_RIGHT):
                original_index, _ = filtered[self.selected]
                if original_index >= 0:
                    return {
                        "index": original_index,
                        "selected": self.selected,
                        "top": self.top,
                    }
            elif ch in (curses.KEY_LEFT, ord("q"), ord("Q"), 27):
                return None


class ServerManager:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.ui = UI(stdscr)
        self.profile = PlatformProfile()
        self.current_dir = "/home" if os.path.isdir("/home") else os.path.expanduser("~")
        self.selected = 0
        self.top = 0
        self.clipboard_mode = None
        self.clipboard_source = None
        self.menu_state = {}
        self.last_metrics_time = 0.0
        self.prev_cpu_total = None
        self.prev_cpu_idle = None
        self.prev_disk = None
        self.prev_net = None

        self.cpu_history = []
        self.ram_history = []
        self.disk_history = []
        self.net_history = []

        self.live_metrics = {
            "cpu_pct": 0.0,
            "ram_pct": 0.0,
            "ram_used": 0,
            "ram_total": 0,
            "swap_pct": 0.0,
            "disk_pct": 0.0,
            "disk_used": 0,
            "disk_total": 0,
            "disk_read_bps": 0.0,
            "disk_write_bps": 0.0,
            "net_rx_bps": 0.0,
            "net_tx_bps": 0.0,
        }

        state = load_json(STATE_FILE, {})
        path = state.get("current_dir")
        if isinstance(path, str) and os.path.isdir(path):
            self.current_dir = path

        nav = state.get("nav", {})
        here = nav.get(self.current_dir, {}) if isinstance(nav, dict) else {}
        if isinstance(here, dict):
            self.selected = int(here.get("selected", 0))
            self.top = int(here.get("top", 0))

        os.makedirs(TRASH_DIR, exist_ok=True)

    def save_state(self):
        state = load_json(STATE_FILE, {})
        state["current_dir"] = self.current_dir
        nav = state.get("nav", {})
        nav[self.current_dir] = {"selected": self.selected, "top": self.top}
        state["nav"] = nav
        save_json(STATE_FILE, state)

    def is_root(self):
        try:
            return os.geteuid() == 0
        except Exception:
            return False

    def ensure_root_for_action(self, action_text):
        if self.is_root():
            return True

        msg = (
            "This action may require root privileges.\n\n"
            "Action: " + action_text + "\n\n"
            "Current user is not root.\n"
            "You can continue and see if it works,\n"
            "or cancel and relaunch with sudo."
        )
        return self.ui.confirm(msg, "Root required?")

    def save_menu_state(self, key, selected, top):
        self.menu_state[key] = {"selected": selected, "top": top}

    def get_menu_state(self, key):
        return self.menu_state.get(key, {"selected": 0, "top": 0})

    def browse_menu(self, key, title, lines, default_index=0):
        st = self.get_menu_state(key)
        browser = ListBrowser(
            self.ui,
            title,
            lines,
            default_index=st.get("selected", default_index),
            default_top=st.get("top", 0),
        )
        res = browser.run()
        if res is None:
            return None
        self.save_menu_state(key, res["selected"], res["top"])
        return res["index"]

    def list_dir(self, path):
        try:
            entries = os.listdir(path)
        except Exception:
            return []
        dirs = []
        files = []
        for x in entries:
            full = os.path.join(path, x)
            if os.path.isdir(full):
                dirs.append(x)
            else:
                files.append(x)
        return sorted(dirs, key=str.lower) + sorted(files, key=str.lower)

    def get_items(self):
        return [".."] + self.list_dir(self.current_dir)

    def selected_item(self):
        items = self.get_items()
        self.selected = min(self.selected, max(0, len(items) - 1))
        name = items[self.selected]
        path = self.current_dir if name == ".." else os.path.join(self.current_dir, name)
        return name, path

    def draw_main(self):
        h, w = self.stdscr.getmaxyx()
        if h < MIN_H or w < MIN_W:
            self.ui.draw_too_small()
            return

        left_w = int(w * 0.70)
        if left_w < 58:
            left_w = 58
        if left_w > w - 22:
            left_w = w - 22
        right_x = left_w + 1
        right_w = w - right_x - 1

        self.stdscr.erase()
        header = " {} {} | {} | path: {} ".format(APP_NAME, APP_VERSION, self.profile.name, self.current_dir)
        safe_addstr(self.stdscr, 0, 0, header.ljust(w - 1), cpair(1) | curses.A_BOLD)

        for y in range(1, h - 1):
            safe_addch(self.stdscr, y, left_w, curses.ACS_VLINE)

        items = self.get_items()
        visible_rows = h - 3

        if self.selected >= len(items):
            self.selected = max(0, len(items) - 1)
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + visible_rows:
            self.top = self.selected - visible_rows + 1

        for row in range(visible_rows):
            idx = self.top + row
            if idx >= len(items):
                break
            name = items[idx]
            if name == "..":
                label, color = "[UP ]", cpair(8)
            else:
                label, color = file_label(os.path.join(self.current_dir, name))
            attr = (cpair(3) | curses.A_BOLD) if idx == self.selected else color
            safe_addstr(
                self.stdscr,
                row + 1,
                1,
                "{:04d} {} {}".format(idx + 1, label, name).ljust(left_w - 2),
                attr,
            )

        selected_name, selected_path = self.selected_item()
        safe_addstr(self.stdscr, 1, right_x + 1, " Info / Keys ", curses.A_BOLD)

        panel = read_preview(selected_path, 4)
        panel += [
            "",
            "Keys",
            "----",
            "Up/Down Navigate",
            "Left    Parent",
            "Right   Open",
            "Enter   Actions",
            "F1      Help",
            "F2      Rename",
            "F3      View",
            "F4      Edit",
            "F5/F6   Copy/Move",
            "F7      New dir",
            "F8      Delete",
            "F9      Admin",
            "P       Paste",
            "M       Permissions menu",
            "S       Search text in files",
            "F10/Q   Quit",
            "",
            "Clipboard",
            "---------",
        ]

        if self.clipboard_mode and self.clipboard_source:
            panel += [
                "Mode: " + self.clipboard_mode,
                "Item: " + os.path.basename(self.clipboard_source),
            ]
        else:
            panel += ["Empty"]

        info_top_y = 2

        out = []
        for line in panel:
            out.extend(wrap_text(line, max(10, right_w - 2)))

        desired_live_top = info_top_y + len(out) + 1
        min_live_top = max(16, h // 2)
        live_top_y = max(min_live_top, desired_live_top)

        if live_top_y > h - 10:
            live_top_y = h - 10

        info_rows = max(1, live_top_y - info_top_y - 1)
        for i, line in enumerate(out[:info_rows], start=info_top_y):
            safe_addstr(self.stdscr, i, right_x + 1, line)

        safe_addstr(
            self.stdscr,
            live_top_y,
            right_x + 1,
            "-" * max(1, right_w - 2),
            cpair(2),
        )

        live_height = max(8, h - live_top_y - 5)
        self.draw_live_panel(
            live_top_y + 1,
            right_x + 1,
            max(10, right_w - 2),
            live_height,
            " Live Resources ",
        )

        safe_addstr(self.stdscr, h - 3, right_x + 1, "terminalnotes.com", cpair(8))

        footer = " ← parent | → open | Enter actions | PgUp/PgDn | Home/End | / find | F9 admin | F10 quit "
        safe_addstr(self.stdscr, h - 1, 0, footer.ljust(w - 1), cpair(2))

        if self.ui.status:
            status = self.ui.status
        else:
            if self.clipboard_mode and self.clipboard_source:
                status = "Clipboard: " + self.clipboard_mode + " -> " + os.path.basename(self.clipboard_source)
            else:
                status = "Clipboard: empty"
        safe_addstr(self.stdscr, h - 2, 0, status.ljust(w - 1), cpair(11))

        self.stdscr.noutrefresh()
        curses.doupdate()

    def go_parent(self):
        old_dir = self.current_dir
        parent = os.path.dirname(self.current_dir.rstrip("/")) or "/"

        state = load_json(STATE_FILE, {})
        nav = state.get("nav", {})
        nav[old_dir] = {"selected": self.selected, "top": self.top}
        state["nav"] = nav
        save_json(STATE_FILE, state)

        self.current_dir = parent
        parent_items = [".."] + self.list_dir(parent)
        base = os.path.basename(old_dir.rstrip("/"))
        target_index = parent_items.index(base) if base in parent_items else 0

        here = nav.get(parent, {}) if isinstance(nav, dict) else {}
        if isinstance(here, dict):
            self.selected = int(here.get("selected", target_index))
            self.top = int(here.get("top", 0))
            self.selected = target_index if base in parent_items else self.selected
        else:
            self.selected = target_index
            self.top = 0
        self.save_state()

    def enter_selected(self):
        name, path = self.selected_item()
        if name == "..":
            self.go_parent()
        elif os.path.isdir(path):
            state = load_json(STATE_FILE, {})
            nav = state.get("nav", {})
            nav[self.current_dir] = {"selected": self.selected, "top": self.top}
            state["nav"] = nav
            save_json(STATE_FILE, state)

            self.current_dir = path
            here = nav.get(self.current_dir, {}) if isinstance(nav, dict) else {}
            if isinstance(here, dict):
                self.selected = int(here.get("selected", 0))
                self.top = int(here.get("top", 0))
            else:
                self.selected = 0
                self.top = 0
            self.save_state()

    def create_folder(self):
        name = self.ui.prompt("New directory name")
        if not name:
            return
        try:
            os.makedirs(os.path.join(self.current_dir, name), exist_ok=False)
            self.ui.set_status("Directory created")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def rename_selected(self):
        name, path = self.selected_item()
        if name == "..":
            return
        new_name = self.ui.prompt("Rename to", name)
        if not new_name or new_name == name:
            return
        new_path = os.path.join(self.current_dir, new_name)
        if os.path.exists(new_path):
            self.ui.message("Target name already exists.", 1.2, "Error")
            return
        msg = "Rename?\n\nFrom: " + name + "\nTo:   " + new_name
        if not self.ui.confirm(msg):
            return
        try:
            os.rename(path, new_path)
            self.ui.set_status("Rename completed")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def chmod_selected(self):
        name, path = self.selected_item()
        if name == "..":
            return

        is_dir = os.path.isdir(path)

        try:
            current_mode = stat.S_IMODE(os.stat(path).st_mode)
            current_octal = format(current_mode, "03o")
        except Exception:
            current_octal = "755" if is_dir else "644"

        presets = []
        if is_dir:
            presets = [
                "755  rwxr-xr-x   Standard directory",
                "750  rwxr-x---   Owner full, group read/execute",
                "700  rwx------   Private directory",
                "775  rwxrwxr-x   Shared group directory",
                "777  rwxrwxrwx   Open directory",
                "Custom mode",
                "Cancel",
            ]
        else:
            presets = [
                "644  rw-r--r--   Standard file",
                "600  rw-------   Private file",
                "640  rw-r-----   Owner rw, group r",
                "664  rw-rw-r--   Shared writable file",
                "755  rwxr-xr-x   Executable file",
                "700  rwx------   Private executable",
                "777  rwxrwxrwx   Open file",
                "Custom mode",
                "Cancel",
            ]

        title = "chmod | {} | current: {}".format(name, current_octal)
        idx = self.browse_menu("chmod_menu_" + path, title, presets, 0)
        if idx is None:
            return

        selected = presets[idx]
        if selected == "Cancel":
            return

        if selected == "Custom mode":
            mode_str = self.ui.prompt(
                "chmod mode (octal, example: 755, 644, 600)",
                current_octal,
            )
            if not mode_str:
                return
            mode_str = mode_str.strip()
        else:
            mode_str = selected.split()[0].strip()

        if len(mode_str) not in (3, 4) or any(ch not in "01234567" for ch in mode_str):
            self.ui.message("Invalid mode. Use octal like 755, 644, 600, 0755", 1.2, "Error")
            return

        try:
            mode = int(mode_str, 8)
        except Exception:
            self.ui.message("Invalid mode value.", 1.2, "Error")
            return

        note = ""
        notes = {
            "644": "Standard file permission",
            "600": "Private file",
            "640": "Owner read/write, group read",
            "664": "Shared writable file",
            "755": "Executable or standard directory",
            "750": "Owner full, group limited",
            "700": "Private owner-only",
            "775": "Shared writable directory",
            "777": "Open to everyone",
        }
        note = notes.get(mode_str[-3:], "")

        msg = (
            "Change permissions?\n\n"
            "Item : " + name + "\n"
            "Path : " + path + "\n"
            "Mode : " + mode_str + "\n"
            "Note : " + (note or "-")
        )
        if not self.ui.confirm(msg, "chmod"):
            return

        try:
            os.chmod(path, mode)
            self.ui.set_status("chmod applied: " + mode_str)
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def chown_selected(self):
        name, path = self.selected_item()
        if name == "..":
            return

        owner, group = get_owner_group(path)
        value = self.ui.prompt(
            "chown owner[:group] (example: root, www-data, root:www-data)",
            owner + ":" + group,
        )
        if not value:
            return

        value = value.strip()
        if ":" in value:
            user_part, group_part = value.split(":", 1)
            user_part = user_part.strip()
            group_part = group_part.strip()
        else:
            user_part = value.strip()
            group_part = ""

        uid = -1
        gid = -1

        if user_part:
            try:
                uid = pwd.getpwnam(user_part).pw_uid
            except KeyError:
                self.ui.message("User not found: " + user_part, 1.2, "Error")
                return

        if group_part:
            try:
                gid = grp.getgrnam(group_part).gr_gid
            except KeyError:
                self.ui.message("Group not found: " + group_part, 1.2, "Error")
                return
        else:
            try:
                gid = os.stat(path).st_gid
            except Exception:
                gid = -1

        msg = (
            "Change owner/group?\n\n"
            "Item   : " + name + "\n"
            "Path   : " + path + "\n"
            "Owner  : " + (user_part or "(keep)") + "\n"
            "Group  : " + (group_part or "(keep current)")
        )
        if not self.ui.confirm(msg, "chown"):
            return

        try:
            os.chown(path, uid, gid)
            self.ui.set_status("chown applied")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def chgrp_selected(self):
        name, path = self.selected_item()
        if name == "..":
            return

        owner, group = get_owner_group(path)
        group_name = self.ui.prompt(
            "chgrp group name",
            group,
        )
        if not group_name:
            return

        group_name = group_name.strip()
        try:
            gid = grp.getgrnam(group_name).gr_gid
        except KeyError:
            self.ui.message("Group not found: " + group_name, 1.2, "Error")
            return

        try:
            uid = os.stat(path).st_uid
        except Exception:
            uid = -1

        msg = (
            "Change group?\n\n"
            "Item  : " + name + "\n"
            "Path  : " + path + "\n"
            "Group : " + group_name
        )
        if not self.ui.confirm(msg, "chgrp"):
            return

        try:
            os.chown(path, uid, gid)
            self.ui.set_status("chgrp applied: " + group_name)
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def permissions_menu(self):
        name, path = self.selected_item()
        if name == "..":
            self.ui.message("Select a file or directory first.", 1.0, "Permissions")
            return

        while True:
            try:
                st = os.stat(path)
                mode_text = stat.filemode(st.st_mode)
                mode_octal = format(stat.S_IMODE(st.st_mode), "03o")
            except Exception:
                mode_text = "?"
                mode_octal = "???"

            owner, group = get_owner_group(path)

            lines = [
                "chmod (select permission mode)",
                "chown (owner:group)",
                "chgrp (group only)",
                "Back",
            ]

            idx = self.browse_menu(
                "permissions_menu_" + path,
                "Permissions | {} | {} {} {}:{}".format(name, mode_text, mode_octal, owner, group),
                lines,
                0,
            )
            if idx is None or lines[idx] == "Back":
                return

            if lines[idx] == "chmod (select permission mode)":
                self.chmod_selected()
            elif lines[idx] == "chown (owner:group)":
                self.chown_selected()
            elif lines[idx] == "chgrp (group only)":
                self.chgrp_selected()


    def view_selected(self):
        name, path = self.selected_item()
        if name == ".." or not os.path.isfile(path):
            return
        if is_text_file(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    self.ui.view_text("View: " + path, f.read())
            except Exception as e:
                self.ui.message("Error: " + str(e), 1.2, "Error")

    def edit_selected(self):
        name, path = self.selected_item()
        if name == ".." or not os.path.isfile(path):
            return
        editor = os.environ.get("EDITOR", "nano")
        cmd = [editor, path]
        if os.path.basename(editor) == "nano":
            cmd = [editor, "-l", path]
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.run(cmd)
        finally:
            curses.reset_prog_mode()
            curses.curs_set(0)
            self.stdscr.keypad(True)
            self.stdscr.erase()
            self.stdscr.refresh()
            self.ui.set_status("Returned from editor")

    def queue_clipboard(self, mode):
        name, path = self.selected_item()
        if name == "..":
            return
        self.clipboard_mode = mode
        self.clipboard_source = path
        self.ui.set_status(mode.capitalize() + " queued. Browse to destination and press P")

    def paste_here(self):
        if not self.clipboard_mode or not self.clipboard_source:
            self.ui.message("Clipboard is empty.", 1.0)
            return
        src = self.clipboard_source
        if not os.path.exists(src):
            self.clipboard_mode = None
            self.clipboard_source = None
            self.ui.message("Source no longer exists. Clipboard cleared.", 1.2, "Error")
            return
        dst = os.path.join(self.current_dir, os.path.basename(src))
        if os.path.exists(dst):
            new_name = self.ui.prompt("Target exists. New name", os.path.basename(src))
            if not new_name:
                return
            dst = os.path.join(self.current_dir, new_name)
            if os.path.exists(dst):
                self.ui.message("Target still exists.", 1.2, "Error")
                return
        msg = self.clipboard_mode.capitalize() + " here?\n\nSource: " + src + "\nDestination: " + dst
        if not self.ui.confirm(msg):
            return
        try:
            if self.clipboard_mode == "copy":
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            else:
                shutil.move(src, dst)
                self.clipboard_mode = None
                self.clipboard_source = None
            self.ui.set_status("Paste completed")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def move_to_trash(self):
        name, path = self.selected_item()
        if name == "..":
            return
        target = os.path.join(TRASH_DIR, str(int(time.time())) + "_" + name)
        try:
            shutil.move(path, target)
            self.ui.set_status("Moved to trash")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def delete_permanently(self):
        name, path = self.selected_item()
        if name == "..":
            return
        if not self.ui.confirm("Delete permanently?\n\n" + name, "Danger"):
            return
        if not self.ui.confirm("Final confirmation: this cannot be undone.", "Danger"):
            return
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            self.ui.set_status("Deleted permanently")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def delete_menu(self):
        idx = self.browse_menu(
            "delete_menu",
            "Delete selected item",
            ["Move to trash", "Delete permanently", "Cancel"],
            0,
        )
        if idx is None or idx == 2:
            return
        if idx == 0:
            if self.ui.confirm("Move selected item to trash?"):
                self.move_to_trash()
        elif idx == 1:
            self.delete_permanently()

    def extract_selected(self):
        name, path = self.selected_item()
        if name == ".." or not can_extract(path):
            return
        default_dir = name
        for suffix in [".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar", ".zip"]:
            if default_dir.lower().endswith(suffix):
                default_dir = default_dir[: -len(suffix)]
                break
        dest_name = self.ui.prompt("Extract into directory", default_dir)
        if not dest_name:
            return
        dest_path = os.path.join(self.current_dir, dest_name)
        if os.path.exists(dest_path):
            self.ui.message("Destination already exists.", 1.2, "Error")
            return
        msg = "Extract archive?\n\nSource: " + path + "\nDestination: " + dest_path
        if not self.ui.confirm(msg):
            return
        try:
            os.makedirs(dest_path, exist_ok=False)
            lower = path.lower()
            if lower.endswith(".zip"):
                with zipfile.ZipFile(path, "r") as zf:
                    zf.extractall(dest_path)
            else:
                with tarfile.open(path, "r:*") as tf:
                    tf.extractall(dest_path)
            self.ui.set_status("Archive extracted")
        except Exception as e:
            self.ui.message("Error: " + str(e), 1.2, "Error")

    def archive_selected(self):
        name, path = self.selected_item()
        if name == ".." or not can_archive(path):
            return
        idx = self.browse_menu(
            "archive_menu",
            "Archive format",
            ["Create .zip", "Create .tar.gz", "Cancel"],
            0,
        )
        if idx is None or idx == 2:
            return

        if idx == 0:
            out_name = self.ui.prompt("Archive name", name + ".zip")
            if not out_name:
                return
            out_path = os.path.join(self.current_dir, out_name)
            if os.path.exists(out_path):
                self.ui.message("Archive already exists.", 1.2, "Error")
                return
            try:
                with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    if os.path.isdir(path):
                        for root, dirs, files in os.walk(path):
                            dirs.sort()
                            files.sort()
                            for f in files:
                                full = os.path.join(root, f)
                                arcname = os.path.relpath(full, start=self.current_dir)
                                zf.write(full, arcname)
                    else:
                        zf.write(path, arcname=name)
                self.ui.set_status("ZIP archive created")
            except Exception as e:
                self.ui.message("Error: " + str(e), 1.2, "Error")
        elif idx == 1:
            out_name = self.ui.prompt("Archive name", name + ".tar.gz")
            if not out_name:
                return
            out_path = os.path.join(self.current_dir, out_name)
            if os.path.exists(out_path):
                self.ui.message("Archive already exists.", 1.2, "Error")
                return
            try:
                with tarfile.open(out_path, "w:gz") as tf:
                    tf.add(path, arcname=name)
                self.ui.set_status("tar.gz archive created")
            except Exception as e:
                self.ui.message("Error: " + str(e), 1.2, "Error")

    def _push_hist(self, arr, value, limit=120):
        arr.append(float(value))
        if len(arr) > limit:
            del arr[: len(arr) - limit]

    def _read_cpu_times(self):
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                first = f.readline().strip().split()
            if len(first) < 8 or first[0] != "cpu":
                return None, None
            nums = [int(x) for x in first[1:]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            return total, idle
        except Exception:
            return None, None

    def _read_meminfo(self):
        data = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    k, v = line.split(":", 1)
                    p = v.strip().split()
                    if p:
                        data[k] = int(p[0]) * 1024
        except Exception:
            pass
        return data

    def _read_net_bytes(self):
        rx = 0
        tx = 0
        try:
            with open("/proc/net/dev", "r", encoding="utf-8") as f:
                lines = f.readlines()[2:]
            for line in lines:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                iface = iface.strip()
                if iface == "lo":
                    continue
                p = rest.split()
                if len(p) >= 16:
                    rx += int(p[0])
                    tx += int(p[8])
        except Exception:
            pass
        return rx, tx

    def _read_disk_bytes(self):
        read_b = 0
        write_b = 0
        try:
            with open("/proc/diskstats", "r", encoding="utf-8") as f:
                for line in f:
                    p = line.split()
                    if len(p) < 14:
                        continue
                    name = p[2]

                    if (
                        name.startswith("loop")
                        or name.startswith("ram")
                        or name.startswith("dm-")
                        or name.startswith("sr")
                    ):
                        continue

                    if name[-1:].isdigit() and not name.startswith("nvme"):
                        continue
                    if name.startswith("nvme") and "p" in name:
                        continue

                    sectors_read = int(p[5])
                    sectors_written = int(p[9])
                    read_b += sectors_read * 512
                    write_b += sectors_written * 512
        except Exception:
            pass
        return read_b, write_b

    def _read_root_disk_usage(self):
        try:
            total, used, free = shutil.disk_usage(self.current_dir if os.path.exists(self.current_dir) else "/")
            pct = (used / total * 100.0) if total else 0.0
            return total, used, free, pct
        except Exception:
            return 0, 0, 0, 0.0

    def update_live_metrics(self, force=False):
        now = time.time()
        if not force and (now - self.last_metrics_time) < 0.8:
            return

        dt = now - self.last_metrics_time if self.last_metrics_time else 0.0
        self.last_metrics_time = now

        cpu_total, cpu_idle = self._read_cpu_times()
        if cpu_total is not None and self.prev_cpu_total is not None and dt > 0:
            total_delta = cpu_total - self.prev_cpu_total
            idle_delta = cpu_idle - self.prev_cpu_idle
            cpu_pct = 0.0
            if total_delta > 0:
                cpu_pct = (1.0 - (idle_delta / total_delta)) * 100.0
            self.live_metrics["cpu_pct"] = max(0.0, min(100.0, cpu_pct))
            self._push_hist(self.cpu_history, self.live_metrics["cpu_pct"])
        self.prev_cpu_total = cpu_total
        self.prev_cpu_idle = cpu_idle

        mem = self._read_meminfo()
        mem_total = mem.get("MemTotal", 0)
        mem_avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        mem_used = max(0, mem_total - mem_avail)
        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = max(0, swap_total - swap_free)
        swap_pct = (swap_used / swap_total * 100.0) if swap_total else 0.0

        self.live_metrics["ram_total"] = mem_total
        self.live_metrics["ram_used"] = mem_used
        self.live_metrics["ram_pct"] = mem_pct
        self.live_metrics["swap_pct"] = swap_pct
        self._push_hist(self.ram_history, mem_pct)

        d_total, d_used, d_free, d_pct = self._read_root_disk_usage()
        self.live_metrics["disk_total"] = d_total
        self.live_metrics["disk_used"] = d_used
        self.live_metrics["disk_pct"] = d_pct
        self._push_hist(self.disk_history, d_pct)

        disk_read, disk_write = self._read_disk_bytes()
        if self.prev_disk is not None and dt > 0:
            prev_read, prev_write = self.prev_disk
            self.live_metrics["disk_read_bps"] = max(0.0, (disk_read - prev_read) / dt)
            self.live_metrics["disk_write_bps"] = max(0.0, (disk_write - prev_write) / dt)
        self.prev_disk = (disk_read, disk_write)

        net_rx, net_tx = self._read_net_bytes()
        if self.prev_net is not None and dt > 0:
            prev_rx, prev_tx = self.prev_net
            self.live_metrics["net_rx_bps"] = max(0.0, (net_rx - prev_rx) / dt)
            self.live_metrics["net_tx_bps"] = max(0.0, (net_tx - prev_tx) / dt)
            self._push_hist(
                self.net_history,
                max(self.live_metrics["net_rx_bps"], self.live_metrics["net_tx_bps"]),
            )
        self.prev_net = (net_rx, net_tx)

    def draw_live_panel(self, y, x, width, height, title=" Live Monitor "):
        if height < 8 or width < 24:
            return

        self.update_live_metrics()

        safe_addstr(self.stdscr, y, x, title[: max(1, width - 1)], curses.A_BOLD)

        m = self.live_metrics
        graph_w = max(8, width - 18)

        cpu_line = "CPU  {:5.1f}% {}".format(
            m["cpu_pct"], sparkline(self.cpu_history, graph_w, 0.0, 100.0)
        )
        ram_line = "RAM  {:5.1f}% {}".format(
            m["ram_pct"], sparkline(self.ram_history, graph_w, 0.0, 100.0)
        )
        disk_line = "DSK  {:5.1f}% {}".format(
            m["disk_pct"], sparkline(self.disk_history, graph_w, 0.0, 100.0)
        )

        net_max = max(self.net_history) if self.net_history else 1.0
        net_line = "NET {:>7} {}".format(
            human_rate(max(m["net_rx_bps"], m["net_tx_bps"])),
            sparkline(self.net_history, graph_w, 0.0, net_max),
        )

        lines = [
            cpu_line,
            "     load now | realtime cpu usage",
            ram_line,
            "     {} / {} | swap {:4.1f}%".format(
                human_size(m["ram_used"]),
                human_size(m["ram_total"]),
                m["swap_pct"],
            ),
            disk_line,
            "     {} / {} | R {}  W {}".format(
                human_size(m["disk_used"]),
                human_size(m["disk_total"]),
                human_rate(m["disk_read_bps"]),
                human_rate(m["disk_write_bps"]),
            ),
            net_line,
            "     RX {}  TX {}".format(
                human_rate(m["net_rx_bps"]),
                human_rate(m["net_tx_bps"]),
            ),
        ]

        max_rows = height - 1
        for i, line in enumerate(lines[:max_rows], start=1):
            safe_addstr(self.stdscr, y + i, x, line[: max(1, width - 1)])

    def live_monitor_screen(self):
        self.stdscr.timeout(1000)
        while True:
            self.update_live_metrics(force=True)
            self.stdscr.erase()
            h, w = self.stdscr.getmaxyx()

            safe_addstr(
                self.stdscr,
                0,
                0,
                " Live System Monitor | Q/Esc/Left back ".ljust(w - 1),
                cpair(1) | curses.A_BOLD,
            )

            top_lines = [
                "CPU   : {:5.1f}%".format(self.live_metrics["cpu_pct"]),
                "RAM   : {:5.1f}%  ({}/{})".format(
                    self.live_metrics["ram_pct"],
                    human_size(self.live_metrics["ram_used"]),
                    human_size(self.live_metrics["ram_total"]),
                ),
                "DISK  : {:5.1f}%  ({}/{})".format(
                    self.live_metrics["disk_pct"],
                    human_size(self.live_metrics["disk_used"]),
                    human_size(self.live_metrics["disk_total"]),
                ),
                "DISK I/O: R {}   W {}".format(
                    human_rate(self.live_metrics["disk_read_bps"]),
                    human_rate(self.live_metrics["disk_write_bps"]),
                ),
                "NET   : RX {}   TX {}".format(
                    human_rate(self.live_metrics["net_rx_bps"]),
                    human_rate(self.live_metrics["net_tx_bps"]),
                ),
                "",
                ascii_bar("CPU", self.live_metrics["cpu_pct"], 40),
                ascii_bar("RAM", self.live_metrics["ram_pct"], 40),
                ascii_bar("Disk", self.live_metrics["disk_pct"], 40),
                "",
                "CPU  : " + sparkline(self.cpu_history, min(70, w - 10), 0.0, 100.0),
                "RAM  : " + sparkline(self.ram_history, min(70, w - 10), 0.0, 100.0),
                "DISK : " + sparkline(self.disk_history, min(70, w - 10), 0.0, 100.0),
                "NET  : " + sparkline(
                    self.net_history,
                    min(70, w - 10),
                    0.0,
                    max(self.net_history) if self.net_history else 1.0,
                ),
            ]

            for i, line in enumerate(top_lines, start=2):
                if i >= h - 1:
                    break
                safe_addstr(self.stdscr, i, 2, line)

            safe_addstr(
                self.stdscr,
                h - 1,
                0,
                "Auto refresh: 1s | Left/Q/Esc back".ljust(w - 1),
                cpair(2),
            )

            self.stdscr.noutrefresh()
            curses.doupdate()

            ch = self.stdscr.getch()
            if ch in (ord("q"), ord("Q"), 27, curses.KEY_LEFT):
                self.stdscr.timeout(-1)
                return

    def show_help(self):
        lines = [
            APP_NAME + " " + APP_VERSION,
            "",
            "Main navigation",
            "---------------",
            "Up/Down     Navigate",
            "Left        Back / parent",
            "Right       Open directory or select menu item",
            "Enter       Actions menu / select",
            "Home/End    Jump to top/bottom",
            "PgUp/PgDn   Page jump",
            "Esc         Cancel dialog",
            "",
            "Function keys",
            "-------------",
            "F1          Help",
            "F2          Rename selected item",
            "F3          View text file",
            "F4          Edit file",
            "F5          Queue copy",
            "F6          Queue move",
            "F7          Create folder",
            "F8          Delete menu",
            "F9          Admin menu",
            "F10         Quit",
            "S           Search inside files",
            "M           Permissions",
            "",
            "Delete menu defaults to Move to trash.",
            "Trash location: ~/.server_manager_trash",
                        "Permissions",
            "-----------",
            "Enter -> Actions -> Permissions",
            "Allows chmod / chown / chgrp on selected item",
            "",
        ]
        self.ui.view_text("Help", lines)

    def reboot_required_status(self):
        if os.path.exists("/var/run/reboot-required"):
            return "YES"
        return "NO"

    def health_summary(self):
        _, host = run("hostname")
        _, kernel = run("uname -r")
        _, uptime = run("uptime -p")
        _, failed = run("systemctl --failed --no-legend --no-pager | wc -l")
        return [
            "OS        : " + self.profile.name,
            "Hostname  : " + host.strip(),
            "Kernel    : " + kernel.strip(),
            "Uptime    : " + uptime.strip(),
            "Failed svc: " + failed.strip(),
            "Reboot req: " + self.reboot_required_status(),
        ]

    def disk_report(self):
        _, out = run("df -h --output=source,size,used,avail,pcent,target | tail -n +2")
        lines = ["Disk usage", ""]
        for raw in out.splitlines():
            p = raw.split()
            if len(p) >= 6:
                src, size, used, avail, pcent, mount = p[:6]
                pct = float(pcent.strip("%"))
                lines.extend(
                    [
                        mount + " (" + src + ")",
                        "  Size " + size + "  Used " + used + "  Avail " + avail,
                        "  " + ascii_bar("Used", pct, 28),
                        "",
                    ]
                )
        return "\n".join(lines)

    def memory_report(self):
        _, out = run("free -m")
        lines = ["Memory usage", ""]
        for raw in out.splitlines():
            p = raw.split()
            if raw.lower().startswith("mem:") and len(p) >= 7:
                total = float(p[1])
                used = float(p[2])
                avail = float(p[6])
                pct = used / total * 100 if total else 0
                lines.extend(
                    [
                        "RAM total: " + str(int(total)) + " MB",
                        "RAM used : " + str(int(used)) + " MB",
                        "RAM avail: " + str(int(avail)) + " MB",
                        ascii_bar("RAM", pct, 30),
                        "",
                    ]
                )
            elif raw.lower().startswith("swap:") and len(p) >= 3:
                total = float(p[1])
                used = float(p[2])
                pct = used / total * 100 if total else 0
                lines.extend(
                    [
                        "Swap total: " + str(int(total)) + " MB",
                        "Swap used : " + str(int(used)) + " MB",
                        ascii_bar("Swap", pct, 30),
                        "",
                    ]
                )
        return "\n".join(lines)




    def build_grep_command(self, base_dir, pattern):
        quoted_dir = json.dumps(base_dir)
        quoted_pat = json.dumps(pattern)
        return (
            "grep -RniI --exclude-dir=.git --exclude-dir=.svn --exclude-dir=node_modules "
            "--exclude='*.pyc' --exclude='*.pyo' --exclude='*.so' "
            + quoted_pat + " " + quoted_dir + " 2>/dev/null | head -n 500"
        )

    def parse_grep_line(self, line):
        parts = line.split(":", 3)
        if len(parts) < 4:
            return None
        path, line_no, _, text = parts
        try:
            line_no = int(line_no)
        except Exception:
            return None
        return {
            "path": path,
            "line": line_no,
            "text": text,
        }

    def content_search(self):
        pattern = self.ui.prompt("Search text in current directory", "")
        if not pattern:
            return

        self.ui.set_status("Searching content...")
        cmd = self.build_grep_command(self.current_dir, pattern)

        rc, out = run(cmd)

        raw_lines = [x for x in out.splitlines() if x.strip()]
        parsed = []

        for raw in raw_lines:
            item = self.parse_grep_line(raw)
            if item:
                parsed.append(item)

        if not parsed:
            parsed = self.content_search_python_fallback(pattern, 500)

        display = []
        for item in parsed:
            rel_path = item["path"]

            try:
                rel_path = os.path.relpath(item["path"], self.current_dir)
            except Exception:
                pass
            display.append("{}:{} | {}".format(rel_path, item["line"], item["text"]))

        if not parsed:
            self.ui.message(
                "No matches found in:\n{}\n\nPattern: {}".format(self.current_dir, pattern),
                1.2,
                "Search",
            )
            self.ui.set_status("No content match found")
            return

        while True:
            idx = self.browse_menu(
                "content_search_results",
                "Content search | {} | {} match(es)".format(pattern, len(parsed)),
                display,
                0,
            )
            if idx is None:
                self.ui.set_status("Content search closed")
                return

            item = parsed[idx]
            path = item["path"]
            line_no = item["line"]

            if not os.path.isfile(path):
                self.ui.message("File no longer exists:\n" + path, 1.2, "Error")
                continue

            self.ui.view_file_at_line(path, line_no, "Search result")

    def content_search_python_fallback(self, pattern, max_results=500):
        results = []
        for root, dirs, files in os.walk(self.current_dir):
            dirs[:] = [d for d in dirs if d not in {".git", ".svn", "node_modules", "__pycache__"}]
            files.sort()
            for name in files:
                path = os.path.join(root, name)
                try:
                    if not is_text_file(path):
                        continue
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, start=1):
                            if pattern.lower() in line.lower():
                                results.append({
                                    "path": path,
                                    "line": i,
                                    "text": line.rstrip("\n"),
                                })
                                if len(results) >= max_results:
                                    return results
                except Exception:
                    pass
        return results

    def get_services_by_filter(self, mode):
        if mode == "failed":
            cmd = "systemctl list-units --type=service --failed --no-pager --plain --no-legend"
        elif mode == "running":
            cmd = "systemctl list-units --type=service --state=running --no-pager --plain --no-legend"
        elif mode == "enabled":
            cmd = "systemctl list-unit-files --type=service --state=enabled --no-pager --plain --no-legend"
        else:
            cmd = "systemctl list-units --type=service --all --no-pager --plain --no-legend"
        return run(cmd)[1].splitlines()

    def services_menu(self):
        while True:
            mode_opts = ["All services", "Failed services", "Running services", "Enabled services", "Back"]
            m = self.browse_menu("services_mode", "Service filter", mode_opts, 0)
            if m is None or mode_opts[m] == "Back":
                return

            selected_mode = mode_opts[m]
            if selected_mode == "All services":
                services = self.get_services_by_filter("all")
            elif selected_mode == "Failed services":
                services = self.get_services_by_filter("failed")
            elif selected_mode == "Running services":
                services = self.get_services_by_filter("running")
            else:
                services = self.get_services_by_filter("enabled")

            if not services:
                self.ui.message("No services found for this filter.", 1.0)
                continue

            idx = self.browse_menu("services_list_" + selected_mode, "Services | " + selected_mode, services)
            if idx is None:
                continue

            parts = services[idx].split()
            if not parts:
                continue
            unit = parts[0]

            opts = ["Show status", "Restart", "Stop", "Recent logs", "Back"]
            a = self.browse_menu("service_action_" + unit, "Service: " + unit, opts)
            if a is None or opts[a] == "Back":
                continue

            if opts[a] == "Show status":
                self.ui.view_text("Status: " + unit, run("systemctl status " + unit + " --no-pager -l | sed -n '1,60p'")[1])
            elif opts[a] == "Recent logs":
                self.ui.view_text("Logs: " + unit, run("journalctl -u " + unit + " -n 100 --no-pager")[1])
            elif opts[a] == "Restart":
                if self.ensure_root_for_action("Restart service: " + unit) and self.ui.confirm("Restart service?\n\n" + unit):
                    self.ui.view_text("Restart: " + unit, run("systemctl restart " + unit + " 2>&1")[1] or "Done")
            elif opts[a] == "Stop":
                if self.ensure_root_for_action("Stop service: " + unit) and self.ui.confirm("Stop service?\n\n" + unit):
                    self.ui.view_text("Stop: " + unit, run("systemctl stop " + unit + " 2>&1")[1] or "Done")

    def live_command_view(self, title, cmd):
        self.ui.message(
            "Live mode is opening.\n\n"
            "Press Ctrl+C to stop live output and return to the program.",
            1.2,
            "Live view",
        )

        curses.def_prog_mode()
        curses.endwin()

        try:
            p = subprocess.Popen(cmd, shell=True)
            try:
                p.wait()
            except KeyboardInterrupt:
                try:
                    p.terminate()
                    p.wait(timeout=2)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        finally:
            curses.reset_prog_mode()
            try:
                curses.curs_set(0)
            except Exception:
                pass
            self.stdscr.keypad(True)
            self.stdscr.erase()
            self.stdscr.refresh()
            self.ui.set_status("Returned from live view")

    def live_log_viewer(self, title, cmd_list, max_buffer=2000):
        lines = []
        pos = 0

        try:
            p = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.ui.message("Failed to start live log:\n" + str(e), 1.5, "Error")
            return

        self.stdscr.timeout(150)

        try:
            while True:
                if p.stdout is not None:
                    try:
                        fd = p.stdout.fileno()
                        ready, _, _ = select.select([fd], [], [], 0)
                        if ready:
                            while True:
                                ready2, _, _ = select.select([fd], [], [], 0)
                                if not ready2:
                                    break
                                line = p.stdout.readline()
                                if not line:
                                    break
                                lines.append(line.rstrip("\n"))
                                if len(lines) > max_buffer:
                                    del lines[: len(lines) - max_buffer]
                                pos = max(0, len(lines) - 1)
                    except Exception:
                        pass

                self.stdscr.erase()
                h, w = self.stdscr.getmaxyx()
                safe_addstr(
                    self.stdscr,
                    0,
                    0,
                    (title + " | Q/Esc/Left çık | Up/Down scroll | End live").ljust(w - 1),
                    cpair(1) | curses.A_BOLD,
                )

                visible = max(1, h - 2)
                start = max(0, pos - visible + 1)

                for row in range(visible):
                    idx = start + row
                    if idx >= len(lines):
                        break
                    safe_addstr(self.stdscr, row + 1, 0, lines[idx])

                self.stdscr.noutrefresh()
                curses.doupdate()

                ch = self.stdscr.getch()

                if ch in (ord("q"), ord("Q"), 27, curses.KEY_LEFT):
                    break
                elif ch == curses.KEY_UP:
                    pos = max(0, pos - 1)
                elif ch == curses.KEY_DOWN:
                    pos = min(max(0, len(lines) - 1), pos + 1)
                elif ch == curses.KEY_PPAGE:
                    pos = max(0, pos - max(1, visible - 1))
                elif ch == curses.KEY_NPAGE:
                    pos = min(max(0, len(lines) - 1), pos + max(1, visible - 1))
                elif ch == curses.KEY_HOME:
                    pos = 0
                elif ch == curses.KEY_END:
                    pos = max(0, len(lines) - 1)

                if p.poll() is not None and ch in (-1, curses.ERR):
                    time.sleep(0.05)

        finally:
            try:
                p.terminate()
            except Exception:
                pass
            try:
                p.wait(timeout=1)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            self.stdscr.timeout(1000)
            self.ui.set_status("Returned from live log view")

    def live_journal_menu(self):
        opts = [
            "journalctl -f",
            "journalctl -f -u ssh/sshd",
            "journalctl -f -p err",
            "Cancel",
        ]
        idx = self.browse_menu("live_journal_menu", "Live log follow", opts, 0)
        if idx is None or opts[idx] == "Cancel":
            return

        item = opts[idx]
        if item == "journalctl -f":
            self.live_log_viewer("Live journal", ["journalctl", "-f", "-n", "100"])
        elif item == "journalctl -f -u ssh/sshd":
            self.live_log_viewer(
                "Live ssh logs",
                ["journalctl", "-f", "-n", "100", "-u", self.profile.ssh_service],
            )
        elif item == "journalctl -f -p err":
            self.live_log_viewer("Live error logs", ["journalctl", "-f", "-n", "100", "-p", "err"])


    def logs_menu(self):
        while True:
            opts = ["Auth log", "Syslog / Messages", "Kernel log (dmesg)", "Current boot journal", "Live journal follow", "Back"]
            idx = self.browse_menu("logs_menu", "Logs", opts)
            if idx is None or opts[idx] == "Back":
                return
            item = opts[idx]
            if item == "Auth log":
                out = run("tail -n 200 " + self.profile.auth_log)[1] if os.path.exists(self.profile.auth_log) else ("Log not found: " + self.profile.auth_log)
            elif item == "Syslog / Messages":
                out = run("tail -n 200 " + self.profile.syslog)[1] if os.path.exists(self.profile.syslog) else ("Log not found: " + self.profile.syslog)
            elif item == "Kernel log (dmesg)":
                out = run("dmesg -T | tail -n 200")[1]
            elif item == "Live journal follow":
                self.live_journal_menu()
                continue
            else:
                out = run("journalctl -b --no-pager | tail -n 200")[1]
            self.ui.view_text(item, out)

    def packages_menu(self):
        if not self.profile.pkg_update:
            self.ui.message("Package manager not configured.", 1.2, "Not supported")
            return
        while True:
            opts = ["Refresh metadata", "List upgradable packages", "Upgrade all", "Reboot required?", "Back"]
            idx = self.browse_menu("packages_menu", "Packages", opts)
            if idx is None or opts[idx] == "Back":
                return
            if opts[idx] == "Refresh metadata":
                if self.ui.confirm("Run command?\n\n" + self.profile.pkg_update):
                    self.ui.view_text("Refresh metadata", run(self.profile.pkg_update)[1])
            elif opts[idx] == "List upgradable packages":
                self.ui.view_text("Upgradable packages", run(self.profile.pkg_list_upgrades)[1])
            elif opts[idx] == "Upgrade all":
                if self.ui.confirm("Run command?\n\n" + self.profile.pkg_upgrade, "Upgrade"):
                    self.ui.view_text("Upgrade all", run(self.profile.pkg_upgrade)[1])
            elif opts[idx] == "Reboot required?":
                msg = "Reboot required: " + self.reboot_required_status()
                if os.path.exists("/var/run/reboot-required.pkgs"):
                    try:
                        with open("/var/run/reboot-required.pkgs", "r", encoding="utf-8", errors="replace") as f:
                            msg += "\n\nPackages:\n" + f.read()
                    except Exception:
                        pass
                self.ui.view_text("Reboot required?", msg)

    def network_menu(self):
        while True:
            opts = ["IP addresses", "Routes", "DNS config", "Listening ports", "Back"]
            idx = self.browse_menu("network_menu", "Network", opts)
            if idx is None or opts[idx] == "Back":
                return
            item = opts[idx]
            if item == "IP addresses":
                out = run("ip -brief addr || ifconfig")[1]
            elif item == "Routes":
                out = run("ip route || route -n")[1]
            elif item == "DNS config":
                out = run("cat /etc/resolv.conf")[1]
            else:
                out = run("ss -tulpn || netstat -tulpn")[1]
            self.ui.view_text(item, out)

    def docker_menu(self):
        if not shutil.which("docker"):
            self.ui.message("docker command not found.", 1.2, "Docker")
            return

        while True:
            opts = [
                "Container list",
                "Running containers",
                "All containers",
                "Back",
            ]
            idx = self.browse_menu("docker_menu", "Docker", opts, 0)
            if idx is None or opts[idx] == "Back":
                return

            if opts[idx] == "Container list" or opts[idx] == "All containers":
                out = run('docker ps -a --format "{{.ID}}  {{.Names}}  {{.Status}}  {{.Image}}"')[1]
            else:
                out = run('docker ps --format "{{.ID}}  {{.Names}}  {{.Status}}  {{.Image}}"')[1]

            lines = [x for x in out.splitlines() if x.strip()]
            if not lines:
                self.ui.message("No containers found.", 1.0, "Docker")
                continue

            cidx = self.browse_menu("docker_containers", "Docker containers", lines, 0)
            if cidx is None:
                continue

            picked = lines[cidx].split()
            if not picked:
                continue
            cid = picked[0]
            cname = picked[1] if len(picked) > 1 else cid

            actions = ["Show logs", "Follow logs", "Restart", "Stop", "Inspect", "Back"]
            a = self.browse_menu("docker_actions_" + cid, "Docker: " + cname, actions, 0)
            if a is None or actions[a] == "Back":
                continue

            action = actions[a]
            if action == "Show logs":
                self.ui.view_text("Docker logs: " + cname, run("docker logs --tail 200 " + cid)[1])
            elif action == "Follow logs":
                self.live_log_viewer("Docker follow logs: " + cname, ["docker", "logs", "-f", "--tail", "100", cid])
            elif action == "Restart":
                if self.ensure_root_for_action("Docker restart: " + cname) and self.ui.confirm("Restart container?\n\n" + cname):
                    self.ui.view_text("Docker restart: " + cname, run("docker restart " + cid)[1] or "Done")
            elif action == "Stop":
                if self.ensure_root_for_action("Docker stop: " + cname) and self.ui.confirm("Stop container?\n\n" + cname):
                    self.ui.view_text("Docker stop: " + cname, run("docker stop " + cid)[1] or "Done")
            elif action == "Inspect":
                self.ui.view_text("Docker inspect: " + cname, run("docker inspect " + cid)[1])

    def firewall_menu(self):
        if self.profile.firewall_tool == "ufw":
            self.ufw_menu()
        elif self.profile.firewall_tool == "firewalld":
            self.firewalld_menu()
        else:
            self.ui.message("No supported firewall tool found (ufw/firewalld).", 1.2, "Firewall")

    def disk_cleanup_menu(self):
        while True:
            opts = [
                "Find large files",
                "Old log files in /var/log",
                "Clean /tmp preview",
                "Delete old files in /tmp",
                "Back",
            ]
            idx = self.browse_menu("disk_cleanup_menu", "Disk cleanup tools", opts, 0)
            if idx is None or opts[idx] == "Back":
                return

            item = opts[idx]
            if item == "Find large files":
                out = run("find " + json.dumps(self.current_dir) + " -type f -printf '%s %p\n' 2>/dev/null | sort -nr | head -n 100")[1]
                lines = []
                for raw in out.splitlines():
                    p = raw.split(" ", 1)
                    if len(p) == 2:
                        try:
                            size = human_size(int(p[0]))
                        except Exception:
                            size = p[0]
                        lines.append(size + "  " + p[1])
                self.ui.view_text("Large files", lines or ["No result"])
            elif item == "Old log files in /var/log":
                out = run("find /var/log -type f -mtime +7 2>/dev/null | sort | head -n 200")[1]
                self.ui.view_text("Old log files", out or "No old logs found")
            elif item == "Clean /tmp preview":
                out = run("find /tmp -mindepth 1 -maxdepth 1 2>/dev/null | sort | head -n 200")[1]
                self.ui.view_text("/tmp contents", out or "No files")
            elif item == "Delete old files in /tmp":
                days = self.ui.prompt("Delete files older than how many days?", "3")
                if not days:
                    continue
                if not days.isdigit():
                    self.ui.message("Invalid number.", 1.2, "Error")
                    continue
                if self.ensure_root_for_action("Delete old files in /tmp") and self.ui.confirm("Delete files in /tmp older than " + days + " day(s)?", "Danger"):
                    cmd = "find /tmp -mindepth 1 -mtime +" + days + " -exec rm -rf {} + 2>&1"
                    self.ui.view_text("tmp cleanup", run(cmd)[1] or "Done")

    def smart_menu(self):
        if not shutil.which("smartctl"):
            self.ui.message("smartctl not found. Install smartmontools.", 1.2, "SMART")
            return

        out = run("lsblk -dn -o NAME,TYPE | awk '$2==\"disk\"{print $1}'")[1]
        disks = ["/dev/" + x.strip() for x in out.splitlines() if x.strip()]
        if not disks:
            self.ui.message("No disks found.", 1.2, "SMART")
            return

        idx = self.browse_menu("smart_disks", "SMART disks", disks, 0)
        if idx is None:
            return

        disk = disks[idx]
        if not self.ensure_root_for_action("Read SMART info for " + disk):
            return

        info = run("smartctl -H -A " + disk + " 2>&1")[1]
        self.ui.view_text("SMART: " + disk, info)

    def user_sessions_menu(self):
        while True:
            opts = [
                "Current users (who)",
                "Login history (last)",
                "Failed logins summary",
                "Back",
            ]
            idx = self.browse_menu("user_sessions_menu", "User / Session tracking", opts, 0)
            if idx is None or opts[idx] == "Back":
                return

            item = opts[idx]
            if item == "Current users (who)":
                self.ui.view_text("who", run("who")[1] or "No active sessions")
            elif item == "Login history (last)":
                self.ui.view_text("last", run("last -n 100")[1])
            elif item == "Failed logins summary":
                if os.path.exists(self.profile.auth_log):
                    cmd = "grep -i 'failed\\|failure\\|invalid user' " + self.profile.auth_log + " 2>/dev/null | tail -n 200"
                    self.ui.view_text("Failed logins", run(cmd)[1] or "No failed logins found")
                else:
                    self.ui.view_text("Failed logins", "Auth log not found: " + self.profile.auth_log)

    def root_shell_prompt(self):
        if self.is_root():
            self.ui.message("Already running as root.", 1.0, "Root")
            return

        if not self.ui.confirm("Open a root shell attempt using sudo -s?\n\nYou may be asked for password.", "Root shell"):
            return

        self.live_command_view("sudo shell", "sudo -s")

    def ufw_menu(self):
        while True:
            opts = ["Status", "Enable", "Disable", "Reload", "Back"]
            idx = self.browse_menu("ufw_menu", "Firewall | UFW", opts, 0)
            if idx is None or opts[idx] == "Back":
                return

            item = opts[idx]
            if item == "Status":
                self.ui.view_text("UFW status", run("ufw status verbose")[1])
            elif item == "Enable":
                if self.ensure_root_for_action("Enable UFW") and self.ui.confirm("Enable UFW?"):
                    self.ui.view_text("UFW enable", run("ufw --force enable")[1])
            elif item == "Disable":
                if self.ensure_root_for_action("Disable UFW") and self.ui.confirm("Disable UFW?"):
                    self.ui.view_text("UFW disable", run("ufw disable")[1])
            elif item == "Reload":
                if self.ensure_root_for_action("Reload UFW") and self.ui.confirm("Reload UFW?"):
                    self.ui.view_text("UFW reload", run("ufw reload")[1])

    def firewalld_menu(self):
        while True:
            opts = ["Status", "List all", "Reload", "Back"]
            idx = self.browse_menu("firewalld_menu", "Firewall | firewalld", opts, 0)
            if idx is None or opts[idx] == "Back":
                return

            item = opts[idx]
            if item == "Status":
                self.ui.view_text("firewalld status", run("firewall-cmd --state")[1])
            elif item == "List all":
                self.ui.view_text("firewalld list-all", run("firewall-cmd --list-all")[1])
            elif item == "Reload":
                if self.ensure_root_for_action("Reload firewalld") and self.ui.confirm("Reload firewalld?"):
                    self.ui.view_text("firewalld reload", run("firewall-cmd --reload")[1])

    def admin_menu(self):
        while True:
            opts = [
                "System dashboard",
                "Live resource monitor",
                "Disk usage graph",
                "Memory usage graph",
                "Network info",
                "Services",
                "Live logs",
                "Logs",
                "Packages",
                "Docker",
                "Firewall",
                "Disk cleanup tools",
                "SMART disk health",
                "User / session tracking",
                "Permissions on selected item",
                "Search text in current directory",
                "Root shell attempt",
                "Back",
            ]

            idx = self.browse_menu("admin_menu", "Admin menu", opts)
            if idx is None or opts[idx] == "Back":
                return

            item = opts[idx]

            if item == "System dashboard":
                self.ui.view_text("System dashboard", self.health_summary())
            elif item == "Live resource monitor":
                self.live_monitor_screen()
            elif item == "Disk usage graph":
                self.ui.view_text("Disk usage", self.disk_report())
            elif item == "Memory usage graph":
                self.ui.view_text("Memory usage", self.memory_report())
            elif item == "Network info":
                self.network_menu()
            elif item == "Services":
                self.services_menu()
            elif item == "Live logs":
                self.live_journal_menu()
            elif item == "Logs":
                self.logs_menu()
            elif item == "Packages":
                self.packages_menu()
            elif item == "Docker":
                self.docker_menu()
            elif item == "Firewall":
                self.firewall_menu()
            elif item == "Disk cleanup tools":
                self.disk_cleanup_menu()
            elif item == "SMART disk health":
                self.smart_menu()
            elif item == "User / session tracking":
                self.user_sessions_menu()
            elif item == "Permissions on selected item":
                self.permissions_menu()
            elif item == "Search text in current directory":
                self.content_search()
            elif item == "Root shell attempt":
                self.root_shell_prompt()

    def files_action_menu(self):
        name, path = self.selected_item()
        if name == "..":
            idx = self.browse_menu(
                "dir_actions",
                "Directory actions",
                ["Go up", "Create new directory", "Paste here", "Cancel"],
            )
            if idx is None or idx == 3:
                return
            if idx == 0:
                self.go_parent()
            elif idx == 1:
                self.create_folder()
            elif idx == 2:
                self.paste_here()
            return

        options = []
        if os.path.isdir(path):
            options.append("Open")
        if os.path.isfile(path) and is_text_file(path):
            options.append("View")
            options.append("Edit")
        options.extend(["Rename", "Permissions", "Copy", "Move", "Paste here"])
        if os.path.isfile(path) and can_extract(path):
            options.append("Extract")
        if can_archive(path):
            options.append("Archive")
        options.extend(["Delete", "Cancel"])

        idx = self.browse_menu("file_actions_" + path, "Actions: " + name, options)
        if idx is None:
            return

        action = options[idx]
        if action == "Open":
            self.enter_selected()
        elif action == "View":
            self.view_selected()
        elif action == "Edit":
            self.edit_selected()
        elif action == "Rename":
            self.rename_selected()
        elif action == "Permissions":
            self.permissions_menu()
        elif action == "Copy":
            self.queue_clipboard("copy")
        elif action == "Move":
            self.queue_clipboard("move")
        elif action == "Paste here":
            self.paste_here()
        elif action == "Delete":
            self.delete_menu()
        elif action == "Extract":
            self.extract_selected()
        elif action == "Archive":
            self.archive_selected()

    def run(self):
        curses.curs_set(0)
        self.stdscr.keypad(True)
        init_colors()
        self.ui.set_status("Ready | F1 Help | F9 Admin | Enter Actions")
        self.stdscr.timeout(1000)
        while True:
            try:
                h, w = self.stdscr.getmaxyx()
                if h < MIN_H or w < MIN_W:
                    self.ui.draw_too_small()
                    ch = self.stdscr.getch()
                    if ch in (ord("q"), ord("Q"), curses.KEY_F10):
                        break
                    continue

                self.draw_main()
                items = self.get_items()
                visible_rows = h - 3
                page = max(1, visible_rows - 2)
                ch = self.stdscr.getch()

                if ch == curses.KEY_RESIZE:
                    continue
                elif ch == curses.KEY_UP and self.selected > 0:
                    self.selected -= 1
                elif ch == curses.KEY_DOWN and self.selected < len(items) - 1:
                    self.selected += 1
                elif ch == curses.KEY_LEFT:
                    self.go_parent()
                elif ch == curses.KEY_RIGHT:
                    self.enter_selected()
                elif ch == curses.KEY_HOME:
                    self.selected = 0
                    self.top = 0
                elif ch == curses.KEY_END:
                    self.selected = max(0, len(items) - 1)
                elif ch == curses.KEY_PPAGE:
                    self.selected = max(0, self.selected - page)
                elif ch == curses.KEY_NPAGE:
                    self.selected = min(len(items) - 1, self.selected + page)
                elif ch in (10, 13):
                    self.files_action_menu()
                elif ch == curses.KEY_F1:
                    self.show_help()
                elif ch == curses.KEY_F2:
                    self.rename_selected()
                elif ch == curses.KEY_F3:
                    self.view_selected()
                elif ch == curses.KEY_F4:
                    self.edit_selected()
                elif ch == curses.KEY_F5:
                    self.queue_clipboard("copy")
                elif ch == curses.KEY_F6:
                    self.queue_clipboard("move")
                elif ch == curses.KEY_F7:
                    self.create_folder()
                elif ch == curses.KEY_F8:
                    self.delete_menu()
                elif ch == ord("m") or ch == ord("M"):
                    self.permissions_menu()
                elif ch == ord("s") or ch == ord("S"):
                    self.content_search()
                elif ch == curses.KEY_F9 or ch in (ord("a"), ord("A")):
                    self.admin_menu()
                elif ch == curses.KEY_F8:
                    self.delete_menu()
                elif ch == ord("m") or ch == ord("M"):
                    self.permissions_menu()
                elif ch == ord("s") or ch == ord("S"):
                    self.content_search()
                elif ch == curses.KEY_F9 or ch in (ord("a"), ord("A")):
                    self.admin_menu()
                elif ch in (ord("p"), ord("P")):
                    self.paste_here()
                elif ch in (ord("b"), ord("B"), curses.KEY_BACKSPACE, 127, 8):
                    self.go_parent()
                elif ch == ord("/"):
                    q = self.ui.prompt("Find first match in current directory")
                    if q:
                        matches = [i for i, item_name in enumerate(items) if q.lower() in item_name.lower()]
                        if matches:
                            self.selected = matches[0]
                            self.top = max(0, self.selected - 3)
                            self.ui.set_status("Jumped to first match: " + q)
                        else:
                            self.ui.set_status("No match found")
                elif ch in (curses.KEY_F10, ord("q"), ord("Q")):
                    if self.ui.confirm("Quit the program?"):
                        break

                self.save_state()

            except Exception as e:
                self.ui.message("Recovered from UI error:\n" + str(e), 1.2, "Recovered")


def main(stdscr):
    app = ServerManager(stdscr)
    app.run()


if __name__ == "__main__":
    curses.wrapper(main)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 端布局预览：把 dashboard/render.js 的绘制逻辑用 Python 复刻一遍，
直接输出 PNG，方便不开浏览器就能确认 1920x1080（或任意分辨率）下的排版、
有没有文字溢出/重叠。

用法:
    python tools/preview.py                        # 1920x1080 -> tools/preview_1920x1080.png
    python tools/preview.py --w 1024 --h 600       # 小屏比例检查
    python tools/preview.py --blank                # 未连接状态（NO SIGNAL）

它是 render.js 的校验镜像：两份实现共用同一份字库（dashboard/font.js），
画出来的像素应该完全一致，用 tools/render_check.js 出图对比即可。
"""

from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_font import G, pack, W as FW, H as FH  # noqa: E402

CHAR_ADV = 6

# 与 render.js 完全一致的配色
C_BG = 0x080C12
C_CARD = 0x111A24
C_CARD_EDGE = 0x22303F
C_TRACK = 0x1E2833
C_TEXT = 0xE2E8F0
C_DIM = 0x7C8DA3
C_DIMMER = 0x3D4C5E
C_CYAN = 0x22D3EE
C_GREEN = 0x34D399
C_AMBER = 0xFBBF24
C_RED = 0xF87171

SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F, SEG_G = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40
DIGIT_SEG = [
    SEG_A | SEG_B | SEG_C | SEG_D | SEG_E | SEG_F,
    SEG_B | SEG_C,
    SEG_A | SEG_B | SEG_G | SEG_E | SEG_D,
    SEG_A | SEG_B | SEG_G | SEG_C | SEG_D,
    SEG_F | SEG_G | SEG_B | SEG_C,
    SEG_A | SEG_F | SEG_G | SEG_C | SEG_D,
    SEG_A | SEG_F | SEG_G | SEG_E | SEG_C | SEG_D,
    SEG_A | SEG_B | SEG_C,
    SEG_A | SEG_B | SEG_C | SEG_D | SEG_E | SEG_F | SEG_G,
    SEG_A | SEG_B | SEG_C | SEG_D | SEG_F | SEG_G,
]

CARDS = [
    ("CPU", "LOAD", C_CYAN),
    ("MEM", "USED", C_GREEN),
    ("GPU", "LOAD", C_AMBER),
]


def build_glyph_table():
    tbl = []
    for code in range(0x20, 0x80):
        rows = G.get(chr(code), ["....."] * 7)
        tbl.append(pack(rows))
    return tbl


GLYPHS = build_glyph_table()


def rgb(c):
    return bytes(((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF))


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.rows = [bytearray(w * 3) for _ in range(h)]

    def fill_rect(self, x, y, w, h, c):
        if w <= 0 or h <= 0:
            return
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, self.w), min(y + h, self.h)
        if x0 >= x1 or y0 >= y1:
            return
        pat = rgb(c) * (x1 - x0)
        for yy in range(y0, y1):
            self.rows[yy][x0 * 3:x1 * 3] = pat

    def fill_hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def fill_circle(self, cx, cy, r, c):
        if r <= 0:
            return
        for dy in range(-r, r + 1):
            dx = int(math.isqrt(max(0, r * r - dy * dy)))
            self.fill_rect(cx - dx, cy + dy, dx * 2 + 1, 1, c)

    def fill_round_rect(self, x, y, w, h, r, c):
        if w <= 0 or h <= 0:
            return
        r = min(r, w // 2, h // 2)
        if r <= 0:
            self.fill_rect(x, y, w, h, c)
            return
        self.fill_rect(x + r, y, w - 2 * r, h, c)
        self.fill_rect(x, y + r, r, h - 2 * r, c)
        self.fill_rect(x + w - r, y + r, r, h - 2 * r, c)
        self.fill_circle(x + r, y + r, r, c)
        self.fill_circle(x + w - r - 1, y + r, r, c)
        self.fill_circle(x + r, y + h - r - 1, r, c)
        self.fill_circle(x + w - r - 1, y + h - r - 1, r, c)

    # ---------------- 文本 ----------------

    @staticmethod
    def text_width(s, scale):
        if not s:
            return 0
        return len(s) * CHAR_ADV * scale - scale

    def draw_text(self, x, y, scale, c, s):
        if not s or scale < 1:
            return 0
        cx = x
        for ch in s:
            code = ord(ch)
            g = GLYPHS[code - 0x20] if 0x20 <= code <= 0x7F else None
            if g:
                for col in range(FW):
                    bits = g[col]
                    if not bits:
                        continue
                    for row in range(FH):
                        if bits & (1 << row):
                            self.fill_rect(cx + col * scale, y + row * scale, scale, scale, c)
            cx += CHAR_ADV * scale
        return cx - x

    def draw_text_right(self, x_right, y, scale, c, s):
        self.draw_text(x_right - self.text_width(s, scale), y, scale, c, s)

    # ---------------- 7 段 ----------------

    def seg_h(self, x, yc, ln, t, c):
        self.fill_round_rect(x, yc - t // 2, ln, t, t // 2, c)

    def seg_v(self, xc, y, ln, t, c):
        self.fill_round_rect(xc - t // 2, y, t, ln, t // 2, c)

    def draw_digit(self, x, y, w, h, seg, on):
        t = max(2, w * 15 // 100)
        hh = h // 2
        hl = w - t
        vl = hh - t
        if seg & SEG_A:
            self.seg_h(x + t // 2, y + t // 2, hl, t, on)
        if seg & SEG_G:
            self.seg_h(x + t // 2, y + hh, hl, t, on)
        if seg & SEG_D:
            self.seg_h(x + t // 2, y + h - t // 2, hl, t, on)
        if seg & SEG_F:
            self.seg_v(x + t // 2, y + t // 2, vl, t, on)
        if seg & SEG_B:
            self.seg_v(x + w - t // 2, y + t // 2, vl, t, on)
        if seg & SEG_E:
            self.seg_v(x + t // 2, y + hh + t // 2, vl, t, on)
        if seg & SEG_C:
            self.seg_v(x + w - t // 2, y + hh + t // 2, vl, t, on)

    def draw_digits_right(self, x_right, y, w, h, gap, on, digits):
        n = len(digits)
        total = n * w + (n - 1) * gap
        x = x_right - total
        for i, ch in enumerate(digits):
            if "0" <= ch <= "9":
                self.draw_digit(x + i * (w + gap), y, w, h, DIGIT_SEG[ord(ch) - 48], on)
        return x


class Layout:
    def __init__(self, cv):
        self.W, self.H = cv.w, cv.h
        k = min(cv.w / 1920.0, cv.h / 1080.0)
        self.k = k if k > 0 else 1.0
        self.ox = (self.W - int(1920 * self.k)) // 2
        self.oy = (self.H - int(1080 * self.k)) // 2

        self.m = self.sc(48)
        self.hdr_y = self.sc(30)
        self.hdr_scale = self.scl(5)
        self.hdr_sep_y = self.sc(92)
        self.foot_y = self.sc(1000)
        self.foot_scale = self.scl(3)

        self.card_y = self.sc(130)
        self.card_h = self.sc(840)
        self.card_w = self.sc(576)
        self.card_r = self.sc(26)
        self.pad = self.sc(40)
        self.card_x = [self.sc(48) + i * (self.card_w + self.sc(48)) for i in range(3)]

        self.title_y = self.sc(36)
        self.title_scale = self.scl(5)
        self.tag_y = self.sc(42)
        self.tag_scale = self.scl(3)

        self.num_y = self.sc(150)
        self.num_h = self.sc(250)
        self.num_w = self.sc(132)
        self.num_gap = self.sc(14)
        self.pct_scale = self.scl(8)
        self.pct_gap = self.sc(24)

        self.temp_y = self.sc(490)
        self.temp_h = self.sc(150)
        self.temp_w = self.sc(74)
        self.temp_gap = self.sc(4)
        self.temp_label_scale = self.scl(4)
        self.temp_dot = self.sc(8)
        self.unit_scale = self.scl(5)

        self.bar_y = self.sc(690)
        self.bar_h = self.sc(26)
        self.foot2_y = self.sc(762)
        self.foot2_scale = self.scl(3)

        self.num_clear_w = self.sc(500)
        self.temp_clear_w = self.sc(420)

    def sc(self, v):
        return int(v * self.k + 0.5)

    def scl(self, v):
        return max(1, int(v * self.k + 0.5))


def load_color(accent, load):
    if load >= 90:
        return C_RED
    if load >= 70:
        return C_AMBER
    return accent


def secondary_label(i):
    return "FREE" if i == 1 else "TEMP"


def secondary_unit(i):
    return "GB" if i == 1 else "\x7fC"


def secondary_value(i, d):
    if not d:
        return None
    if i == 0:
        v = d.get("cpu_temp_c10")
        return v if v and v > 0 else None
    if i == 1:
        if not d.get("mem_total"):
            return None
        free = max(0, d["mem_total"] - d["mem_used"])
        return free * 10 // 1024
    v = d.get("gpu_temp_c10")
    return v if v and v > 0 else None


def draw_value_x10(cv, num_right, y, w, h, gap, dot_w, color, v10):
    v10 = max(0, v10)
    whole, frac = v10 // 10, v10 % 10
    ip = str(whole)
    x = num_right
    cv.draw_digits_right(x, y, w, h, gap, color, str(frac))
    x -= w
    dot_gap = gap + dot_w // 2
    dot_x = x - dot_gap - dot_w
    cv.fill_round_rect(dot_x, y + h - dot_w - gap, dot_w, dot_w, dot_w // 2, color)
    x = dot_x - dot_gap
    cv.draw_digits_right(x, y, w, h, gap, color, ip)
    x -= len(ip) * w + (len(ip) - 1) * gap
    return x


def draw_card_frame(cv, L, i):
    x = L.ox + L.card_x[i]
    y = L.oy + L.card_y
    cv.fill_round_rect(x, y, L.card_w, L.card_h, L.card_r, C_CARD_EDGE)
    cv.fill_round_rect(x + 2, y + 2, L.card_w - 4, L.card_h - 4,
                       max(0, L.card_r - 2), C_CARD)


def draw_card_static(cv, L, i):
    cx = L.ox + L.card_x[i]
    cy = L.oy + L.card_y
    title, tag, accent = CARDS[i]

    draw_card_frame(cv, L, i)
    cv.draw_text(cx + L.pad, cy + L.title_y, L.title_scale, accent, title)
    cv.draw_text_right(cx + L.card_w - L.pad, cy + L.tag_y, L.tag_scale, C_DIMMER, tag)

    sep_y = cy + L.title_y + L.title_scale * FH + L.sc(14)
    cv.fill_hline(cx + L.pad, sep_y, L.card_w - 2 * L.pad, C_TRACK)

    cv.fill_hline(cx + L.pad, cy + L.sc(452), L.card_w - 2 * L.pad, C_TRACK)

    cv.draw_text(cx + L.pad,
                 cy + L.temp_y + (L.temp_h - L.temp_label_scale * FH) // 2,
                 L.temp_label_scale, C_DIM, secondary_label(i))

    cv.fill_round_rect(cx + L.pad, cy + L.bar_y, L.card_w - 2 * L.pad,
                       L.bar_h, L.bar_h // 2, C_TRACK)


def clear_card_values(cv, L, i):
    cx = L.ox + L.card_x[i]
    cy = L.oy + L.card_y
    right = cx + L.card_w - L.pad
    cv.fill_rect(right - L.num_clear_w, cy + L.num_y - L.sc(10),
                 L.num_clear_w, L.num_h + L.sc(20), C_CARD)
    cv.fill_rect(right - L.temp_clear_w, cy + L.temp_y - L.sc(10),
                 L.temp_clear_w, L.temp_h + L.sc(20), C_CARD)
    cv.fill_rect(cx + L.pad, cy + L.bar_y - L.sc(2),
                 L.card_w - 2 * L.pad, L.bar_h + L.sc(4), C_CARD)
    cv.fill_rect(cx + L.pad, cy + L.foot2_y - L.sc(6),
                 L.card_w - 2 * L.pad, L.foot2_scale * FH + L.sc(12), C_CARD)


def footnote(i, d):
    if not d:
        return "--"
    if i == 0:
        return f"{d['cpu_clk'] / 1000:.2f} GHZ" if d["cpu_clk"] else "--"
    if i == 1:
        return f"{d['mem_used'] / 1024:.1f} / {d['mem_total'] / 1024:.1f} GB" if d["mem_total"] else "--"
    if d["gpu_used"] or d["gpu_power"]:
        return f"{d['gpu_used'] / 1024:.1f} GB  {d['gpu_power']:.0f} W"
    return "--"


def draw_card_values(cv, L, i, d, link_ok):
    cx = L.ox + L.card_x[i]
    cy = L.oy + L.card_y
    right = cx + L.card_w - L.pad

    load = 0
    if d:
        load = (d["cpu_load"], d["mem_load"], d["gpu_load"])[i] or 0
    load = max(0, min(100, load))

    accent = load_color(CARDS[i][2], load)
    if not link_ok:
        accent = C_DIMMER

    # 大号百分比
    pct_w = cv.text_width("%", L.pct_scale)
    num_right = right - pct_w - L.pct_gap
    digits = str(load) if (link_ok and d) else "--"
    cv.draw_digits_right(num_right, cy + L.num_y, L.num_w, L.num_h, L.num_gap, accent, digits)
    pct_y = cy + L.num_y + L.num_h - L.pct_scale * FH - L.sc(10)
    cv.draw_text(num_right + L.pct_gap, pct_y, L.pct_scale, accent, "%")

    # 副数值（温度 / 剩余内存）
    v10 = secondary_value(i, d) if link_ok else None
    unit = secondary_unit(i)
    unit_w = cv.text_width(unit, L.unit_scale)
    num_right2 = right - unit_w - L.sc(14)
    uy = cy + L.temp_y + L.temp_h - L.unit_scale * FH - L.sc(6)
    if v10 is not None:
        draw_value_x10(cv, num_right2, cy + L.temp_y, L.temp_w, L.temp_h,
                       L.temp_gap, L.temp_dot, accent, v10)
        cv.draw_text(right - unit_w, uy, L.unit_scale, C_DIM, unit)
    else:
        # 没有数据时画 "--"，和单位同一基线
        dash_scale = L.unit_scale * 2
        cv.draw_text_right(num_right2, uy + L.unit_scale * FH - dash_scale * FH,
                           dash_scale, C_DIMMER, "--")
        cv.draw_text(right - unit_w, uy, L.unit_scale, C_DIMMER, unit)

    # 进度条
    bar_w = L.card_w - 2 * L.pad
    fill_w = bar_w * load // 100
    if fill_w > 0 and link_ok:
        fill_w = max(fill_w, L.bar_h)
        cv.fill_round_rect(cx + L.pad, cy + L.bar_y, fill_w, L.bar_h,
                           L.bar_h // 2, accent)

    # 底部小字
    cv.draw_text(cx + L.pad, cy + L.foot2_y, L.foot2_scale, C_DIM,
                 footnote(i, d if link_ok else None))


def draw_header(cv, L, d, link_ok):
    y = L.oy + L.hdr_y
    left = L.ox + L.m
    right = L.ox + L.sc(1920) - L.m

    cv.draw_text(left, y, L.hdr_scale, C_TEXT, "SYSTEM MONITOR")

    st_scale = L.scl(4)
    st = "LINK OK" if link_ok else "NO SIGNAL"
    st_col = C_GREEN if link_ok else C_RED
    st_w = cv.text_width(st, st_scale)
    dot_r = L.sc(6)
    st_y = L.oy + L.hdr_y + (L.hdr_scale - st_scale) * FH // 2 + L.sc(2)

    cv.fill_circle(right - st_w - dot_r * 3, st_y + st_scale * FH // 2, dot_r, st_col)
    cv.draw_text(right - st_w, st_y, st_scale, st_col, st)

    if d:
        cv.draw_text_right(right - st_w - dot_r * 6, y, L.scl(3), C_DIMMER,
                           f"SEQ {d['seq']}")

    cv.fill_hline(left, L.oy + L.hdr_sep_y, L.sc(1920) - 2 * L.m, C_TRACK)


def draw_footer(cv, L, link_ok):
    y = L.oy + L.foot_y
    left = L.ox + L.m
    right = L.ox + L.sc(1920) - L.m
    cv.draw_text(left, y, L.foot_scale, C_DIMMER,
                 "PC LINK  LIVE" if link_ok else "WAITING FOR DATA")
    cv.draw_text_right(right, y, L.foot_scale, C_DIMMER,
                       f"SYSTEM MONITOR  {L.W}x{L.H}")


def render(w, h, data=None, link_ok=True):
    cv = Canvas(w, h)
    L = Layout(cv)
    cv.fill_rect(0, 0, w, h, C_BG)
    draw_header(cv, L, data, link_ok)
    for i in range(3):
        draw_card_static(cv, L, i)
    draw_footer(cv, L, link_ok)
    for i in range(3):
        draw_card_values(cv, L, i, data, link_ok)
    return cv


DEMO = {
    "seq": 1234,
    "cpu_load": 37,
    "mem_load": 62,
    "cpu_temp_c10": 612,
    "cpu_clk": 3800,
    "mem_used": 12800,
    "mem_total": 32768,
    "gpu_load": 88,
    "gpu_temp_c10": 735,
    "gpu_used": 5120,
    "gpu_power": 118.0,
}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--w", type=int, default=1920)
    ap.add_argument("--h", type=int, default=1080)
    ap.add_argument("--blank", action="store_true", help="未连接状态")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cv = render(args.w, args.h, None if args.blank else DEMO, link_ok=not args.blank)
    out = args.out or os.path.join(root, "tools", f"preview_{args.w}x{args.h}.png")

    from PIL import Image
    img = Image.frombytes("RGB", (args.w, args.h),
                          b"".join(bytes(r) for r in cv.rows))
    img.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

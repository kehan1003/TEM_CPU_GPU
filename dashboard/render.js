/* =========================================================================
 * SYSTEM MONITOR 渲染核心（浏览器 / Node 通用）
 *
 * 唯一的一份绘制实现：设计基准 1920×1080，按 min(w/1920, h/1080) 等比缩放后居中。
 * tools/preview.py 是它逐行对应的 Python 镜像，两份输出用
 * tools/render_check.js + 像素比对来保证一致（当前全图 0 差异）。
 * 只依赖一个支持 fillRect / roundRect / arc 的 2D ctx，方便在 Node 里
 * 用假 canvas 跑像素校验。
 * ========================================================================= */
"use strict";

const FONT = (typeof MON_FONT5X7 !== "undefined") ? MON_FONT5X7 : require("./font.js");

const DW = 1920, DH = 1080;

/* 配色（0xRRGGBB，与 C 版一致） */
const COL = {
  bg:       "#080C12",
  card:     "#111A24",
  cardEdge: "#22303F",
  track:    "#1E2833",
  text:     "#E2E8F0",
  dim:      "#7C8DA3",
  dimmer:   "#3D4C5E",
  cyan:     "#22D3EE",
  green:    "#34D399",
  amber:    "#FBBF24",
  red:      "#F87171",
};

const SEG_A = 0x01, SEG_B = 0x02, SEG_C = 0x04, SEG_D = 0x08,
      SEG_E = 0x10, SEG_F = 0x20, SEG_G = 0x40;

const DIGIT_SEG = [
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
];

const CARDS = [
  { title: "CPU", tag: "LOAD", accent: COL.cyan },
  { title: "MEM", tag: "USED", accent: COL.green },
  { title: "GPU", tag: "LOAD", accent: COL.amber },
];

const FONT_W = 5, FONT_H = 7, CHAR_ADV = 6;

/* ------------------------------------------------------------------ 基础图元 */

function fillRect(ctx, x, y, w, h, c) {
  if (w <= 0 || h <= 0) return;
  ctx.fillStyle = c;
  ctx.fillRect(x, y, w, h);
}

function fillHLine(ctx, x, y, w, c) {
  fillRect(ctx, x, y, w, 1, c);
}

function fillCircle(ctx, cx, cy, r, c) {
  if (r <= 0) return;
  ctx.fillStyle = c;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
}

function fillRoundRect(ctx, x, y, w, h, r, c) {
  if (w <= 0 || h <= 0) return;
  r = Math.min(r, Math.floor(w / 2), Math.floor(h / 2));
  if (r <= 0) {
    fillRect(ctx, x, y, w, h, c);
    return;
  }
  ctx.fillStyle = c;
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") {
    ctx.roundRect(x, y, w, h, r);
  } else {
    const a = Math.PI / 2;
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y); ctx.arc(x + w - r, y + r, r, -a, 0);
    ctx.lineTo(x + w, y + h - r); ctx.arc(x + w - r, y + h - r, r, 0, a);
    ctx.lineTo(x + r, y + h); ctx.arc(x + r, y + h - r, r, a, 2 * a);
    ctx.lineTo(x, y + r); ctx.arc(x + r, y + r, r, 2 * a, 3 * a);
    ctx.closePath();
  }
  ctx.fill();
}

/* ------------------------------------------------------------------ 文本 */

function textWidth(s, scale) {
  if (!s) return 0;
  return s.length * CHAR_ADV * scale - scale;
}

function drawText(ctx, x, y, scale, c, s) {
  if (!s || scale < 1) return 0;
  ctx.fillStyle = c;
  let cx = x;
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i);
    if (code >= 0x20 && code <= 0x7F) {
      const g = FONT[code - 0x20];
      for (let col = 0; col < FONT_W; col++) {
        const bits = g[col];
        if (!bits) continue;
        for (let row = 0; row < FONT_H; row++) {
          if (bits & (1 << row)) {
            ctx.fillRect(cx + col * scale, y + row * scale, scale, scale);
          }
        }
      }
    }
    cx += CHAR_ADV * scale;
  }
  return cx - x;
}

function drawTextRight(ctx, xRight, y, scale, c, s) {
  drawText(ctx, xRight - textWidth(s, scale), y, scale, c, s);
}

/* ------------------------------------------------------------------ 7 段 */

function segH(ctx, x, yc, len, t, c) {
  fillRoundRect(ctx, x, yc - Math.floor(t / 2), len, t, Math.floor(t / 2), c);
}

function segV(ctx, xc, y, len, t, c) {
  fillRoundRect(ctx, xc - Math.floor(t / 2), y, t, len, Math.floor(t / 2), c);
}

function drawDigit(ctx, x, y, w, h, seg, on) {
  const t = Math.max(2, Math.floor(w * 15 / 100));
  const hh = Math.floor(h / 2);
  const hl = w - t;
  const vl = hh - t;
  const t2 = Math.floor(t / 2);

  if (seg & SEG_A) segH(ctx, x + t2, y + t2, hl, t, on);
  if (seg & SEG_G) segH(ctx, x + t2, y + hh, hl, t, on);
  if (seg & SEG_D) segH(ctx, x + t2, y + h - t2, hl, t, on);
  if (seg & SEG_F) segV(ctx, x + t2, y + t2, vl, t, on);
  if (seg & SEG_B) segV(ctx, x + w - t2, y + t2, vl, t, on);
  if (seg & SEG_E) segV(ctx, x + t2, y + hh + t2, vl, t, on);
  if (seg & SEG_C) segV(ctx, x + w - t2, y + hh + t2, vl, t, on);
}

function drawDigitsRight(ctx, xRight, y, w, h, gap, on, digits) {
  const n = digits.length;
  if (n === 0) return xRight;
  const total = n * w + (n - 1) * gap;
  const x = xRight - total;
  for (let i = 0; i < n; i++) {
    const ch = digits[i];
    if (ch >= "0" && ch <= "9") {
      drawDigit(ctx, x + i * (w + gap), y, w, h, DIGIT_SEG[ch.charCodeAt(0) - 48], on);
    }
  }
  return x;
}

/* ------------------------------------------------------------------ 布局 */

function makeLayout(W, H) {
  const k = Math.min(W / DW, H / DH) || 1;
  const L = {
    W, H, k,
    ox: Math.floor((W - Math.floor(DW * k)) / 2),
    oy: Math.floor((H - Math.floor(DH * k)) / 2),
  };
  const sc = v => Math.floor(v * L.k + 0.5);
  const scl = v => Math.max(1, Math.floor(v * L.k + 0.5));
  L.sc = sc; L.scl = scl;

  L.m = sc(48);
  L.hdr_y = sc(30);
  L.hdr_scale = scl(5);
  L.hdr_sep_y = sc(92);
  L.foot_y = sc(1000);
  L.foot_scale = scl(3);

  L.card_y = sc(130);
  L.card_h = sc(840);
  L.card_w = sc(576);
  L.card_r = sc(26);
  L.pad = sc(40);
  L.card_x = [sc(48), sc(48) + (sc(576) + sc(48)), sc(48) + 2 * (sc(576) + sc(48))];

  L.title_y = sc(36);
  L.title_scale = scl(5);
  L.tag_y = sc(42);
  L.tag_scale = scl(3);

  L.num_y = sc(150);
  L.num_h = sc(250);
  L.num_w = sc(132);
  L.num_gap = sc(14);
  L.pct_scale = scl(8);
  L.pct_gap = sc(24);

  L.temp_y = sc(490);
  L.temp_h = sc(150);
  L.temp_w = sc(74);
  L.temp_gap = sc(4);
  L.temp_label_scale = scl(4);
  L.temp_dot = sc(8);
  L.unit_scale = scl(5);

  L.bar_y = sc(690);
  L.bar_h = sc(26);
  L.foot2_y = sc(762);
  L.foot2_scale = scl(3);

  return L;
}

/* ------------------------------------------------------------------ 卡片 */

function loadColor(accent, load) {
  if (load >= 90) return COL.red;
  if (load >= 70) return COL.amber;
  return accent;
}

function secondaryLabel(i) { return i === 1 ? "FREE" : "TEMP"; }
function secondaryUnit(i) { return i === 1 ? "GB" : "\x7fC"; }

function secondaryValue(i, d) {
  if (!d) return null;
  if (i === 0) {
    const v = d.cpu_temp_c10;
    return (v && v > 0) ? v : null;
  }
  if (i === 1) {
    if (!d.mem_total) return null;
    const free = Math.max(0, d.mem_total - d.mem_used);
    return Math.floor(free * 10 / 1024);
  }
  const v = d.gpu_temp_c10;
  return (v && v > 0) ? v : null;
}

function drawValueX10(ctx, numRight, y, w, h, gap, dotW, color, v10) {
  v10 = Math.max(0, v10);
  const whole = Math.floor(v10 / 10);
  const frac = v10 % 10;
  const ip = String(whole);

  let x = numRight;
  drawDigitsRight(ctx, x, y, w, h, gap, color, String(frac));
  x -= w;
  const dotGap = gap + Math.floor(dotW / 2);
  const dotX = x - dotGap - dotW;
  fillRoundRect(ctx, dotX, y + h - dotW - gap, dotW, dotW, Math.floor(dotW / 2), color);
  x = dotX - dotGap;
  drawDigitsRight(ctx, x, y, w, h, gap, color, ip);
  x -= ip.length * w + (ip.length - 1) * gap;
  return x;
}

function drawCardFrame(ctx, L, i) {
  const x = L.ox + L.card_x[i];
  const y = L.oy + L.card_y;
  fillRoundRect(ctx, x, y, L.card_w, L.card_h, L.card_r, COL.cardEdge);
  fillRoundRect(ctx, x + 2, y + 2, L.card_w - 4, L.card_h - 4,
                Math.max(0, L.card_r - 2), COL.card);
}

function drawCardStatic(ctx, L, i) {
  const cx = L.ox + L.card_x[i];
  const cy = L.oy + L.card_y;
  const card = CARDS[i];

  drawCardFrame(ctx, L, i);
  drawText(ctx, cx + L.pad, cy + L.title_y, L.title_scale, card.accent, card.title);
  drawTextRight(ctx, cx + L.card_w - L.pad, cy + L.tag_y, L.tag_scale,
                COL.dimmer, card.tag);

  const sepY = cy + L.title_y + L.title_scale * FONT_H + L.sc(14);
  fillHLine(ctx, cx + L.pad, sepY, L.card_w - 2 * L.pad, COL.track);
  fillHLine(ctx, cx + L.pad, cy + L.sc(452), L.card_w - 2 * L.pad, COL.track);

  drawText(ctx, cx + L.pad,
           cy + L.temp_y + Math.floor((L.temp_h - L.temp_label_scale * FONT_H) / 2),
           L.temp_label_scale, COL.dim, secondaryLabel(i));

  fillRoundRect(ctx, cx + L.pad, cy + L.bar_y, L.card_w - 2 * L.pad,
                L.bar_h, Math.floor(L.bar_h / 2), COL.track);
}

function footnote(i, d) {
  if (!d) return "--";
  if (i === 0) {
    return d.cpu_clk ? (d.cpu_clk / 1000).toFixed(2) + " GHZ" : "--";
  }
  if (i === 1) {
    return d.mem_total
      ? (d.mem_used / 1024).toFixed(1) + " / " + (d.mem_total / 1024).toFixed(1) + " GB"
      : "--";
  }
  if (d.gpu_used || d.gpu_power) {
    return (d.gpu_used / 1024).toFixed(1) + " GB  " + Math.round(d.gpu_power) + " W";
  }
  return "--";
}

function drawCardValues(ctx, L, i, d, linkOk) {
  const cx = L.ox + L.card_x[i];
  const cy = L.oy + L.card_y;
  const right = cx + L.card_w - L.pad;

  let load = 0;
  if (d) load = [d.cpu_load, d.mem_load, d.gpu_load][i] || 0;
  load = Math.max(0, Math.min(100, load));

  let accent = loadColor(CARDS[i].accent, load);
  if (!linkOk) accent = COL.dimmer;

  /* 大号百分比 */
  const pctW = textWidth("%", L.pct_scale);
  const numRight = right - pctW - L.pct_gap;
  const digits = (linkOk && d) ? String(load) : "--";
  drawDigitsRight(ctx, numRight, cy + L.num_y, L.num_w, L.num_h, L.num_gap, accent, digits);
  const pctY = cy + L.num_y + L.num_h - L.pct_scale * FONT_H - L.sc(10);
  drawText(ctx, numRight + L.pct_gap, pctY, L.pct_scale, accent, "%");

  /* 副数值：温度 / 剩余内存 */
  const v10 = linkOk ? secondaryValue(i, d) : null;
  const unit = secondaryUnit(i);
  const unitW = textWidth(unit, L.unit_scale);
  const numRight2 = right - unitW - L.sc(14);
  const uy = cy + L.temp_y + L.temp_h - L.unit_scale * FONT_H - L.sc(6);

  if (v10 !== null) {
    drawValueX10(ctx, numRight2, cy + L.temp_y, L.temp_w, L.temp_h,
                 L.temp_gap, L.temp_dot, accent, v10);
    drawText(ctx, right - unitW, uy, L.unit_scale, COL.dim, unit);
  } else {
    /* 没有数据时画 "--"，和单位同一基线 */
    const dashScale = L.unit_scale * 2;
    drawTextRight(ctx, numRight2,
                  uy + L.unit_scale * FONT_H - dashScale * FONT_H,
                  dashScale, COL.dimmer, "--");
    drawText(ctx, right - unitW, uy, L.unit_scale, COL.dimmer, unit);
  }

  /* 进度条 */
  const barX = cx + L.pad;
  const barW = L.card_w - 2 * L.pad;
  fillRoundRect(ctx, barX, cy + L.bar_y, barW, L.bar_h,
                Math.floor(L.bar_h / 2), COL.track);
  let fillW = Math.floor(barW * load / 100);
  if (fillW > 0 && linkOk) {
    fillW = Math.max(fillW, L.bar_h);
    fillRoundRect(ctx, barX, cy + L.bar_y, fillW, L.bar_h,
                  Math.floor(L.bar_h / 2), accent);
  }

  /* 底部小字 */
  drawText(ctx, cx + L.pad, cy + L.foot2_y, L.foot2_scale, COL.dim,
           footnote(i, linkOk ? d : null));
}

/* ------------------------------------------------------------------ 页头页脚 */

function drawHeader(ctx, L, d, linkOk) {
  const y = L.oy + L.hdr_y;
  const left = L.ox + L.m;
  const right = L.ox + L.sc(1920) - L.m;

  drawText(ctx, left, y, L.hdr_scale, COL.text, "SYSTEM MONITOR");

  const stScale = L.scl(4);
  const st = linkOk ? "LINK OK" : "NO SIGNAL";
  const stCol = linkOk ? COL.green : COL.red;
  const stW = textWidth(st, stScale);
  const dotR = L.sc(6);
  const stY = L.oy + L.hdr_y + Math.floor((L.hdr_scale - stScale) * FONT_H / 2) + L.sc(2);

  fillCircle(ctx, right - stW - dotR * 3, stY + Math.floor(stScale * FONT_H / 2), dotR, stCol);
  drawText(ctx, right - stW, stY, stScale, stCol, st);

  if (d) {
    drawTextRight(ctx, right - stW - dotR * 6, y, L.scl(3), COL.dimmer, "SEQ " + d.seq);
  }

  fillHLine(ctx, left, L.oy + L.hdr_sep_y, L.sc(1920) - 2 * L.m, COL.track);
}

function drawFooter(ctx, L, linkOk) {
  const y = L.oy + L.foot_y;
  const left = L.ox + L.m;
  const right = L.ox + L.sc(1920) - L.m;

  drawText(ctx, left, y, L.foot_scale, COL.dimmer,
           linkOk ? "PC LINK  LIVE" : "WAITING FOR DATA");
  drawTextRight(ctx, right, y, L.foot_scale, COL.dimmer,
                "SYSTEM MONITOR  " + L.W + "x" + L.H);
}

/* ------------------------------------------------------------------ 渲染 */

function render(ctx, d, linkOk, W, H) {
  const L = makeLayout(W, H);
  fillRect(ctx, 0, 0, W, H, COL.bg);
  drawHeader(ctx, L, d, linkOk);
  for (let i = 0; i < 3; i++) drawCardStatic(ctx, L, i);
  drawFooter(ctx, L, linkOk);
  for (let i = 0; i < 3; i++) drawCardValues(ctx, L, i, d, linkOk);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { render, makeLayout, COL, CARDS };
}

#!/usr/bin/env node
/**
 * 用 Node 跑 dashboard/render.js 的像素校验：
 * 自己实现一个最小 canvas 2D 上下文（只有 fillRect / roundRect / arc / fill），
 * 硬边、无抗锯齿，输出 PPM，再用 Python 转成 PNG 和 tools/preview_*.png 对照。
 *
 *   node tools/render_check.js 1920 1080 tools/js_1920x1080.ppm
 *   node tools/render_check.js 1024 600  tools/js_1024x600.ppm
 */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");

global.MON_FONT5X7 = require(path.join(ROOT, "dashboard", "font.js"));
const { render } = require(path.join(ROOT, "dashboard", "render.js"));

function parseColor(css) {
  const m = /^#([0-9a-fA-F]{6})$/.exec(css);
  if (!m) throw new Error("unsupported color " + css);
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
}

function isqrt(v) {
  if (v <= 0) return 0;
  let x = v, y = Math.floor((x + 1) / 2);
  while (y < x) { x = y; y = Math.floor((x + v / x) / 2); }
  return x;
}

class Ctx {
  constructor(w, h) {
    this.w = w;
    this.h = h;
    this.buf = new Uint8Array(w * h * 3);
    this.fillStyle = "#000000";
    this.path = [];
  }

  _rect(x, y, w, h, rgb) {
    let x0 = Math.max(0, Math.round(x));
    let y0 = Math.max(0, Math.round(y));
    let x1 = Math.min(this.w, Math.round(x + w));
    let y1 = Math.min(this.h, Math.round(y + h));
    for (let yy = y0; yy < y1; yy++) {
      let o = (yy * this.w + x0) * 3;
      for (let xx = x0; xx < x1; xx++) {
        this.buf[o++] = rgb[0];
        this.buf[o++] = rgb[1];
        this.buf[o++] = rgb[2];
      }
    }
  }

  _circle(cx, cy, r, rgb) {
    if (r <= 0) return;
    for (let dy = -r; dy <= r; dy++) {
      const dx = isqrt(r * r - dy * dy);
      this._rect(cx - dx, cy + dy, dx * 2 + 1, 1, rgb);
    }
  }

  _roundRect(s, rgb) {
    const w = Math.round(s.w), h = Math.round(s.h);
    const x = Math.round(s.x), y = Math.round(s.y);
    let r = Math.min(Math.round(s.r), Math.floor(w / 2), Math.floor(h / 2));
    if (w <= 0 || h <= 0) return;
    if (r <= 0) { this._rect(x, y, w, h, rgb); return; }
    this._rect(x + r, y, w - 2 * r, h, rgb);
    this._rect(x, y + r, r, h - 2 * r, rgb);
    this._rect(x + w - r, y + r, r, h - 2 * r, rgb);
    this._circle(x + r, y + r, r, rgb);
    this._circle(x + w - r - 1, y + r, r, rgb);
    this._circle(x + r, y + h - r - 1, r, rgb);
    this._circle(x + w - r - 1, y + h - r - 1, r, rgb);
  }

  fillRect(x, y, w, h) {
    this._rect(x, y, w, h, parseColor(this.fillStyle));
  }

  beginPath() { this.path = []; }
  moveTo() {}
  lineTo() {}
  closePath() {}

  roundRect(x, y, w, h, r) {
    this.path.push({ t: "rr", x, y, w, h, r });
  }

  arc(cx, cy, r) {
    this.path.push({ t: "c", cx, cy, r });
  }

  fill() {
    const rgb = parseColor(this.fillStyle);
    for (const s of this.path) {
      if (s.t === "rr") this._roundRect(s, rgb);
      else this._circle(Math.round(s.cx), Math.round(s.cy), Math.round(s.r), rgb);
    }
    this.path = [];
  }

  toPPM() {
    const head = Buffer.from(`P6\n${this.w} ${this.h}\n255\n`, "ascii");
    return Buffer.concat([head, Buffer.from(this.buf)]);
  }
}

const DEMO = {
  seq: 1234,
  cpu_load: 37, cpu_temp_c10: 612, cpu_clk: 3800,
  mem_load: 62, mem_used: 12800, mem_total: 32768,
  gpu_load: 88, gpu_temp_c10: 735, gpu_used: 5120, gpu_total: 16384, gpu_power: 118.0,
  alive: true,
};

const W = parseInt(process.argv[2] || "1920", 10);
const H = parseInt(process.argv[3] || "1080", 10);
const out = process.argv[4] || path.join(ROOT, "tools", `js_${W}x${H}.ppm`);
const blank = process.argv.includes("--blank");

/* --json '{"cpu_load":37,...}' 或 --jsonfile stats.json 用真实数据出图 */
let payload = DEMO;
const ji = process.argv.indexOf("--json");
const jf = process.argv.indexOf("--jsonfile");
if (ji > 0 && process.argv[ji + 1]) {
  payload = JSON.parse(process.argv[ji + 1]);
} else if (jf > 0 && process.argv[jf + 1]) {
  payload = JSON.parse(fs.readFileSync(process.argv[jf + 1], "utf-8"));
}

const ctx = new Ctx(W, H);
render(ctx, blank ? null : payload, !blank, W, H);
fs.writeFileSync(out, ctx.toPPM());
console.log(`wrote ${out}  ${W}x${H}`);

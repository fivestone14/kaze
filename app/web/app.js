// Fetches /latest.json and /bars.json every 30 s and draws the three views from them; nothing on screen comes from
// anywhere else. One number formatter, one clock formatter, one fetch, one render per block, status said plainly.
"use strict";

const CHANNELS = { "5_minutes": 5, "30_minutes": 30, "60_minutes": 60 };
const FRESH_SECONDS = 6 * 60;
const REFRESH_MS = 30000;
const PROMISED_SHARE = 0.8;
const OPTIONS_AGREE_RATIO = 1.25;
const FONT = "Plus Jakarta Sans, sans-serif";
const C = { ink: "#161A17", blue: "#2F44B8", rust: "#A8492F", green: "#2e9e5b", greenDeep: "#1f6b3e",
            faint: "#5C6459", sand: "#B9B2A4", band: "#D5D0C6", rule: "#E4E0D8", cream: "#F4F1EB",
            white: "#FFFFFF", grey: "#8a8a8a", track: "#C9D1F4" };
const PUSH_LABELS = { price_trend: "Price Trend", straightness: "Straightness", breadth: "Breadth",
                      big_tech_vs_rest: "Big Tech vs Rest", options_buyers: "Options Buyers",
                      yield_change: "10-Year Yield", mirai_claude: "Claude" };
const FEED_LABELS = { spx_bars: "SPX Bars", watchlist_quotes: "Quotes", nyse_breadth: "Breadth",
                      options_scan: "Options Scan", options_tape: "Options Tape", futures_bars: "Futures",
                      event_calendar: "Calendar", news_headlines: "News", claude_forecast: "Claude",
                      schwab_login: "Schwab Login" };
const DIAL_LABELS = { fed_window: "Fed", release_window: "News", closing_window: "Close",
                      acceleration: "Pace", options_vs_tape: "Options vs Tape" };
const state = { feed: null, bars: [], channel: "30_minutes", fetchedAt: null, unreachable: false, missing: false };

// ---- the one number formatter, the one clock formatter, small text helpers ------------------------------------------

function fmt(value, decimals = 0, signed = false) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const text = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: decimals,
                                                          maximumFractionDigits: decimals });
  if (value < 0 && Number(text.replace(/,/g, "")) !== 0) return "−" + text;
  return signed ? "+" + text : text;
}

function clock(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}m`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function titleCase(text) {
  return String(text).replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function plusMinus(value, decimals = 1) {
  return value === null || value === undefined ? "—" : `±${fmt(value, decimals)}`;
}

function el(id) {
  return document.getElementById(id);
}

function note(id, text) {
  const node = el(id);
  node.hidden = !text;
  node.textContent = text || "";
}

function svgText(x, y, text, attrs = "") {
  return `<text x="${x}" y="${y}" font-family="${FONT}" ${attrs}>${text}</text>`;
}

function svgRect(x, y, width, height, rx, fill, opacity = 1, extra = "") {
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${rx}" fill="${fill}" `
       + `fill-opacity="${opacity}" ${extra}/>`;
}

function svgLine(x1, y1, x2, y2, stroke, width, extra = "") {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${width}" ${extra}/>`;
}

function svgPolyline(points, stroke, width, extra = "") {
  return `<polyline points="${points.join(" ")}" fill="none" stroke="${stroke}" stroke-width="${width}" ${extra}/>`;
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

// ---- what the feed holds, read one way -----------------------------------------------------------------------------

function range() {
  return state.feed && state.feed.range ? state.feed.range : null;
}

function channel() {
  const block = range();
  return block && block.channels ? block.channels[state.channel] : null;
}

function windows() {
  const block = range();
  return block && block.windows && block.windows.status !== "failed" ? block.windows : null;
}

function windowsFailure() {
  const block = range();
  return block && block.windows && block.windows.status === "failed" ? block.windows.reason : null;
}

function reasons() {
  const text = state.feed && state.feed.status_reason;
  return text ? text.split(",").filter(Boolean) : [];
}

function feedAgeSeconds() {
  const moment = state.feed && (state.feed.cut_at || state.feed.failed_at);
  return moment ? (Date.now() - new Date(moment).getTime()) / 1000 : null;
}

function emptyReason() {
  const feed = state.feed;
  if (!feed) return state.unreachable ? "Can't Reach The Mac" : "No Live Feed Yet";
  if (feed.status === "failed") return `Read Failed · ${feed.status_reason}`;
  if (!range()) return "Range Step Failed On This Read";
  if (windowsFailure()) return `Grading Failed · ${windowsFailure()}`;
  return "No Read Yet Today For This Window";
}

function valueOf(key) {
  const values = state.feed && state.feed.values;
  return values && values[key] !== undefined ? values[key] : null;
}

// ---- the fetch and the render -------------------------------------------------------------------------------------

async function loadFeed() {
  try {
    const get = (path) => fetch(location.origin + path, { cache: "no-store" });
    const [feedReply, barsReply] = await Promise.all([get("/latest.json"), get("/bars.json")]);
    state.missing = feedReply.status === 404;
    state.feed = feedReply.ok ? await feedReply.json() : null;
    state.bars = barsReply.ok ? (await barsReply.json()).bars : [];
    state.unreachable = false;
    state.fetchedAt = Date.now();
  } catch (failure) {
    state.unreachable = true;
  }
  render();
}

function render() {
  renderClock();
  renderNotice();
  renderSwitches();
  renderRead();
  renderSight();
  renderSteps();
}

function renderClock() {
  const feed = state.feed;
  const age = feedAgeSeconds();
  let text = "No Feed", stale = true;
  if (feed && feed.status === "failed") {
    text = `Failed · ${clock(feed.failed_at)}`;
  } else if (feed && age !== null && age > FRESH_SECONDS) {
    text = `Stale · ${clock(feed.cut_at)} · ${ago(age)} Ago`;
  } else if (feed) {
    text = `Live · ${clock(feed.cut_at)}`;
    stale = false;
  }
  document.querySelectorAll(".clock-text").forEach((node) => { node.textContent = text; });
  document.querySelectorAll(".clock-dot").forEach((node) => node.classList.toggle("stale", stale));
  const block = range();
  const priorClose = valueOf("prior_close_points");
  const change = block && priorClose ? (block.price - priorClose) / priorClose * 100 : null;
  el("price").textContent = block ? fmt(block.price, 2) : "—";
  el("change").textContent = change === null ? "—" : `${fmt(change, 2, true)}%`;
  el("change").className = "change " + (change === null ? "faint" : change < 0 ? "down" : "up");
}

function renderNotice() {
  const feed = state.feed;
  const lines = [];
  if (state.unreachable) {
    lines.push(`Can't Reach The Mac${state.fetchedAt ? " · Showing " + clock(state.fetchedAt) : ""}`);
  }
  if (state.missing) lines.push("No Live Feed Yet · The Engine Has Not Written One");
  if (feed && feed.status === "failed") {
    lines.push(`Read Failed At ${clock(feed.failed_at)} · ${feed.status_reason}`);
  } else {
    reasons().forEach((reason) => lines.push(`${titleCase(reason)} On This Read`));
  }
  if (windowsFailure()) lines.push(`Grading: ${windowsFailure()}`);
  note("notice", lines.join(" · "));
}

function renderSwitches() {
  document.querySelectorAll("[data-channel]").forEach((button) => {
    button.setAttribute("aria-selected", button.dataset.channel === state.channel ? "true" : "false");
  });
}

// ---- Read: the range line, the chart, the strip, the list, the scores ---------------------------------------------

function renderRead() {
  const block = windows();
  const current = block ? block.current[state.channel] : null;
  const finished = block ? block.finished[state.channel] : [];
  const minutes = CHANNELS[state.channel];
  const band = current && current.bands["80"];
  el("range-line").textContent = band ? `${fmt(band.low)} – ${fmt(band.high)}` : "—";
  el("held").textContent = current ? `${current.minutes_held} / ${minutes} Min` : `— / ${minutes} Min`;
  renderChart(current, finished, minutes);
  renderStrip(current);
  renderReadsList(current, finished);
  renderScores(block ? block.score[state.channel] : null);
}

function renderChart(current, finished, minutes) {
  const chart = el("chart");
  const anchorEnd = current ? current.ends_at : finished[0] ? finished[0].ends_at : null;
  note("chart-note", anchorEnd ? "" : emptyReason());
  chart.innerHTML = "";
  if (!anchorEnd) return;
  const width = 336, left = 4, right = 330, top = 14, bottom = 198;
  const pxPerMinute = 0.25 * (right - left) / minutes;
  const endMs = new Date(anchorEnd).getTime();
  const startMs = endMs - (right - left) / pxPerMinute * 60000;
  const ms = (iso) => new Date(iso).getTime();
  const x = (iso) => left + (ms(iso) - startMs) / 60000 * pxPerMinute;
  const boxes = finished.filter((w) => ms(w.window_from) >= startMs && w.half_width !== null);
  const bars = state.bars.filter((bar) => ms(bar.at) >= startMs && ms(bar.at) <= endMs);
  const liveHalf = current ? current.bands["80"].half_width : null;
  const prices = [];
  boxes.forEach((w) => prices.push(w.price + w.half_width, w.price - w.half_width, w.high, w.low));
  bars.forEach((bar) => prices.push(bar.close));
  if (liveHalf !== null) prices.push(current.price + liveHalf, current.price - liveHalf);
  const known = prices.filter((p) => p !== null && p !== undefined);
  if (!known.length) return;
  const pad = (Math.max(...known) - Math.min(...known)) * 0.06 || 1;
  const high = Math.max(...known) + pad, low = Math.min(...known) - pad;
  const y = (price) => top + (high - price) / (high - low) * (bottom - top);
  const parts = boxes.map((w) => readBox(w, x, y));
  if (liveHalf !== null) parts.push(liveCone(current, x, y));
  parts.push(priceLines(current, bars, x, y), axisLabels(current, boxes, bars, x, width));
  chart.innerHTML = parts.join("");
}

function readBox(w, x, y) {
  const x0 = x(w.window_from), x1 = x(w.ends_at);
  const y0 = y(w.price + w.half_width), y1 = y(w.price - w.half_width), yRead = y(w.price);
  const inside = w.grades.ends_inside;
  const colour = inside === null ? C.grey : inside ? C.green : C.rust;
  const gaugeX = x1 - 10;
  const parts = [svgRect(x0 + 1, y0, x1 - x0 - 2, y1 - y0, 6, C.ink, 0.045),
                 svgRect(gaugeX, y0 + 20, 5, Math.max(0, y1 - y0 - 26), 2.5, C.ink, 0.08)];
  if (w.end !== null) {
    const yEnd = y(w.end);
    const usedTop = Math.min(yRead, yEnd), usedBottom = Math.max(yRead, yEnd);
    parts.push(svgRect(gaugeX, usedTop, 5, usedBottom - usedTop, 2.5, colour, 0.75),
               svgLine(gaugeX - 3, yEnd, gaugeX + 8, yEnd, C.ink, 1.6));
  }
  const cx = gaugeX + 2.5, cy = y0 + 8;
  parts.push(`<circle cx="${cx}" cy="${cy}" r="6" fill="${colour}"/>`);
  if (inside === true) {
    parts.push(`<path d="M${cx - 3.5} ${cy}l2.5 2.5 4-4.5" fill="none" stroke="${C.white}" stroke-width="1.8" `
               + `stroke-linecap="round" stroke-linejoin="round"/>`);
  } else if (inside === false) {
    parts.push(`<path d="M${cx - 3} ${cy - 3}l6 6M${cx + 3} ${cy - 3}l-6 6" stroke="${C.white}" `
               + `stroke-width="1.8" stroke-linecap="round"/>`);
  }
  return parts.join("");
}

function liveCone(current, x, y) {
  const xr = x(current.window_from), xe = x(current.ends_at);
  const cone = (half, opacity) => {
    const upper = [], lower = [];
    for (let step = 0; step <= 4; step += 1) {
      const t = step / 4, px = xr + t * (xe - xr), reach = half * Math.sqrt(t);
      upper.push(`${px.toFixed(1)},${y(current.price + reach).toFixed(1)}`);
      lower.unshift(`${px.toFixed(1)},${y(current.price - reach).toFixed(1)}`);
    }
    return `<polygon points="${upper.concat(lower).join(" ")}" fill="${C.blue}" fill-opacity="${opacity}"/>`;
  };
  const endHalf = current.bands["80"].half_width, averageHalf = current.bands.average_80.half_width;
  const label = `font-size="10" text-anchor="end" fill="${C.blue}"`;
  return cone(endHalf, 0.12) + (averageHalf !== null ? cone(averageHalf, 0.2) : "")
       + svgText(xe, y(current.price + endHalf) - 4, fmt(current.price + endHalf), label)
       + svgText(xe, y(current.price - endHalf) + 11, fmt(current.price - endHalf), label);
}

function priceLines(current, bars, x, y) {
  if (!bars.length) return "";
  const fromMs = current ? new Date(current.window_from).getTime() : Infinity;
  const before = bars.filter((bar) => new Date(bar.at).getTime() < fromMs);
  const after = bars.filter((bar) => new Date(bar.at).getTime() >= fromMs);
  const point = (bar) => `${x(bar.at).toFixed(1)},${y(bar.close).toFixed(1)}`;
  const round = `stroke-linejoin="round" stroke-linecap="round"`;
  const parts = [];
  if (before.length) parts.push(svgPolyline(before.map(point), C.ink, 2, round));
  const last = bars[bars.length - 1];
  const lastX = x(last.at), lastY = y(last.close);
  if (current && after.length) {
    const start = `${x(current.read_at).toFixed(1)},${y(current.price).toFixed(1)}`;
    const averages = [];
    let sum = 0;
    after.forEach((bar, index) => {
      sum += bar.close;
      averages.push(`${x(bar.at).toFixed(1)},${y(sum / (index + 1)).toFixed(1)}`);
    });
    parts.push(svgPolyline([start].concat(after.map(point)), C.blue, 2.6, round),
               svgPolyline([start].concat(averages), C.ink, 1.8, `stroke-dasharray="4 2.5"`),
               `<circle cx="${lastX}" cy="${lastY}" r="9" fill="${C.blue}" fill-opacity="0.15"/>`,
               `<circle cx="${lastX}" cy="${lastY}" r="5" fill="${C.blue}" stroke="${C.cream}" stroke-width="2"/>`,
               `<circle cx="${lastX}" cy="${y(sum / after.length)}" r="4.5" fill="${C.cream}" stroke="${C.ink}" `
               + `stroke-width="2"/>`);
  } else {
    parts.push(`<circle cx="${lastX}" cy="${lastY}" r="5" fill="${C.blue}" stroke="${C.cream}" stroke-width="2"/>`);
  }
  return parts.join("");
}

function axisLabels(current, boxes, bars, x, width) {
  const parts = boxes.map((w) => svgText((x(w.window_from) + x(w.ends_at)) / 2, 213, clock(w.read_at),
                                         `font-size="10" text-anchor="middle" fill="${C.faint}"`));
  const last = bars[bars.length - 1];
  const nowX = clamp(last ? x(last.at) : current ? x(current.read_at) : width - 12, 12, width - 12);
  parts.push(svgText(nowX, 213, "Now", `font-size="10" text-anchor="middle" fill="${C.ink}" font-weight="700"`));
  return parts.join("");
}

function renderStrip(current) {
  const strip = el("strip");
  const verdict = el("strip-verdict");
  const endHalf = current && current.bands["80"].half_width;
  if (!endHalf) {
    strip.innerHTML = "";
    verdict.textContent = "—";
    verdict.className = "faint";
    return;
  }
  const averageHalf = current.bands.average_80.half_width;
  const centre = 156, scale = 110.9 / endHalf, edgeLeft = centre - 110.9, edgeRight = centre + 110.9;
  const at = (move) => clamp(centre + move * scale, 6, 306);
  const move = current.now.move, averageMove = current.now.average_move;
  const inside = current.so_far.average_inside;
  verdict.textContent = inside === null ? "—" : inside ? "Inside ✓" : "Outside ✗";
  verdict.className = inside === null ? "faint" : inside ? "inside" : "outside";
  const liveX = at(move || 0), averageX = averageMove === null ? null : at(averageMove);
  const liveLeft = averageX === null || liveX <= averageX;
  const small = `font-size="10" text-anchor="middle"`;
  const bold = `font-size="10" font-weight="700"`;
  const parts = [
    svgText(liveX + (liveLeft ? 3 : -3), 10, `Live ${fmt(move, 1, true)}`,
            `${bold} text-anchor="${liveLeft ? "end" : "start"}" fill="${C.blue}"`),
    svgLine(0, 32, 312, 32, C.sand, 1),
  ];
  if (averageHalf !== null) {
    parts.push(svgRect(centre - averageHalf * scale, 21, 2 * averageHalf * scale, 22, 5, C.band),
               svgText(centre - averageHalf * scale, 60, fmt(-averageHalf, 1), `${small} fill="${C.faint}"`),
               svgText(centre + averageHalf * scale, 60, fmt(averageHalf, 1, true), `${small} fill="${C.faint}"`));
  }
  const dashed = `stroke-dasharray="3 2"`;
  parts.push(svgLine(edgeLeft, 20, edgeLeft, 44, C.rust, 1.5, dashed),
             svgLine(edgeRight, 20, edgeRight, 44, C.rust, 1.5, dashed),
             svgLine(centre, 17, centre, 47, C.ink, 2.5), svgLine(liveX, 15, liveX, 49, C.blue, 3));
  if (averageX !== null) {
    parts.push(svgText(averageX + (liveLeft ? -4 : 4), 10, `Average ${fmt(averageMove, 1, true)}`,
                       `${bold} text-anchor="${liveLeft ? "start" : "end"}" fill="${C.ink}"`),
               `<circle cx="${averageX}" cy="32" r="6" fill="${C.cream}" stroke="${C.ink}" stroke-width="2" `
               + `${dashed}/>`);
  }
  parts.push(svgText(centre, 60, `Read ${fmt(current.price, 1)}`, `${bold} text-anchor="middle" fill="${C.ink}"`),
             svgText(edgeLeft, 60, fmt(-endHalf, 1), `${small} fill="${C.rust}"`),
             svgText(edgeRight, 60, fmt(endHalf, 1, true), `${small} fill="${C.rust}"`));
  strip.innerHTML = parts.join("");
}

function renderReadsList(current, finished) {
  const rows = [];
  if (current && current.bands["80"].half_width !== null) {
    rows.push(readRow(current.read_at, current.bands["80"].half_width, current.now.farthest_up,
                      current.now.farthest_down, current.now.move, "Live", "live"));
  }
  finished.forEach((w) => {
    const inside = w.grades.ends_inside;
    const word = inside === null ? "Pending" : inside ? "Inside" : "Outside";
    rows.push(readRow(w.read_at, w.half_width, w.farthest_up, w.farthest_down, w.end_move, word,
                      inside === null ? "faint" : inside ? "inside" : "outside"));
  });
  el("reads-list").innerHTML = rows.join("") || `<p class="block-note">${emptyReason()}</p>`;
}

function readRow(readAt, half, up, down, endMove, word, kind) {
  const colour = kind === "live" ? C.blue : kind === "inside" ? C.green : kind === "outside" ? C.rust : C.grey;
  const scale = half ? 40 / half : 0;
  const spanLeft = clamp(60 - (down || 0) * scale, 2, 118), spanRight = clamp(60 + (up || 0) * scale, 2, 118);
  const bar = [svgRect(20, 2, 80, 6, 3, C.track)];
  if (up !== null && down !== null && half) {
    const innerLeft = Math.max(spanLeft, 20), innerRight = Math.min(spanRight, 100);
    if (innerRight > innerLeft) bar.push(svgRect(innerLeft, 3.5, innerRight - innerLeft, 3, 1.5, colour, 0.45));
    if (spanLeft < 20) bar.push(svgRect(spanLeft, 3.5, 20 - spanLeft, 3, 1.5, colour, 0.6));
    if (spanRight > 100) bar.push(svgRect(100, 3.5, spanRight - 100, 3, 1.5, colour, 0.6));
  }
  if (endMove !== null && half) {
    bar.push(`<circle cx="${clamp(60 + endMove * scale, 5, 115)}" cy="5" r="3.5" fill="${colour}"/>`);
  }
  const travelled = up === null || down === null ? "—" : `${fmt(up, 0, true)} ${fmt(-down)}`;
  const span = half ? `${travelled} · ±${fmt(half)}` : "—";
  return `<div class="read-row"><span class="when ${kind === "live" ? "live" : ""}">${clock(readAt)}</span>`
       + `<svg viewBox="0 0 120 10" aria-hidden="true">${bar.join("")}</svg>`
       + `<span class="span">${span}</span><span class="word ${kind}">${word}</span></div>`;
}

function renderScores(score) {
  const block = channel();
  const targets = { ends_inside: PROMISED_SHARE, average_inside: PROMISED_SHARE,
                    never_left: block ? block.stays_inside_share : null };
  const labels = { ends_inside: "Ends Inside", average_inside: "Average Inside", never_left: "Never Left" };
  el("scores").innerHTML = Object.keys(labels).map((word) => {
    const counts = score && score[word];
    const graded = counts ? counts.graded : 0;
    const good = graded && targets[word] !== null ? counts.yes / graded >= targets[word] : null;
    const colour = good === null ? "faint" : good ? "inside" : "outside";
    return `<div class="score"><b class="${colour}">${graded ? counts.yes : "—"}<small>/${graded}</small></b>`
         + `<span>${labels[word]}</span></div>`;
  }).join("");
}

// ---- Sight: how far, which way, feeds ------------------------------------------------------------------------------

function renderSight() {
  const block = range(), ch = channel();
  const minutes = CHANNELS[state.channel];
  note("sight-note", block && ch ? "" : emptyReason());
  renderSpeedBars(block);
  el("speed-value").textContent = block ? fmt(block.speed, 2) : "—";
  renderChain(block, ch, minutes);
  renderRangeBar(block, ch);
  renderSightChecks(block, ch);
  renderPush(block, ch);
  renderFeeds();
}

function renderSpeedBars(block) {
  const bars = block ? [["Fast · 5 Min", block.speed_fast, 0.5], ["Used · 15 Min", block.speed, 0.98],
                        ["Slow · 30 Min", block.speed_slow, 0.5]] : [];
  const most = Math.max(...bars.map((bar) => bar[1] || 0), 0.01);
  const barHeight = (value) => (value === null ? 0 : 12 + 38 * value / most);
  const parts = bars.map(([label, value, opacity], index) => {
    const x = 8 + index * 84, height = barHeight(value), y = 50 - height;
    const inkOrWhite = opacity > 0.9 ? C.white : C.ink;
    return svgRect(x, y, 46, height, 5, C.blue, opacity)
         + svgText(x + 23, y + 12, fmt(value, 2),
                   `font-size="10" font-weight="700" text-anchor="middle" fill="${inkOrWhite}"`)
         + svgText(x + 23, 63, label, `font-size="9.5" text-anchor="middle" fill="${C.faint}"`);
  });
  if (block && block.speed !== null) {
    const usedY = 50 - barHeight(block.speed) - 3;
    parts.push(svgLine(0, usedY, 230, usedY, C.ink, 1.4, `stroke-dasharray="4 3"`));
  }
  el("speed-bars").innerHTML = parts.join("");
}

function renderChain(block, ch, minutes) {
  const half = ch && ch.bands["80"].half_width;
  el("chain").innerHTML = `<span class="box">${block ? fmt(block.speed, 2) : "—"}</span>`
    + `<span class="arrow">× √${minutes} →</span><span class="box">${plusMinus(ch && ch.swing)} Swing</span>`
    + `<span class="arrow">× ${ch ? fmt(ch.stretch, 2) : "—"} →</span>`
    + `<span class="box box-blue">${plusMinus(half)}</span>`;
}

function renderRangeBar(block, ch) {
  const half = ch && ch.bands["80"].half_width;
  if (!half) {
    el("range-bar").innerHTML = "";
    return;
  }
  const centre = 156, scale = 136.7 / half, averageHalf = ch.bands.average_80.half_width, rung = ch.rung_points;
  const bold = `font-size="10" font-weight="700" text-anchor="middle"`;
  const parts = [svgRect(centre - half * scale, 8, 2 * half * scale, 20, 6, C.blue, 0.14)];
  if (averageHalf !== null) {
    parts.push(svgRect(centre - averageHalf * scale, 8, 2 * averageHalf * scale, 20, 5, C.blue, 0.22),
               svgText(centre - averageHalf * scale + 3, 22, `Avg ${plusMinus(averageHalf)}`,
                       `font-size="9.5" fill="${C.blue}"`));
  }
  const rungLeft = centre - rung * scale, rungRight = clamp(centre + rung * scale, 0, 312);
  const dashed = `stroke-dasharray="2 2"`;
  parts.push(svgLine(rungLeft, 4, rungLeft, 32, C.ink, 1, dashed),
             svgLine(rungRight, 4, rungRight, 32, C.ink, 1, dashed),
             svgLine(centre, 3, centre, 33, C.ink, 2.5),
             svgText(centre - half * scale, 45, fmt(ch.bands["80"].low), `${bold} fill="${C.blue}"`),
             svgText(centre, 45, `Read ${fmt(block.price)}`, `${bold} fill="${C.ink}"`),
             svgText(centre + half * scale, 45, fmt(ch.bands["80"].high), `${bold} fill="${C.blue}"`),
             svgText(Math.min(rungRight + 3, 290), 22, `±${rung}`, `font-size="9.5" fill="${C.ink}"`));
  el("range-bar").innerHTML = parts.join("");
}

function optionsAgree(ch) {
  return ch && ch.options_over_tape !== null
         && ch.options_over_tape <= OPTIONS_AGREE_RATIO && ch.options_over_tape >= 1 / OPTIONS_AGREE_RATIO;
}

function newsCheck(block, ch) {
  const dial = ch && ch.dials && ch.dials.release_window;
  const next = block && block.releases && block.releases.length ? block.releases[0] : null;
  const quiet = !next && (!dial || dial.raw === 1);
  const name = next ? `${titleCase(next.name)} At ${clock(next.at)}`
             : quiet ? "No News Left Today" : "News In The Window";
  return { name, value: dial ? `×${fmt(dial.applied, 2)}` : "—", good: quiet };
}

function checkRow(mark, name, value, cls) {
  return `<div class="check"><span class="name">${mark}${name}</span><span class="value ${cls}">${value}</span></div>`;
}

function markIcon(good) {
  return `<i class="mark ${good ? "mark-green" : "mark-amber"}">${good ? "✓" : "!"}</i>`;
}

function renderSightChecks(block, ch) {
  if (!ch) {
    el("sight-checks").innerHTML = "";
    return;
  }
  const agree = optionsAgree(ch);
  const news = newsCheck(block, ch);
  const share = ch.stays_inside_share;
  el("sight-checks").innerHTML =
    checkRow(markIcon(agree), agree ? "Options Agree" : "Options Differ",
             `${plusMinus(ch.options_swing)} vs ${plusMinus(ch.swing)}`, agree ? "inside" : "")
    + checkRow(markIcon(news.good), news.name, news.value, news.good ? "inside" : "")
    + checkRow(`<i class="dot-sand"></i>`, `Never Leaves ±${ch.rung_points}`,
               share === null ? "—" : `${fmt(share * 100)}% Of Past Windows`, "");
}

function pushValue(name) {
  const pct = (value) => (value === null ? null : `${fmt(value * 100)}%`);
  const advancing = pct(valueOf("nyse_advancing_portion")), bullish = pct(valueOf("bullish_portion_of_sided_premium"));
  const since = valueOf("move_points|since=prior_close");
  const straight = valueOf("trend_straightness_ratio|window_minutes=30");
  const qqq = valueOf("fund_change_from_prior_close_pct|symbol=QQQ");
  const rsp = valueOf("fund_change_from_prior_close_pct|symbol=RSP");
  const basisPoints = valueOf("ten_year_yield_change_basis_points");
  const up = valueOf("mirai_claude_forecast_pct|horizon=30_minutes&side=up");
  const down = valueOf("mirai_claude_forecast_pct|horizon=30_minutes&side=down");
  switch (name) {
    case "price_trend": return since === null ? null : `${fmt(since, 0, true)} Since Close`;
    case "straightness": return straight === null ? null : `${fmt(straight, 2)} Straight`;
    case "breadth": return advancing && `${advancing} Up`;
    case "big_tech_vs_rest": return qqq === null || rsp === null ? null : `${fmt(qqq - rsp, 1, true)}%`;
    case "options_buyers": return bullish && `${bullish} Bullish`;
    case "yield_change": return basisPoints === null ? null : `${fmt(basisPoints, 1, true)} bp`;
    case "mirai_claude": return up === null || down === null || !(up + down) ? null
                                : `${fmt(up / (up + down) * 100)}% Up`;
    default: return null;
  }
}

function leanMeter(lean, width = 86) {
  const mid = width / 2;
  const track = svgRect(0, 4, width, 4, 2, C.rule) + svgLine(mid, 1, mid, 11, C.faint, 1);
  if (lean === null || lean === undefined) return track;
  const x = clamp(lean * width, 4, width - 4);
  const colour = lean < 0.49 ? C.rust : lean > 0.51 ? C.blue : C.grey;
  const fill = Math.abs(x - mid) > 0.5 ? svgRect(Math.min(x, mid), 4, Math.abs(x - mid), 4, 2, colour, 0.45) : "";
  return track + fill + `<circle cx="${x}" cy="6" r="4" fill="${colour}"/>`;
}

function directionWords(direction) {
  if (!direction || direction.chance_up_if_moves === null) return "—";
  const text = direction.text === "coin flip until earned" ? "Coin Flip" : titleCase(direction.text);
  return `Up ${fmt(direction.chance_up_if_moves * 100)}% · ${text}`;
}

function renderPush(block, ch) {
  const push = block ? block.push : null;
  const direction = ch ? ch.direction : null;
  const weight = direction ? direction.trust_applied : null;
  el("push-weight").textContent = weight === null ? "—" : `Weight ${fmt(weight, weight ? 2 : 0)}`;
  const failed = reasons().includes("push_failed");
  const rows = (push || []).map((entry) => {
    const entryWeight = entry.weight_by_channel ? entry.weight_by_channel[state.channel] : 0;
    const value = entry.reason ? titleCase(entry.reason) : pushValue(entry.name) || entry.value;
    const label = PUSH_LABELS[entry.name] || titleCase(entry.name);
    return `<div class="push-row"><span>${label}${entryWeight ? ` · ${fmt(entryWeight, 2)}` : ""}</span>`
         + `<svg viewBox="0 0 86 12" aria-hidden="true">${leanMeter(entry.lean_if_trusted)}</svg>`
         + `<span class="value">${value}</span></div>`;
  });
  const empty = failed ? "Readings Failed On This Read" : block ? "No Readings On This Read" : emptyReason();
  el("push-rows").innerHTML = rows.join("") || `<p class="block-note">${empty}</p>`;
  el("push-rows").classList.toggle("earned", Boolean(weight));
  el("push-call-label").textContent = weight ? `Counted At Weight ${fmt(weight, 2)} →`
                                             : "Not Counted Until Earned →";
  el("push-call").textContent = directionWords(direction);
}

function renderFeeds() {
  const feeds = state.feed && state.feed.feeds ? state.feed.feeds : {};
  const names = Object.keys(feeds);
  const live = names.filter((name) => feeds[name].status === "live").length;
  el("feeds-pill").textContent = names.length ? `${live} Of ${names.length} Live` : "—";
  el("feeds-pill").className = "pill " + (live === names.length ? "pill-green" : "pill-purple");
  el("feeds-grid").innerHTML = names.map((name) => {
    const feed = feeds[name];
    const age = feed.status === "blind" ? "Blind" : feed.age_s === null ? titleCase(feed.status) : ago(feed.age_s);
    return `<div class="feed"><span class="name"><i class="feed-dot ${feed.status}"></i>`
         + `${FEED_LABELS[name] || titleCase(name)}</span><span class="age">${age}</span></div>`;
  }).join("");
  const login = state.feed && state.feed.login;
  if (!login || !login.state) {
    el("login-line").textContent = "Schwab Login · —";
  } else if (login.state === "ok" && login.expires_at) {
    const when = new Date(login.expires_at);
    const day = `${when.toLocaleDateString([], { weekday: "short" })} ${String(when.getMonth() + 1).padStart(2, "0")}`
              + `-${String(when.getDate()).padStart(2, "0")}`;
    el("login-line").textContent = `Schwab Login Good Until ${day} · ${fmt(login.hours_left)} H Left`;
  } else {
    el("login-line").textContent = `Schwab Login ${titleCase(login.state)}`;
  }
}

// ---- Steps: the build-up, the checks, the answer -------------------------------------------------------------------

function renderSteps() {
  const block = range(), ch = channel();
  const minutes = CHANNELS[state.channel];
  const capped = ch && ch.clipped ? " · Capped At The Close" : "";
  el("steps-sub").textContent = block ? `Read At ${clock(block.read_at)} · ${minutes}-Min Window${capped}` : "—";
  note("steps-note", block && ch ? "" : emptyReason());
  renderStepBars(block, ch, minutes);
  renderStepChecks(block, ch, minutes);
  renderAnswer(ch);
}

function tag(kind) {
  return `<span class="tag tag-${kind}">${titleCase(kind)}</span>`;
}

function stepBar(number, name, kind, value, sub, svg) {
  return `<div class="step"><div class="step-head"><span class="name">${number} · ${name}${tag(kind)}</span>`
       + `<span class="value">${value}</span></div><span class="faint-small">${sub}</span>${svg}</div>`;
}

function renderStepBars(block, ch, minutes) {
  const half = ch && ch.bands["80"].half_width;
  if (!half) {
    el("steps-bars").innerHTML = "";
    return;
  }
  const centre = 156, scale = 136.7 / half;
  const edge = (reach) => (centre - reach * scale).toFixed(1);
  const farEdge = (reach) => (centre + reach * scale).toFixed(1);
  const rect = (reach, opacity) => svgRect(edge(reach), 8, (2 * reach * scale).toFixed(1), 14, 5, C.blue, opacity);
  const centreLine = svgLine(centre, 5, centre, 25, C.ink, 1.6);
  const funnel = (from, to) =>
    `<path d="M${edge(from)} 0 L${edge(to)} 8 M${farEdge(from)} 0 L${farEdge(to)} 8" stroke="${C.sand}" `
    + `stroke-width="1" stroke-dasharray="2 2" fill="none"/>`;
  const bar = (inner, height = 26) => `<svg viewBox="0 0 312 ${height}" aria-hidden="true">${inner}</svg>`;
  const bold = `font-size="10" font-weight="700" text-anchor="middle" fill="${C.blue}"`;
  const speed = block.speed, swing = ch.swing, averageHalf = ch.bands.average_80.half_width;
  const share = ch.stays_inside_share, windowsCount = ch.fit_windows;
  const neverSub = share === null ? "—"
    : windowsCount ? `${fmt(Math.round(share * windowsCount))} Of ${fmt(windowsCount)} Past Windows`
    : `${fmt(share * 100)}% Of Past Windows`;
  const dashedBox = svgRect(edge(ch.rung_points), 8, (2 * ch.rung_points * scale).toFixed(1), 14, 5, "none", 1,
                            `stroke="${C.ink}" stroke-width="1.3" stroke-dasharray="3 2"`);
  el("steps-bars").innerHTML =
    stepBar(1, "Speed", "today", fmt(speed, 2), "One Minute's Typical Move · 15-Min Memory",
            bar(rect(speed, 0.55) + centreLine))
    + stepBar(2, "Swing", "today", plusMinus(swing), `Over ${minutes} Minutes · ${fmt(speed, 2)} × √${minutes}`,
              bar(funnel(speed, swing) + rect(swing, 0.35) + centreLine))
    + stepBar("3–4", `End Band · ${fmt(PROMISED_SHARE * 10)} In 10`, "history", plusMinus(half),
              `Stretched By Past Days · ${fmt(swing, 1)} × ${fmt(ch.stretch, 2)}`,
              bar(funnel(swing, half) + rect(half, 0.18) + centreLine
                  + svgText(edge(half), 34, fmt(ch.bands["80"].low), bold)
                  + svgText(farEdge(half), 34, fmt(ch.bands["80"].high), bold), 36))
    + stepBar("4b", "Average Band", "history", plusMinus(averageHalf),
              `Where The Average Lands · ${fmt(swing, 1)} × ${fmt(ch.average_stretch, 2)}`,
              bar((averageHalf === null ? "" : rect(averageHalf, 0.3)) + centreLine))
    + stepBar(5, `Never Leaves ±${ch.rung_points}`, "history", share === null ? "—" : `${fmt(share * 100)}%`,
              neverSub, bar(dashedBox + centreLine));
}

function dialGauge(applied) {
  const angle = applied === null ? 0 : clamp((applied - 1) * 150, -90, 90) * Math.PI / 180;
  const x = (28 + 17 * Math.sin(angle)).toFixed(1), y = (28 - 17 * Math.cos(angle)).toFixed(1);
  return `<path d="M8 28 A 20 20 0 0 1 48 28" fill="none" stroke="${C.rule}" stroke-width="5" stroke-linecap="round"/>`
       + svgLine(28, 28, x, y, C.ink, 2, `stroke-linecap="round"`) + `<circle cx="28" cy="28" r="3" fill="${C.ink}"/>`;
}

function stepCheck(number, name, kind, sub, svg, value) {
  return `<div class="step-check"><div class="text"><span class="name">${number} · ${name}${tag(kind)}</span>`
       + `<span class="sub">${sub}</span></div>${svg}<span class="value">${value}</span></div>`;
}

function dialsLine(ch) {
  const dials = ch.dials || {};
  const names = Object.keys(dials);
  const earned = names.filter((name) => dials[name].weight);
  const moving = names.filter((name) => dials[name].raw !== 1 && dials[name].raw !== null);
  if (earned.length) return `Earned: ${earned.map((name) => DIAL_LABELS[name]).join(" · ")}`;
  if (moving.length) {
    return `${moving.map((name) => DIAL_LABELS[name]).join(" · ")} · Raw ×${fmt(ch.dials_product.raw, 2)}`;
  }
  return "All Five Dials At Rest";
}

function renderStepChecks(block, ch, minutes) {
  if (!ch) {
    el("steps-checks").innerHTML = "";
    return;
  }
  const agree = optionsAgree(ch);
  const most = Math.max(ch.options_swing || 0, ch.swing || 0, 0.01);
  const direction = ch.direction;
  const chance = direction ? direction.chance_up_if_moves : null;
  const trust = direction ? fmt(direction.trust_applied, direction.trust_applied ? 2 : 0) : "—";
  const applied = ch.dials_product.applied;
  el("steps-checks").innerHTML =
    stepCheck(6, "Options", "check", `${fmt(block.straddle, 1)} × √(${minutes} ÷ ${fmt(block.minutes_left)})`,
              `<svg viewBox="0 0 56 34" role="img" aria-label="Options swing next to tape swing">`
              + svgRect(3, 7, (50 * (ch.options_swing || 0) / most).toFixed(1), 8, 3, C.greenDeep, 0.55)
              + svgRect(3, 19, (50 * (ch.swing || 0) / most).toFixed(1), 8, 3, C.blue, 0.45) + `</svg>`,
              `${plusMinus(ch.options_swing)} ${agree ? "✓" : "!"}`)
    + stepCheck(7, "Dials", "earned", dialsLine(ch),
                `<svg viewBox="0 0 56 34" role="img" aria-label="Dial at times ${fmt(applied, 2)}">`
                + `${dialGauge(applied)}</svg>`, `×${fmt(applied, 2)}`)
    + stepCheck(8, "Direction", "earned", direction ? `50 + ${trust} × (Lean − 50)` : "—",
                `<svg viewBox="0 0 56 34" role="img" aria-label="Direction meter"><g transform="translate(3,11)">`
                + `${leanMeter(chance, 50)}</g>` + svgText(3, 32, "Down", `font-size="7" fill="${C.rust}"`)
                + svgText(53, 32, "Up", `font-size="7" text-anchor="end" fill="${C.blue}"`) + `</svg>`,
                chance === null ? "—" : `Up ${fmt(chance * 100)}%`);
}

function renderAnswer(ch) {
  const band = ch && ch.bands["80"], chance = ch && ch.direction ? ch.direction.chance_up_if_moves : null;
  const between = band && band.half_width !== null ? `${fmt(band.low)} – ${fmt(band.high)}` : "—";
  el("answer").innerHTML =
    `<div><span>Ends Between</span><span>${between}</span></div>`
    + `<div><span>Average Within</span><span>${plusMinus(ch && ch.bands.average_80.half_width)}</span></div>`
    + `<div><span>Direction</span><span>${chance === null ? "—" : `Up ${fmt(chance * 100)}%`}</span></div>`;
}

// ---- tabs, switches, timers ---------------------------------------------------------------------------------------

function showTab(name) {
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${name}`; });
  document.querySelectorAll(".tabs button").forEach((button) => {
    if (button.dataset.tab === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  window.scrollTo(0, 0);
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  const switchButton = event.target.closest("[data-channel]");
  if (tab) showTab(tab.dataset.tab);
  if (switchButton) {
    state.channel = switchButton.dataset.channel;
    render();
  }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadFeed(); });
setInterval(loadFeed, REFRESH_MS);
loadFeed();

// Fetches /latest.json and /bars.json every 30 s, and holds /price.stream open for SPX's live price while the page is
// shown; it draws the three views from those three and nothing else. One fetch, one render per block (each in its own
// guard), one number formatter, one clock, one home for the live window's maths.
"use strict";

const CHANNELS = { "5_minutes": 5, "30_minutes": 30, "60_minutes": 60 };
const FRESH_SECONDS = 8 * 60;
const TICK_FRESH_SECONDS = 10;
const BAR_CLOSE_SECOND_MS = 59000;
const STREAM_REOPEN_MS = 1000;
const STREAM_SILENCE_MS = 40000;   // no tick and no heartbeat this long: a stalled connection, reopen it
const STREAM_BACKOFF_MS = 5000;
const STREAM_BACKOFF_MAX_MS = 60000;
const CLOSE_GRACE_SECONDS = 60;
const FIRST_READ_SECONDS = 5 * 60;
const POLL_MS = 30000;
const POLL_BACKOFF_MAX_MS = 5 * 60000;
const FETCH_TIMEOUT_MS = 10000;
const LIST_ROWS_SHOWN = 12;
const LEAN_DEAD_ZONE = 0.01;
// How much time a chart spans, ending at the live window's end: about fifteen 5-minute boxes, eight 30-minute windows
// and nine overlapping 60-minute ones, so a box stays readable on a 360 px phone; never before the session's open.
const PAST_SPAN_MINUTES = { "5_minutes": 75, "30_minutes": 240, "60_minutes": 270 };
const CHART_PAD = { top: 4, bottom: 6, coneLabel: 4 };
const LABEL_CHAR_WIDTH = 6;   // a 10 px label's widest character, for the label collision checks
const BOLD_CHAR_WIDTH = 7.5;  // the same for a bold one
const LABEL_GAP = 4;
const AXIS_LABEL_CLEAR = 14;  // a cone label's baseline stays this far above the time labels' baseline
const BODY_FONT_PX = 12;      // app.css's body size: the chart's label sizes grow with the page's text when it does
const CONE_STEPS = 4;
const FONT = "Plus Jakarta Sans, sans-serif";
const COLOUR = { ink: "#161A17", blue: "#2F44B8", rust: "#A8492F", green: "#2e9e5b", greenDeep: "#1f6b3e",
                 faint: "#5C6459", sand: "#B9B2A4", band: "#D5D0C6", rule: "#E4E0D8", cream: "#F4F1EB",
                 white: "#FFFFFF", grey: "#8a8a8a", track: "#C9D1F4" };
const CHART = { width: 336, left: 4, right: 330, top: 14, bottom: 198, labelY: 213, dotHalo: 9, averageDot: 4.5 };
// The strip: the two numbers on its top line at the card's edges, each after its key; every needle ends 4 px above
// the average band's bottom, so the labels under the strip keep clear of them.
const STRIP = { width: 312, centre: 156, reach: 110.9, edge: 6, inset: 2, topY: 10, keyY: 6.5, keyRadius: 3.5,
                keyGap: 8, bandTop: 21, bandBottom: 43, ringRadius: 6, labelY: 60 };
STRIP.needleEnd = STRIP.bandBottom - 4;
const RANGE_BAR = { width: 312, centre: 156, reach: 136.7 };
const SPEED = { width: 240, barWidth: 46, step: 84, left: 12, baseline: 50, top: 2, nameY: 62, memoryY: 75 };
const METER = { width: 86, small: 50 };
const ROW = { width: 120, trackLeft: 20, trackRight: 100, centre: 60, reach: 40 };
const FEED_LABELS = { spx_live_price: "SPX Price", spx_bars: "SPX Bars", watchlist_quotes: "Quotes",
                      nyse_breadth: "Breadth", options_scan: "Options Scan", options_tape: "Options Tape",
                      futures_bars: "Futures",
                      event_calendar: "Calendar", news_headlines: "News", claude_forecast: "Claude",
                      schwab_login: "Schwab Login" };
// What a once-a-day or hand-kept feed's row says instead of an age, from its as_of.
const AS_OF_WORDS = { futures_bars: (at) => `Night · ${clock(at)}`, schwab_login: (at) => `Checked ${clock(at)}`,
                      event_calendar: (day) => `To ${monthDay(day)}` };
const QUIET_CADENCES = ["once_a_day", "hand_kept"];
// The band wideners' plain names, keyed by the feed's dial names in the engine's order: the one home for these words.
const WIDENER_NAMES = { fed_window: "Fed Decision Day", release_window: "Data Release In The Window",
                        closing_window: "Last 15 Minutes Of The Day", acceleration: "Price Speeding Up",
                        options_vs_tape: "Options Expect More Movement" };
const COUNTED_WIDENERS = ["acceleration", "options_vs_tape"];   // always reading: on only once they have weight
const REPLY_WORDS = { 401: "Sign-In Refused (401) · Check The Token",
                      404: "No Live Feed Yet · The Engine Has Not Written One",
                      503: "The Mac's Server Can't Answer Right Now (503)" };
const state = { feed: null, bars: [], channel: "30_minutes", fetchedAt: null, unreachable: false, unreadable: false,
                feedReply: null, barsReply: null, inFlight: false, failures: 0, pollTimer: null, showAll: false,
                tick: null, stream: null, streamFailures: 0, streamTimer: null, tickTimer: null, openPush: new Set() };

// ---- the one number formatter, the one clock formatter, small text helpers ------------------------------------------

function fmt(value, decimals = 0, signed = false) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  if (Number.isNaN(number)) return "—";
  const text = Math.abs(number).toLocaleString("en-US", { minimumFractionDigits: decimals,
                                                           maximumFractionDigits: decimals });
  if (number < 0 && Number(text.replace(/,/g, "")) !== 0) return "−" + text;
  return signed ? "+" + text : text;
}

function clock(iso, seconds = false) {
  if (!iso) return "—";
  const parts = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
  return new Date(iso).toLocaleTimeString([], seconds ? { ...parts, second: "2-digit" } : parts);
}

/** "12-31" for a bare date, read as the day it names. */
function monthDay(day) {
  return day ? day.slice(5, 10) : "—";
}

function ms(iso) {
  return new Date(iso).getTime();
}

/** "Fri 10-16" for a moment or a bare date; a bare date is read as a local day, never as UTC midnight. */
function dayWord(iso) {
  if (!iso) return "—";
  const when = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso);
  return `${when.toLocaleDateString([], { weekday: "short" })} ${String(when.getMonth() + 1).padStart(2, "0")}`
       + `-${String(when.getDate()).padStart(2, "0")}`;
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

function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
                                                     "'": "&#39;" }[c]));
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

/** How much larger the page's text is drawn than app.css asks (1 unless a text size setting enlarges it). */
function textScale() {
  return parseFloat(getComputedStyle(document.body).fontSize) / BODY_FONT_PX || 1;
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function remembered(key, fallback) {
  try {
    return sessionStorage.getItem(key) || fallback;
  } catch (failure) {
    return fallback;
  }
}

function remember(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch (failure) {
    // a private window or blocked storage: the choice lives for this page only
  }
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

function liveWindow() {
  const block = windows();
  return block ? block.current[state.channel] : null;
}

/** The chosen channel's running or finished windows, newest first; an empty list when the feed has none. */
function windowList(kind) {
  const block = windows();
  return block && block[kind] ? block[kind][state.channel] || [] : [];
}

function halfWidth(bands, name) {
  return bands && bands[name] ? bands[name].half_width : null;
}

function bandPortion() {
  const block = range();
  return block && block.band_portion !== undefined ? block.band_portion : null;
}

function reasons() {
  const text = state.feed && state.feed.status_reason;
  return text ? text.split(",").filter(Boolean) : [];
}

function valueOf(key) {
  const values = state.feed && state.feed.values;
  return values && values[key] !== undefined ? values[key] : null;
}

function feedAgeSeconds() {
  const moment = state.feed && (state.feed.cut_at || state.feed.failed_at);
  return moment ? (Date.now() - new Date(moment).getTime()) / 1000 : null;
}

/** The feed's standing: live within six minutes of its read, late after that while the session is open, closed once
 *  the session is over, and no_read_today once the next session has had time for its first read with no newer feed. */
function feedState() {
  const feed = state.feed;
  if (!feed) return "none";
  if (state.unreachable) return "unreachable";
  if (state.unreadable) return "unreadable";
  if (feed.status === "failed") return "failed";
  const session = feed.session || {};
  const closesAt = session.closes_at ? new Date(session.closes_at).getTime() : null;
  const nextOpen = session.next_session_opens_at ? new Date(session.next_session_opens_at).getTime() : null;
  if (closesAt !== null && Date.now() > closesAt + CLOSE_GRACE_SECONDS * 1000) {
    const firstReadDue = nextOpen === null ? null : nextOpen + (FIRST_READ_SECONDS + FRESH_SECONDS) * 1000;
    return firstReadDue !== null && Date.now() > firstReadDue ? "no_read_today" : "closed";
  }
  return feedAgeSeconds() > FRESH_SECONDS ? "late" : "live";
}

function isLive() {
  return feedState() === "live";
}

/** True while the session the feed describes is open: the read is live or late, not closed. */
function sessionOpen() {
  return ["live", "late"].includes(feedState());
}

/** The live tick when it belongs to the feed's session (a tick from the next morning never meets an old read). */
function sessionTick() {
  const cut = state.feed && state.feed.cut_at;
  return state.tick && cut && state.tick.at.slice(0, 10) === cut.slice(0, 10) ? state.tick : null;
}

/** True while ticks arrive: the newest one traded less than ten seconds ago. */
function tickFresh() {
  const tick = sessionTick();
  return Boolean(tick) && (Date.now() - ms(tick.at)) / 1000 < TICK_FRESH_SECONDS;
}

/** The newest price the page holds and when it was the price: the live tick, a minute's close or the read's price. */
function latestPrice() {
  const block = range();
  const bar = state.bars[state.bars.length - 1];
  const tick = sessionTick();
  const known = [block ? { price: block.price, atMs: ms(block.read_at) } : null,
                 bar ? { price: bar.close, atMs: ms(bar.at) + BAR_CLOSE_SECOND_MS } : null,
                 tick ? { price: tick.price, atMs: ms(tick.at) } : null]
    .filter((entry) => entry && entry.price !== null);
  return known.reduce((newest, entry) => (!newest || entry.atMs >= newest.atMs ? entry : newest), null);
}

/** The live window measured now, the grade's way: its minute closes from window_from (the live tick standing in for
 *  the minute still trading), the move and running average from the read price, and whether that average sits inside
 *  its band. The one home for this maths: the chart's line, the strip and the list's live row all read it. */
function liveNow(current) {
  const fromMs = ms(current.window_from), endMs = ms(current.ends_at);
  const series = state.bars.filter((bar) => ms(bar.at) >= fromMs && ms(bar.at) < endMs)
    .map((bar) => ({ at: bar.at, close: bar.close }));
  const tick = sessionTick();
  const tickMinuteMs = tick ? Math.floor(ms(tick.at) / 60000) * 60000 : null;
  const lastMs = series.length ? ms(series[series.length - 1].at) : -Infinity;
  if (tick && tickMinuteMs >= fromMs && tickMinuteMs < endMs && tickMinuteMs > lastMs) {
    series.push({ at: new Date(tickMinuteMs).toISOString(), close: tick.price });
  }
  if (!series.length || current.price === null) {
    return { ...current.now, average_inside: current.average_inside, series, averages: [] };
  }
  let sum = 0;
  const averages = series.map((point, index) => {
    sum += point.close;
    return sum / (index + 1);
  });
  const move = series[series.length - 1].close - current.price;
  const averageMove = averages[averages.length - 1] - current.price;
  const averageHalf = halfWidth(current.bands, "average_80");
  return { move, average_move: averageMove,
           farthest_up: Math.max(current.now.farthest_up || 0, move, 0),
           farthest_down: Math.max(current.now.farthest_down || 0, -move, 0),
           average_inside: averageHalf === null ? null : Math.abs(averageMove) <= averageHalf, series, averages };
}

/** The read a channel's screens explain: the live window's own read (its chain travels with it), else the newest
 *  read's channel block (the same read when the newest read is the headline one). One shape for Sight and Steps. */
function explainedRead() {
  const current = liveWindow();
  if (current && current.chain) {
    return { ...current.chain, read_at: current.read_at, price: current.price, bands: current.bands,
             ends_at: current.ends_at, clipped: current.clipped, status: current.status, reason: current.reason,
             rung_points: current.rung_points };
  }
  const block = range(), ch = channel();
  if (!ch) return null;
  return { read_at: block.read_at, price: block.price, bands: ch.bands, ends_at: ch.ends_at, clipped: ch.clipped,
           status: ch.status, reason: ch.reason, rung_points: ch.rung_points, speed: block.speed,
           speed_fast: block.speed_fast, speed_slow: block.speed_slow, swing: ch.swing, stretch: ch.stretch,
           average_stretch: ch.average_stretch, fit_sessions: ch.fit_sessions, fit_windows: ch.fit_windows,
           stays_inside_share: ch.stays_inside_share, dials: ch.dials, dials_product: ch.dials_product,
           straddle: block.straddle, minutes_left: block.minutes_left, options_swing: ch.options_swing,
           options_check: ch.options_check, chance_up_if_moves: ch.direction ? ch.direction.chance_up_if_moves : null,
           trust_applied: ch.direction ? ch.direction.trust_applied : null };
}

function emptyReason() {
  const feed = state.feed;
  if (!feed) {
    if (state.unreachable) return "Can't Reach The Mac";
    return state.unreadable ? "Feed Unreadable" : REPLY_WORDS[state.feedReply] || "No Live Feed Yet";
  }
  if (feed.status === "failed") return `Read Failed · ${feed.status_reason}`;
  if (!range()) return "Range Step Failed On This Read";
  const read = explainedRead();
  if (read && read.status === "blind") return `${titleCase(read.reason || "blind")} On This Read`;
  if (windowsFailure()) return `Grading Failed · ${windowsFailure()}`;
  return "No Read Yet Today For This Window";
}

function newestReadDiffers(read) {
  const block = range();
  return read && block && block.read_at !== read.read_at;
}

// ---- the fetch, the poll and the render ----------------------------------------------------------------------------

/** One request to the Mac (same origin, so the shell's sign-in rides along), given up when its reply has not begun
 *  within ten seconds; the control given aborts it later too. */
function fetchWithTimeout(path, control = new AbortController()) {
  const timer = setTimeout(() => control.abort(), FETCH_TIMEOUT_MS);
  return fetch(location.origin + path, { cache: "no-store", signal: control.signal })
    .finally(() => clearTimeout(timer));
}

/** Hold /price.stream open while the page is shown: each event is a tick; the stream ending (the server closes it
 *  after 30 minutes) reopens it in a second, a failure in a growing wait. Without ticks the 30 s poll carries on. */
async function openPriceStream() {
  if (state.stream || document.hidden) return;
  const control = new AbortController();
  state.stream = control;
  try {
    const reply = await fetchWithTimeout("/price.stream", control);
    if (!reply.ok || !reply.body) throw new Error(`price stream ${reply.status}`);
    const reader = reply.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let watchdog = setTimeout(() => control.abort(), STREAM_SILENCE_MS);
    for (;;) {
      const { value, done } = await reader.read();
      clearTimeout(watchdog);
      if (done) break;
      watchdog = setTimeout(() => control.abort(), STREAM_SILENCE_MS);
      state.streamFailures = 0;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      events.forEach(takePriceEvent);
    }
  } catch (failure) {
    if (state.stream === control) state.streamFailures += 1;
  }
  if (state.stream !== control) return;
  state.stream = null;
  clearTimeout(state.streamTimer);
  const wait = state.streamFailures
    ? Math.min(STREAM_BACKOFF_MS * 2 ** (state.streamFailures - 1), STREAM_BACKOFF_MAX_MS) : STREAM_REOPEN_MS;
  state.streamTimer = setTimeout(openPriceStream, wait);
}

function closePriceStream() {
  clearTimeout(state.streamTimer);
  const control = state.stream;
  state.stream = null;
  if (control) control.abort();
}

/** One server-sent event: a data line is a tick ({price, at}); a comment line only keeps the connection open. */
function takePriceEvent(text) {
  const data = text.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5)).join("");
  if (!data) return;
  try {
    const tick = JSON.parse(data);
    if (typeof tick.price !== "number" || !tick.at) return;
    state.tick = tick;
  } catch (failure) {
    return;
  }
  renderLive();
  clearTimeout(state.tickTimer);
  state.tickTimer = setTimeout(renderLive, TICK_FRESH_SECONDS * 1000);
}

async function loadFeed() {
  if (state.inFlight) return;
  state.inFlight = true;
  try {
    const [feedReply, barsReply] = await Promise.all([fetchWithTimeout("/latest.json"),
                                                      fetchWithTimeout("/bars.json")]);
    state.feedReply = feedReply.status;
    state.barsReply = barsReply.status;
    const feed = feedReply.ok ? await parsed(feedReply) : null;
    state.unreadable = feedReply.ok && feed === undefined;
    state.feed = state.unreadable ? state.feed : feed;
    const bars = barsReply.ok ? await parsed(barsReply) : null;
    state.bars = bars ? bars.bars : [];
    state.unreachable = false;
    state.failures = 0;
    state.fetchedAt = Date.now();
  } catch (failure) {
    state.unreachable = true;
    state.failures += 1;
  }
  state.inFlight = false;
  render();
  schedulePoll();
}

async function parsed(reply) {
  try {
    return await reply.json();
  } catch (failure) {
    return undefined;
  }
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const wait = state.unreachable ? Math.min(POLL_MS * 2 ** (state.failures - 1), POLL_BACKOFF_MAX_MS) : POLL_MS;
  state.pollTimer = setTimeout(loadFeed, wait);
}

/** One block draws inside its own guard: a bad field empties that block with its reason, never the page. */
function guarded(draw, noteId) {
  try {
    draw();
  } catch (failure) {
    console.error(noteId, failure);
    note(noteId, `This Block Failed To Draw · ${failure.message}`);
  }
}

/** What a tick moves: the header, the chart's line, the strip, the list's live row and the feeds card. */
function renderLive() {
  guarded(renderClock, "clock-note");
  guarded(renderRead, "chart-note");
  guarded(renderStrip, "strip-note");
  guarded(renderReadsList, "list-note");
  guarded(renderFeeds, "feeds-note");
}

function render() {
  guarded(renderClock, "clock-note");
  guarded(renderNotice, "notice");
  guarded(renderSwitches, "clock-note");
  guarded(renderRead, "chart-note");
  guarded(renderStrip, "strip-note");
  guarded(renderReadsList, "list-note");
  guarded(renderScores, "list-note");
  guarded(renderSight, "sight-note");
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.push : null;
  guarded(renderPush, "push-note");
  keepFocus(focused);
  guarded(renderFeeds, "feeds-note");
  guarded(renderSteps, "steps-note");
  guarded(renderAnswer, "steps-note");
}

function renderClock() {
  const feed = state.feed;
  const kind = feedState();
  const cut = feed && feed.cut_at;
  const last = clock(feed && (feed.cut_at || feed.failed_at));
  const words = { none: state.unreadable ? "Feed Unreadable" : "No Feed",
                  failed: `Failed · ${clock(feed && feed.failed_at)}`,
                  unreachable: `Can't Reach · Last Read ${last}`, unreadable: `Feed Unreadable · Last Read ${last}`,
                  closed: `Closed · Last Read ${clock(cut)}`,
                  no_read_today: `No Read Yet Today · Last Read ${dayWord(cut)} ${clock(cut)}`,
                  late: `Late · Last Read ${clock(cut)} · ${ago(feedAgeSeconds())} Ago`,
                  live: `Read ${clock(cut)} · Price ${tickFresh() ? "Live" : priceMoment()}` };
  document.querySelectorAll(".clock-text").forEach((node) => { node.textContent = words[kind]; });
  document.querySelectorAll(".clock-dot").forEach((node) => { node.className = `clock-dot ${kind}`; });
  const latest = latestPrice();
  const priorClose = valueOf("prior_close_points");
  const change = latest && priorClose ? (latest.price - priorClose) / priorClose * 100 : null;
  el("price").textContent = latest ? fmt(latest.price, 2) : "—";
  el("change").textContent = change === null ? "—" : `${fmt(change, 2, true)}%`;
  el("change").className = "change " + (change === null ? "faint" : change < 0 ? "down" : "up");
}

/** When the header's price was the price, to the second, on the phone's clock. */
function priceMoment() {
  const latest = latestPrice();
  return latest ? clock(new Date(latest.atMs).toISOString(), true) : "—";
}

function renderNotice() {
  const feed = state.feed;
  const lines = [];
  if (state.unreachable) {
    lines.push(`Can't Reach The Mac${state.fetchedAt ? " · Last Fetched " + clock(state.fetchedAt) : ""}`);
  } else if (state.unreadable) {
    lines.push(`Feed Unreadable${feed ? " · Showing The Last Good Read" : ""}`);
  } else if (!feed && state.feedReply) {
    lines.push(REPLY_WORDS[state.feedReply] || `Server Error (${state.feedReply})`);
  }
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
  const current = liveWindow();
  const minutes = CHANNELS[state.channel];
  const band = current && current.bands["80"];
  el("range-line").textContent = band && band.half_width !== null ? `${fmt(band.low)} – ${fmt(band.high)}` : "—";
  const standing = isLive() ? "" : current && current.ended ? " · Ended" : " · Last";
  el("held").textContent = current ? `${fmt(current.minutes_held)} / ${minutes} Min${standing}`
                                   : `— / ${minutes} Min`;
  renderChart(current, windowList("running"), windowList("finished"), minutes);
}

/** The day's price, each past read's band as a box (running ones dashed) and the live cone, on a time axis that ends
 *  at the live window's end and spans the channel's PAST_SPAN_MINUTES (from the open while the day is shorter), with
 *  a price axis fitted to what is on screen. */
function renderChart(current, running, finished, minutes) {
  const chart = el("chart");
  const anchorEnd = current ? current.ends_at : running[0] ? running[0].ends_at : finished[0] ? finished[0].ends_at
                  : state.bars.length ? state.bars[state.bars.length - 1].at : null;
  const notes = anchorEnd && current ? [] : [emptyReason()];
  if (!state.bars.length && state.barsReply && state.barsReply !== 200) {
    notes.push(`Price Line Unavailable (${fmt(state.barsReply)})`);
  }
  note("chart-note", notes.join(" · "));
  chart.innerHTML = "";
  chart.hidden = !anchorEnd;
  if (!anchorEnd) return;
  const endMs = ms(anchorEnd);
  const session = state.feed && state.feed.session;
  const openMs = session && session.opens_at ? ms(session.opens_at)
               : state.bars.length ? ms(state.bars[0].at) : -Infinity;
  const startMs = Math.min(Math.max(openMs, endMs - PAST_SPAN_MINUTES[state.channel] * 60000),
                           endMs - minutes * 60000);
  const x = (iso) => CHART.left + (ms(iso) - startMs) / (endMs - startMs) * (CHART.right - CHART.left);
  const shown = (window) => ms(window.window_from) >= startMs && window.half_width !== null;
  const boxes = finished.filter(shown), runs = running.filter(shown);
  const fromMs = current ? ms(current.window_from) : Infinity;
  const before = state.bars.filter((bar) => ms(bar.at) >= startMs && ms(bar.at) <= endMs && ms(bar.at) < fromMs);
  const now = current ? liveNow(current) : null;
  const bars = before.concat(now ? now.series : []);
  const liveHalf = current ? halfWidth(current.bands, "80") : null;
  const prices = bars.map((bar) => bar.close);
  boxes.concat(runs).forEach((window) => prices.push(window.price + window.half_width,
                                                     window.price - window.half_width, window.end));
  if (liveHalf !== null) prices.push(current.price + liveHalf, current.price - liveHalf);
  const known = prices.filter((p) => p !== null && p !== undefined);
  if (!known.length) return;
  const top = CHART.top + CHART_PAD.top, bottom = CHART.bottom - CHART_PAD.bottom;
  const high = Math.max(...known);
  // the cone's lower price label hangs under its edge: that edge stays CHART_PAD.coneLabel above the bottom
  const coneLow = liveHalf === null ? null : current.price - liveHalf;
  const low = Math.min(...known, coneLow === null ? Infinity
                                 : high - (high - coneLow) / (1 - CHART_PAD.coneLabel / (bottom - top)));
  const y = (price) => top + (high - price) / (high - low || 1) * (bottom - top);
  // where finished windows overlap (60 minutes), every other one from the day's first is outlined to tell them apart
  const overlapping = finished.some((window, index) => index + 1 < finished.length
                                                       && ms(finished[index + 1].ends_at) > ms(window.window_from));
  const outlined = (window) => overlapping && (finished.length - 1 - finished.indexOf(window)) % 2 === 1;
  const parts = boxes.map((window) => readBox(window, x, y, outlined(window)));
  runs.forEach((window) => parts.push(runningBox(window, x, y)));
  const marks = liveMarks(bars, now, x, y).concat(runs.map((window) => runningGaugeArea(window, x, y)));
  if (liveHalf !== null) parts.push(liveCone(current, x, y, marks));
  parts.push(priceLines(current, before, now, x, y),
             axisLabels(current, boxes, bars, x, startMs === openMs ? "Open" : ""));
  chart.innerHTML = parts.join("");
}

/** A window's gauge on its box's right edge: the track, the part used from the read price to yEnd, and a tick there. */
function gauge(gaugeX, y0, y1, yRead, yEnd, colour) {
  const parts = [svgRect(gaugeX, y0 + 20, 5, Math.max(0, y1 - y0 - 26), 2.5, COLOUR.ink, 0.08)];
  if (yEnd !== null) {
    const usedTop = Math.min(yRead, yEnd), usedBottom = Math.max(yRead, yEnd);
    parts.push(svgRect(gaugeX, usedTop, 5, usedBottom - usedTop, 2.5, colour, 0.75),
               svgLine(gaugeX - 3, yEnd, gaugeX + 8, yEnd, COLOUR.ink, 1.6));
  }
  return parts.join("");
}

function readBox(window, x, y, outlined) {
  const x0 = x(window.window_from), x1 = x(window.ends_at);
  const y0 = y(window.price + window.half_width), y1 = y(window.price - window.half_width);
  const inside = window.grades.ends_inside;
  const colour = inside === null ? COLOUR.grey : inside ? COLOUR.green : COLOUR.rust;
  const gaugeX = x1 - 10;
  const outline = outlined ? `stroke="${COLOUR.sand}" stroke-width="1"` : "";
  const parts = [svgRect(x0 + 1, y0, x1 - x0 - 2, y1 - y0, 6, COLOUR.ink, 0.045, outline),
                 gauge(gaugeX, y0, y1, y(window.price), window.end === null ? null : y(window.end), colour)];
  const cx = gaugeX + 2.5, cy = y0 + 8;
  parts.push(`<circle cx="${cx}" cy="${cy}" r="6" fill="${colour}"/>`);
  if (inside === true) {
    parts.push(`<path d="M${cx - 3.5} ${cy}l2.5 2.5 4-4.5" fill="none" stroke="${COLOUR.white}" stroke-width="1.8" `
               + `stroke-linecap="round" stroke-linejoin="round"/>`);
  } else if (inside === false) {
    parts.push(`<path d="M${cx - 3} ${cy - 3}l6 6M${cx + 3} ${cy - 3}l-6 6" stroke="${COLOUR.white}" `
               + `stroke-width="1.8" stroke-linecap="round"/>`);
  }
  return parts.join("");
}

/** A window still running: a dashed blue outline, a hollow dashed mark at its top right, the used-so-far gauge. */
/** The strip a running box's gauge takes on its right edge, for drawing it and for keeping labels off it. */
function runningGaugeArea(window, x, y) {
  const right = x(window.ends_at) - 5;
  const half = window.half_width;
  return { left: right - 5, right, top: y(window.price + half), bottom: y(window.price - half) };
}

function runningBox(window, x, y) {
  const x0 = x(window.window_from), x1 = x(window.ends_at);
  const y0 = y(window.price + window.half_width), y1 = y(window.price - window.half_width);
  const gaugeX = runningGaugeArea(window, x, y).left;
  return svgRect(x0 + 1, y0, x1 - x0 - 2, y1 - y0, 6, "none", 1,
                 `stroke="${COLOUR.blue}" stroke-opacity="0.6" stroke-width="1.2" stroke-dasharray="4 3"`)
       + gauge(gaugeX, y0, y1, y(window.price), window.end === null ? null : y(window.end), COLOUR.blue)
       + `<circle cx="${gaugeX + 2.5}" cy="${y0 + 8}" r="5" fill="${COLOUR.cream}" stroke="${COLOUR.blue}" `
       + `stroke-width="2" stroke-dasharray="3 2"/>`;
}

/** The room priceLines' live dot and running-average dot take, as boxes the cone's labels keep off. */
function liveMarks(bars, now, x, y) {
  const last = bars[bars.length - 1];
  if (!last) return [];
  const dot = (cx, cy, r) => ({ left: cx - r, right: cx + r, top: cy - r, bottom: cy + r });
  const marks = [dot(x(last.at), y(last.close), CHART.dotHalo)];
  if (now && now.averages.length) {
    marks.push(dot(x(last.at), y(now.averages[now.averages.length - 1]), CHART.averageDot));
  }
  return marks;
}

/** True when a 10 px label ending at right on baseline would touch one of the marks (boxes: left/right/top/bottom). */
function labelTouches(text, right, baseline, marks) {
  const scale = textScale();
  const left = right - text.length * LABEL_CHAR_WIDTH * scale;
  return marks.some((mark) => left < mark.right && right > mark.left
                              && baseline - 10 * scale < mark.bottom && baseline + 3 * scale > mark.top);
}

/** One of the cone's price labels at its end, never down into the time labels' row; when it would touch the live dot
 *  it moves further out (above the upper edge, below the lower one) while that stays clear of the chart's top and the
 *  time labels, else left of the dot. */
function coneLabel(text, right, wanted, outward, marks) {
  // a cream halo keeps the label legible where a narrow cone's label overhangs a box or gauge
  const attrs = `font-size="10" text-anchor="end" fill="${COLOUR.blue}" stroke="${COLOUR.cream}" stroke-width="3" `
              + `paint-order="stroke"`;
  const lowest = CHART.labelY - AXIS_LABEL_CLEAR * textScale();
  const baseline = Math.min(wanted, lowest);
  if (!labelTouches(text, right, baseline, marks)) return svgText(right, baseline, text, attrs);
  const reach = outward < 0 ? Math.min(...marks.map((mark) => mark.top - 4))
                            : Math.max(...marks.map((mark) => mark.bottom + 11));
  if (reach >= 10 && reach <= lowest && !labelTouches(text, right, reach, marks)) {
    return svgText(right, reach, text, attrs);
  }
  return svgText(Math.min(...marks.map((mark) => mark.left - 2)), baseline, text, attrs);
}

function liveCone(current, x, y, marks) {
  const xr = x(current.read_at), xe = x(current.ends_at);
  const cone = (reachAtEnd, opacity) => {
    const upper = [], lower = [];
    for (let step = 0; step <= CONE_STEPS; step += 1) {
      const t = step / CONE_STEPS, px = xr + t * (xe - xr), reach = reachAtEnd * Math.sqrt(t);
      upper.push(`${px.toFixed(1)},${y(current.price + reach).toFixed(1)}`);
      lower.unshift(`${px.toFixed(1)},${y(current.price - reach).toFixed(1)}`);
    }
    return `<polygon points="${upper.concat(lower).join(" ")}" fill="${COLOUR.blue}" fill-opacity="${opacity}"/>`;
  };
  const endHalf = halfWidth(current.bands, "80"), averageHalf = halfWidth(current.bands, "average_80");
  const upper = current.price + endHalf, lower = current.price - endHalf;
  return cone(endHalf, 0.12) + (averageHalf !== null ? cone(averageHalf, 0.2) : "")
       + coneLabel(fmt(upper), xe, y(upper) - 4, -1, marks) + coneLabel(fmt(lower), xe, y(lower) + 11, 1, marks);
}

/** The day's line before the live window in ink, up to the read's price at the read; inside it, the window's closes
 *  (the live tick last) in blue with their running average dashed, both from liveNow, and the live dot at the end. */
function priceLines(current, before, now, x, y) {
  const after = now ? now.series : [];
  const bars = before.concat(after);
  if (!bars.length) return "";
  const point = (bar) => `${x(bar.at).toFixed(1)},${y(bar.close).toFixed(1)}`;
  const round = `stroke-linejoin="round" stroke-linecap="round"`;
  const start = current && current.price !== null
              ? `${x(current.read_at).toFixed(1)},${y(current.price).toFixed(1)}` : null;
  const parts = [];
  if (before.length) {
    parts.push(svgPolyline(before.map(point).concat(start && after.length ? [start] : []), COLOUR.ink, 2, round));
  }
  const last = bars[bars.length - 1];
  const lastX = x(last.at), lastY = y(last.close);
  if (start && after.length) {
    const averages = after.map((bar, index) => `${x(bar.at).toFixed(1)},${y(now.averages[index]).toFixed(1)}`);
    parts.push(svgPolyline([start].concat(after.map(point)), COLOUR.blue, 2.6, round),
               svgPolyline([start].concat(averages), COLOUR.ink, 1.8, `stroke-dasharray="4 2.5"`),
               `<circle cx="${lastX}" cy="${lastY}" r="${CHART.dotHalo}" fill="${COLOUR.blue}" fill-opacity="0.15"/>`,
               `<circle cx="${lastX}" cy="${lastY}" r="5" fill="${COLOUR.blue}" stroke="${COLOUR.cream}" `
               + `stroke-width="2"/>`,
               `<circle cx="${lastX}" cy="${y(now.averages[now.averages.length - 1])}" r="${CHART.averageDot}" `
               + `fill="${COLOUR.cream}" stroke="${COLOUR.ink}" stroke-width="2"/>`);
  } else {
    parts.push(`<circle cx="${lastX}" cy="${lastY}" r="5" fill="${COLOUR.blue}" stroke="${COLOUR.cream}" `
               + `stroke-width="2"/>`);
  }
  return parts.join("");
}

/** The time labels: Now (or Last) first, then the axis's start (Open, or its time), then each finished box's read
 *  time at its middle, newest first, each left out where it would touch one already placed. Running boxes get none:
 *  their middle is where Now sits. */
function axisLabels(current, boxes, bars, x, startWord) {
  const last = bars[bars.length - 1];
  const room = 12 * textScale();
  const nowX = clamp(last ? x(last.at) : current ? x(current.read_at) : CHART.width - room, room, CHART.width - room);
  const faint = `font-size="10" fill="${COLOUR.faint}"`;
  const labels = [{ x: nowX, text: isLive() ? "Now" : "Last", anchor: "middle", charWidth: BOLD_CHAR_WIDTH,
                    attrs: `font-size="10" fill="${COLOUR.ink}" font-weight="700"` },
                  { x: CHART.left, text: startWord, anchor: "start", attrs: faint }];
  boxes.forEach((window) => labels.push({ x: (x(window.window_from) + x(window.ends_at)) / 2,
                                          text: clock(window.read_at), anchor: "middle", attrs: faint }));
  const placed = [], parts = [];
  labels.forEach((label) => {
    const width = label.text.length * (label.charWidth || LABEL_CHAR_WIDTH) * textScale();
    const left = label.anchor === "start" ? label.x : label.x - width / 2;
    if (placed.some(([from, to]) => left < to + LABEL_GAP && left + width > from - LABEL_GAP)) return;
    placed.push([left, left + width]);
    parts.push(svgText(label.x, CHART.labelY, label.text, `${label.attrs} text-anchor="${label.anchor}"`));
  });
  return parts.join("");
}

/** The Average Since The Read strip: the live and average numbers at the top line's edges, each after its key, the
 *  bands, the needles, and under it the read's price with the band-edge numbers, laid out so no two labels touch. */
function renderStrip() {
  const current = liveWindow();
  const strip = el("strip");
  const verdict = el("strip-verdict");
  const endHalf = current ? halfWidth(current.bands, "80") : null;
  note("strip-note", endHalf ? "" : emptyReason());
  strip.hidden = !endHalf;
  if (!endHalf) {
    strip.innerHTML = "";
    verdict.textContent = "—";
    verdict.className = "faint";
    return;
  }
  const averageHalf = halfWidth(current.bands, "average_80");
  const scale = STRIP.reach / endHalf;
  const edgeLeft = STRIP.centre - STRIP.reach, edgeRight = STRIP.centre + STRIP.reach;
  const at = (move) => clamp(STRIP.centre + move * scale, STRIP.edge, STRIP.width - STRIP.edge);
  const now = liveNow(current);
  const move = now.move, averageMove = now.average_move;
  const inside = now.average_inside;
  verdict.textContent = inside === null ? "—" : inside ? "Inside ✓" : "Outside ✗";
  verdict.className = inside === null ? "faint" : inside ? "inside" : "outside";
  const liveX = at(move || 0), averageX = averageMove === null ? null : at(averageMove);
  const small = `font-size="10" text-anchor="middle"`;
  const bold = `font-size="10" font-weight="700"`;
  const dashed = `stroke-dasharray="3 2"`;
  const ringY = STRIP.needleEnd - STRIP.ringRadius - 1;
  const parts = [
    `<circle cx="${STRIP.inset + STRIP.keyRadius}" cy="${STRIP.keyY}" r="${STRIP.keyRadius}" fill="${COLOUR.blue}"/>`,
    svgText(STRIP.inset + STRIP.keyRadius + STRIP.keyGap, STRIP.topY,
            `${isLive() ? "Live" : "Last"} ${fmt(move, 1, true)}`, `${bold} fill="${COLOUR.blue}"`),
    svgLine(0, ringY, STRIP.width, ringY, COLOUR.sand, 1),
  ];
  const under = [];
  if (averageHalf !== null) {
    const bandLeft = STRIP.centre - averageHalf * scale, bandRight = STRIP.centre + averageHalf * scale;
    parts.push(svgRect(bandLeft, STRIP.bandTop, bandRight - bandLeft, STRIP.bandBottom - STRIP.bandTop, 5,
                       COLOUR.band));
    under.push(svgText(bandLeft, STRIP.labelY, fmt(-averageHalf, 1), `${small} fill="${COLOUR.faint}" data-under="-1"`),
               svgText(bandRight, STRIP.labelY, fmt(averageHalf, 1, true),
                       `${small} fill="${COLOUR.faint}" data-under="1"`));
  }
  parts.push(svgLine(edgeLeft, STRIP.bandTop - 1, edgeLeft, STRIP.bandBottom + 1, COLOUR.rust, 1.5, dashed),
             svgLine(edgeRight, STRIP.bandTop - 1, edgeRight, STRIP.bandBottom + 1, COLOUR.rust, 1.5, dashed),
             svgLine(STRIP.centre, 17, STRIP.centre, STRIP.needleEnd, COLOUR.ink, 2.5),
             svgLine(liveX, 15, liveX, STRIP.needleEnd, COLOUR.blue, 3));
  if (averageX !== null) {
    parts.push(svgText(STRIP.width - STRIP.inset, STRIP.topY, `Average ${fmt(averageMove, 1, true)}`,
                       `${bold} text-anchor="end" fill="${COLOUR.ink}" data-average-label`),
               `<circle data-average-key cx="0" cy="${STRIP.keyY}" r="${STRIP.keyRadius}" fill="${COLOUR.cream}" `
               + `stroke="${COLOUR.ink}" stroke-width="1.5" stroke-dasharray="2 1.5"/>`,
               `<circle cx="${averageX}" cy="${ringY}" r="${STRIP.ringRadius}" fill="${COLOUR.cream}" `
               + `stroke="${COLOUR.ink}" stroke-width="2" ${dashed}/>`);
  }
  under.push(svgText(edgeLeft, STRIP.labelY, fmt(-endHalf, 1), `${small} fill="${COLOUR.rust}" data-under="-1"`),
             svgText(edgeRight, STRIP.labelY, fmt(endHalf, 1, true), `${small} fill="${COLOUR.rust}" data-under="1"`),
             svgText(STRIP.centre, STRIP.labelY, `Read ${fmt(current.price, 1)}`,
                     `${bold} text-anchor="middle" fill="${COLOUR.ink}" data-under="0"`));
  strip.innerHTML = parts.concat(under).join("");
  const averageLabel = strip.querySelector("[data-average-label]");
  if (averageLabel) {
    const keyX = STRIP.width - STRIP.inset - textWidth(averageLabel) - STRIP.keyGap;
    strip.querySelector("[data-average-key]").setAttribute("cx", keyX.toFixed(1));
  }
  spreadUnder(strip);
}

/** A strip label's width: measured once it is drawn on screen, else (a hidden tab) a widest-character estimate. */
function textWidth(node) {
  const measured = node.getComputedTextLength();
  return measured > 0 ? measured : node.textContent.length * LABEL_CHAR_WIDTH;
}

/** The labels under the strip: the read's price at the centre, each band-edge number at its edge, moved out past the
 *  one nearer the centre where they would touch, then in from the card's edge where they would leave it. */
function spreadUnder(strip) {
  const readHalf = textWidth(strip.querySelector('[data-under="0"]')) / 2;
  [-1, 1].forEach((side) => {
    const labels = Array.from(strip.querySelectorAll(`[data-under="${side}"]`))
      .map((node) => ({ node, x: Number(node.getAttribute("x")), half: textWidth(node) / 2 }))
      .sort((a, b) => side * (a.x - b.x));
    let inner = STRIP.centre + side * (readHalf + LABEL_GAP);
    labels.forEach((label) => {
      label.x = side < 0 ? Math.min(label.x, inner - label.half) : Math.max(label.x, inner + label.half);
      inner = label.x + side * (label.half + LABEL_GAP);
    });
    let outer = side < 0 ? STRIP.inset : STRIP.width - STRIP.inset;
    labels.slice().reverse().forEach((label) => {
      label.x = side < 0 ? Math.max(label.x, outer + label.half) : Math.min(label.x, outer - label.half);
      outer = label.x - side * (label.half + LABEL_GAP);
    });
    labels.forEach((label) => label.node.setAttribute("x", label.x.toFixed(1)));
  });
}

function renderReadsList() {
  const current = liveWindow();
  const rows = [];
  if (current && halfWidth(current.bands, "80") !== null) {
    const word = isLive() ? "Live" : current.ended ? "Ended" : "Last";
    const now = liveNow(current);
    rows.push(readRow(current.read_at, halfWidth(current.bands, "80"), now.farthest_up, now.farthest_down, now.move,
                      word, "live"));
  }
  windowList("running").forEach((window) => {
    rows.push(readRow(window.read_at, window.half_width, window.farthest_up, window.farthest_down, window.end_move,
                      "Running", "running"));
  });
  windowList("finished").forEach((window) => {
    const inside = window.grades.ends_inside;
    const word = inside === null ? titleCase(window.status || "pending") : inside ? "Inside" : "Outside";
    rows.push(readRow(window.read_at, window.half_width, window.farthest_up, window.farthest_down, window.end_move,
                      word, inside === null ? "faint" : inside ? "inside" : "outside"));
  });
  const shown = state.showAll ? rows : rows.slice(0, LIST_ROWS_SHOWN);
  const more = rows.length > shown.length
             ? `<button class="show-all" data-show-all>Show All ${fmt(rows.length)}</button>` : "";
  el("reads-list").innerHTML = shown.join("") + more || `<p class="block-note">${esc(emptyReason())}</p>`;
}

/** One list row: the read's time, a mini bar of how far price went each way against the band (dashed with a hollow
 *  end dot for a running window), the numbers and the word. */
function readRow(readAt, half, up, down, endMove, word, kind) {
  const colour = kind === "live" || kind === "running" ? COLOUR.blue : kind === "inside" ? COLOUR.green
               : kind === "outside" ? COLOUR.rust : COLOUR.grey;
  const scale = half ? ROW.reach / half : 0;
  const spanLeft = clamp(ROW.centre - (down || 0) * scale, 2, ROW.width - 2);
  const spanRight = clamp(ROW.centre + (up || 0) * scale, 2, ROW.width - 2);
  const bar = [svgRect(ROW.trackLeft, 2, ROW.trackRight - ROW.trackLeft, 6, 3, COLOUR.track)];
  if (kind === "running" && up !== null && down !== null && half) {
    bar.push(svgLine(spanLeft, 5, spanRight, 5, colour, 3, `stroke-dasharray="3 2" stroke-opacity="0.7"`));
  } else if (up !== null && down !== null && half) {
    const innerLeft = Math.max(spanLeft, ROW.trackLeft), innerRight = Math.min(spanRight, ROW.trackRight);
    if (innerRight > innerLeft) bar.push(svgRect(innerLeft, 3.5, innerRight - innerLeft, 3, 1.5, colour, 0.45));
    if (spanLeft < ROW.trackLeft) bar.push(svgRect(spanLeft, 3.5, ROW.trackLeft - spanLeft, 3, 1.5, colour, 0.6));
    if (spanRight > ROW.trackRight) {
      bar.push(svgRect(ROW.trackRight, 3.5, spanRight - ROW.trackRight, 3, 1.5, colour, 0.6));
    }
  }
  if (endMove !== null && half) {
    const dotX = clamp(ROW.centre + endMove * scale, 5, ROW.width - 5);
    bar.push(kind === "running" ? `<circle cx="${dotX}" cy="5" r="3" fill="${COLOUR.cream}" stroke="${colour}" `
                                  + `stroke-width="1.5"/>`
                                : `<circle cx="${dotX}" cy="5" r="3.5" fill="${colour}"/>`);
  }
  const travelled = up === null || down === null ? "—" : `${fmt(up, 0, true)} −${fmt(down)}`;
  const span = half ? `${travelled} · ±${fmt(half)}` : "—";
  return `<div class="read-row"><span class="when ${kind === "live" ? "live" : ""}">${clock(readAt)}</span>`
       + `<svg viewBox="0 0 ${ROW.width} 10" aria-hidden="true">${bar.join("")}</svg>`
       + `<span class="span">${span}</span><span class="word ${kind}">${esc(word)}</span></div>`;
}

function renderScores() {
  const score = windows() ? windows().score[state.channel] : null;
  const read = explainedRead();
  const promised = bandPortion();
  const targets = { ends_inside: promised, average_inside: promised,
                    never_left: read ? read.stays_inside_share : null };
  const labels = { ends_inside: "Ends Inside", average_inside: "Average Inside", never_left: "Never Left" };
  el("scores").innerHTML = Object.keys(labels).map((word) => {
    const counts = score && score[word];
    const graded = counts ? counts.graded : 0;
    const good = graded && targets[word] !== null ? counts.yes / graded >= targets[word] : null;
    const colour = good === null ? "faint" : good ? "inside" : "outside";
    const yes = graded ? fmt(counts.yes) : "—";
    return `<div class="score"><b class="${colour}">${yes}<small>/${fmt(graded)}</small></b>`
         + `<span>${labels[word]}</span></div>`;
  }).join("");
}

// ---- Sight: how far, which way, feeds ------------------------------------------------------------------------------

function readLine(read, minutes, short = false) {
  if (!read) return "—";
  const window = short ? "" : ` · ${minutes}-Min Window`;
  const ends = read.ends_at ? ` · Ends ${clock(read.ends_at)}` : "";
  const capped = read.clipped ? " · Capped At The Close" : "";
  const newer = newestReadDiffers(read) ? ` · Newest Read ${clock(range().read_at)}` : "";
  const thin = read.reason && String(read.reason).includes("thin_fit") ? ` · ${titleCase(read.reason)}` : "";
  return `Read At ${clock(read.read_at)}${window}${ends}${capped}${newer}${thin}`;
}

/** A read Sight and Steps can draw: not blind, with an end band. */
function usableRead(read) {
  return Boolean(read) && read.status !== "blind" && halfWidth(read.bands, "80") !== null;
}

function renderSight() {
  const read = explainedRead();
  const minutes = CHANNELS[state.channel];
  const usable = usableRead(read);
  note("sight-note", usable ? "" : emptyReason());
  el("sight-read-line").textContent = readLine(read, minutes, true);
  renderSpeedBars(usable ? read : null);
  el("speed-value").textContent = usable ? fmt(read.speed, 2) : "—";
  renderChain(usable ? read : null, minutes);
  renderRangeBar(usable ? read : null);
  renderSightChecks(usable ? read : null);
  renderWideners(usable ? read : null);
}

function renderSpeedBars(read) {
  const bars = read ? [["Fast", "5-Min Memory", read.speed_fast, 0.5], ["Used", "15-Min Memory", read.speed, 0.98],
                       ["Slow", "30-Min Memory", read.speed_slow, 0.5]] : [];
  const most = Math.max(...bars.map((bar) => bar[2] || 0), 0.01);
  const barHeight = (value) => (value === null ? 0 : (SPEED.baseline - SPEED.top) * value / most);
  const parts = bars.map(([name, memory, value, opacity], index) => {
    const x = SPEED.left + index * SPEED.step, height = barHeight(value), y = SPEED.baseline - height;
    const inkOrWhite = opacity > 0.9 ? COLOUR.white : COLOUR.ink;
    const middle = x + SPEED.barWidth / 2;
    return svgRect(x, y, SPEED.barWidth, height, 5, COLOUR.blue, opacity)
         + svgText(middle, SPEED.baseline - 6, fmt(value, 2),
                   `font-size="10" font-weight="700" text-anchor="middle" fill="${inkOrWhite}"`)
         + svgText(middle, SPEED.nameY, name,
                   `font-size="9.5" font-weight="700" text-anchor="middle" fill="${COLOUR.faint}"`)
         + svgText(middle, SPEED.memoryY, memory, `font-size="8" text-anchor="middle" fill="${COLOUR.faint}"`);
  });
  if (read && read.speed !== null) {
    const usedY = SPEED.baseline - barHeight(read.speed);
    parts.push(svgLine(0, usedY, SPEED.width, usedY, COLOUR.ink, 1.4, `stroke-dasharray="4 3"`));
  }
  parts.push(svgLine(0, SPEED.baseline, SPEED.width, SPEED.baseline, COLOUR.sand, 1));
  el("speed-bars").innerHTML = parts.join("");
}

function widenersApplied(read) {
  return read && read.dials_product ? read.dials_product.applied : null;
}

function renderChain(read, minutes) {
  const bandHalf = read ? halfWidth(read.bands, "80") : null;
  const applied = widenersApplied(read);
  el("chain").innerHTML = `<span class="box">${read ? fmt(read.speed, 2) : "—"}</span>`
    + `<span class="arrow">× √${minutes} →</span><span class="box">${plusMinus(read && read.swing)} Swing</span>`
    + `<span class="arrow">× ${read ? fmt(read.stretch, 2) : "—"} × ${fmt(applied, 2)} Band Wideners →</span>`
    + `<span class="box box-blue">${plusMinus(bandHalf)}</span>`;
}

function renderRangeBar(read) {
  const bandHalf = read ? halfWidth(read.bands, "80") : null;
  el("range-bar").hidden = !bandHalf;
  if (!bandHalf) {
    el("range-bar").innerHTML = "";
    return;
  }
  const scale = RANGE_BAR.reach / bandHalf, averageHalf = halfWidth(read.bands, "average_80");
  const rung = Number(read.rung_points);
  const bold = `font-size="10" font-weight="700" text-anchor="middle"`;
  const parts = [svgRect(RANGE_BAR.centre - bandHalf * scale, 8, 2 * bandHalf * scale, 20, 6, COLOUR.blue, 0.14)];
  if (averageHalf !== null) {
    parts.push(svgRect(RANGE_BAR.centre - averageHalf * scale, 8, 2 * averageHalf * scale, 20, 5, COLOUR.blue, 0.22),
               svgText(RANGE_BAR.centre - averageHalf * scale + 3, 22, `Avg ${plusMinus(averageHalf)}`,
                       `font-size="9.5" fill="${COLOUR.blue}"`));
  }
  const rungLeft = RANGE_BAR.centre - rung * scale;
  const rungRight = clamp(RANGE_BAR.centre + rung * scale, 0, RANGE_BAR.width);
  const dashed = `stroke-dasharray="2 2"`;
  const labelFits = rungRight + 24 < RANGE_BAR.centre + bandHalf * scale;
  const labelX = !Number.isFinite(rung) ? RANGE_BAR.width - 20 : labelFits ? rungRight + 3 : rungRight - 3;
  if (Number.isFinite(rung)) {
    parts.push(svgLine(rungLeft, 4, rungLeft, 32, COLOUR.ink, 1, dashed),
               svgLine(rungRight, 4, rungRight, 32, COLOUR.ink, 1, dashed));
  }
  parts.push(svgLine(RANGE_BAR.centre, 3, RANGE_BAR.centre, 33, COLOUR.ink, 2.5),
             svgText(RANGE_BAR.centre - bandHalf * scale, 45, fmt(read.bands["80"].low),
                     `${bold} fill="${COLOUR.blue}"`),
             svgText(RANGE_BAR.centre, 45, `Read ${fmt(read.price)}`, `${bold} fill="${COLOUR.ink}"`),
             svgText(RANGE_BAR.centre + bandHalf * scale, 45, fmt(read.bands["80"].high),
                     `${bold} fill="${COLOUR.blue}"`),
             svgText(labelX, 22, `±${fmt(rung)}`,
                     `font-size="9.5" text-anchor="${labelFits ? "start" : "end"}" fill="${COLOUR.ink}"`));
  el("range-bar").innerHTML = parts.join("");
}

function optionsWord(read) {
  const check = read ? read.options_check : null;
  return { word: check === "agree" ? "Agree" : check === "differ" ? "Differ" : "—",
           mark: check === "agree" ? "✓" : check === "differ" ? "!" : "·", good: check === "agree" };
}

function newsCheck(read) {
  const block = range();
  const covered = valueOf("does_calendar_cover_day");
  const next = block && block.releases && block.releases.length ? block.releases[0] : null;
  const dial = read.dials ? read.dials.release_window : null;
  const quiet = !next && (!dial || dial.raw === 1);
  if (covered === 0 || covered === false) {
    return { name: "Calendar Doesn't Cover Today", value: "—", good: false };
  }
  const name = next ? `${esc(titleCase(next.name))} At ${clock(next.at)}`
             : quiet ? "No News Left Today" : "News In The Window";
  return { name, value: dial ? `×${fmt(dial.applied, 2)}` : "—", good: quiet };
}

function checkRow(mark, name, value, cls) {
  return `<div class="check"><span class="name">${mark}${name}</span><span class="value ${cls}">${value}</span></div>`;
}

function markIcon(good, glyph) {
  return `<i class="mark ${good ? "mark-green" : "mark-amber"}">${glyph}</i>`;
}

function renderSightChecks(read) {
  if (!read) {
    el("sight-checks").innerHTML = "";
    return;
  }
  const options = optionsWord(read);
  const news = newsCheck(read);
  const share = read.stays_inside_share;
  el("sight-checks").innerHTML =
    checkRow(markIcon(options.good, options.mark), `Options ${options.word}`,
             `${plusMinus(read.options_swing)} vs ${plusMinus(read.swing)}`, options.good ? "inside" : "")
    + checkRow(markIcon(news.good, news.good ? "✓" : "!"), news.name, news.value, news.good ? "inside" : "")
    + checkRow(`<i class="dot-sand"></i>`, `Never Leaves ±${fmt(read.rung_points)}`,
               share === null ? "—" : `${fmt(share * 100)}% At Today's Speed`, "");
}

/** A multiplier in words: ×2.6, ×1.36. */
function times(value) {
  return `×${fmt(value, 2).replace(/(\.\d)0$/, "$1")}`;
}

/** One band widener's standing on a read: on only once it has earned weight (then the multiplier the band used), else
 *  what it would add and that it is not counted yet, or when it next applies. Sight's list and Steps' line read it. */
function widenerStatus(name, read) {
  const dial = read.dials ? read.dials[name] : null;
  const raw = dial ? dial.raw : null;
  const moved = raw !== null && raw !== undefined && raw !== 1;
  if (dial && dial.weight) return { on: true, by: times(dial.applied), words: `Counted ${times(dial.applied)}` };
  if (COUNTED_WIDENERS.includes(name)) return { on: false, words: "Watching · Not Counted Yet" };
  if (moved && name === "release_window") {
    // the newest release before the window's end is one overlapping it whenever any does
    const due = (range() && range().releases || []).filter((release) => ms(release.at) < ms(read.ends_at))
      .reduce((newest, release) => (!newest || ms(release.at) > ms(newest.at) ? release : newest), null);
    return { on: false, words: `Due ${due ? `${clock(due.at)} ` : ""}${times(raw)} · Not Counted Yet` };
  }
  if (moved) return { on: false, words: `Due ${times(raw)} · Not Counted Yet` };
  const fedAt = range() && range().fed_decision_at;
  const session = state.feed && state.feed.session;
  const closingFrom = session && session.closing_window_from;
  return { on: false, words: { fed_window: fedAt ? `From ${clock(fedAt)}` : "Not Today", release_window: "None Due",
                               closing_window: closingFrom ? `From ${clock(closingFrom)}` : "Not Today" }[name] };
}

/** Sight's list of every band widener with its standing, the one place all of them are named. */
function renderWideners(read) {
  el("sight-wideners").hidden = !read;
  el("widener-rows").innerHTML = read ? Object.keys(WIDENER_NAMES).map((name) => {
    const status = widenerStatus(name, read);
    return `<div class="widener"><span class="name">${WIDENER_NAMES[name]}</span>`
         + `<span class="status ${status.on ? "on" : ""}">${esc(status.words)}</span></div>`;
  }).join("") : "";
}

function leanMeter(lean, width = METER.width) {
  const mid = width / 2;
  const track = svgRect(0, 4, width, 4, 2, COLOUR.rule) + svgLine(mid, 1, mid, 11, COLOUR.faint, 1);
  if (lean === null || lean === undefined) return track;
  const x = clamp(lean * width, 4, width - 4);
  const tilt = lean - 0.5;
  const colour = Math.abs(tilt) <= LEAN_DEAD_ZONE ? COLOUR.grey : tilt < 0 ? COLOUR.rust : COLOUR.blue;
  const fill = Math.abs(x - mid) > 0.5 ? svgRect(Math.min(x, mid), 4, Math.abs(x - mid), 4, 2, colour, 0.45) : "";
  return track + fill + `<circle cx="${x}" cy="6" r="4" fill="${colour}"/>`;
}

/** The call in words: the engine's text once any trust is earned, Coin Flip while the applied trust is 0. */
function directionWords(direction) {
  if (!direction || direction.chance_up_if_moves === null || direction.chance_up_if_moves === undefined) return "—";
  const words = direction.trust_applied === 0 || !direction.text ? "Coin Flip" : titleCase(direction.text);
  return `Up ${fmt(direction.chance_up_if_moves * 100)}% · ${words}`;
}

/** Give keyboard focus back to the push row that held it, since re-rendering the rows replaces the buttons. */
function keepFocus(name) {
  const row = name && document.querySelector(`[data-push="${CSS.escape(name)}"]`);
  if (row) row.focus();
}

function renderPush() {
  const block = range(), ch = channel();
  const push = block ? block.push : null;
  const direction = ch ? ch.direction : null;
  const weight = direction ? direction.trust_applied : null;
  note("push-note", block ? "" : emptyReason());
  el("push-weight").textContent = weight === null ? "—" : `Weight ${fmt(weight, weight ? 2 : 0)}`;
  const failed = reasons().includes("push_failed");
  const rows = (push || []).map((entry) => {
    const entryWeight = entry.weight_by_channel ? entry.weight_by_channel[state.channel] : 0;
    const answer = entry.reason ? `— · ${titleCase(entry.reason)}` : entry.answer || "—";
    const open = state.openPush.has(entry.name);
    return `<button class="push-row" type="button" data-push="${esc(entry.name)}" aria-expanded="${open}">`
         + `<span class="label">${esc(entry.label || titleCase(entry.name))}`
         + `${entryWeight ? ` · ${fmt(entryWeight, 2)}` : ""}</span>`
         + `<svg viewBox="0 0 ${METER.width} 12" aria-hidden="true">${leanMeter(entry.lean_if_trusted)}</svg>`
         + `<span class="short">${esc(answer)}</span>`
         + `<span class="figure"${open ? "" : " hidden"}>${esc(entry.value || "No Figure On This Read")}</span>`
         + `</button>`;
  });
  const empty = failed ? "Readings Failed On This Read" : block ? "No Readings On This Read" : "";
  el("push-rows").innerHTML = rows.join("") || (empty ? `<p class="block-note">${empty}</p>` : "");
  el("push-rows").classList.toggle("earned", Boolean(weight));
  el("push-call-label").textContent = weight ? `Counted At Weight ${fmt(weight, 2)} →`
                                             : "Not Counted Until Earned →";
  el("push-call").textContent = directionWords(direction);
}

/** The live price's row as the page sees it now: Live while ticks arrive, else its age while the session is open,
 *  else the moment of its last trade (a grey dot: closed, not broken); a page that never had a tick shows the
 *  read's own row. */
function livePriceRow(feed) {
  const tick = sessionTick();
  const at = tick ? tick.at : feed.as_of;
  if (tickFresh()) return { status: "live", words: "Live" };
  if (!at) return { status: feed.status, words: titleCase(feed.status) };
  if (sessionOpen()) return { status: "partial", words: `Partial · ${ago((Date.now() - ms(at)) / 1000)}` };
  return { status: "quiet", words: `Last ${clock(at, true)}` };
}

/** One feed row's dot and words: an age for live and minute feeds, the as_of words for the once-a-day and hand-kept
 *  ones (a grey dot while they are fresh for their kind), the status word first whenever a feed is not fresh. */
function feedRow(name, feed) {
  if (feed.cadence === "live") return livePriceRow(feed);
  const age = feed.age_s === null || feed.age_s === undefined ? "" : ago(feed.age_s);
  const asOf = AS_OF_WORDS[name] && feed.as_of ? AS_OF_WORDS[name](feed.as_of) : "";
  const quiet = QUIET_CADENCES.includes(feed.cadence);
  const when = quiet ? asOf || age : age;
  const words = [feed.status === "live" ? "" : titleCase(feed.status), when, feed.note || ""].filter(Boolean);
  return { status: feed.status === "live" && quiet ? "quiet" : feed.status, words: words.join(" · ") || "Live" };
}

function renderFeeds() {
  const feeds = state.feed && state.feed.feeds ? state.feed.feeds : {};
  const names = Object.keys(feeds);
  const rows = names.map((name) => feedRow(name, feeds[name]));
  const fresh = rows.filter((row) => row.status === "live" || row.status === "quiet").length;
  note("feeds-note", names.length ? "" : emptyReason());
  el("feeds-pill").textContent = names.length ? `${fresh} Of ${names.length} OK` : "—";
  el("feeds-pill").className = "pill " + (fresh === names.length ? "pill-green" : "pill-purple");
  el("feeds-grid").innerHTML = names.map((name, index) =>
    `<div class="feed"><span class="name"><i class="feed-dot ${esc(rows[index].status)}"></i>`
    + `${esc(FEED_LABELS[name] || titleCase(name))}</span><span class="age">${esc(rows[index].words)}</span></div>`
  ).join("");
  const login = state.feed && state.feed.login;
  if (!login || !login.state) {
    el("login-line").textContent = "Schwab Login · —";
  } else if (login.state === "ok" && login.expires_at) {
    el("login-line").textContent = `Schwab Login Good Until ${dayWord(login.expires_at)} · ${fmt(login.hours_left)} `
                                 + "H Left";
  } else {
    el("login-line").textContent = `Schwab Login ${titleCase(login.state)}`;
  }
  const nightly = state.feed && state.feed.nightly;
  if (!nightly || !nightly.expected) {
    el("nightly-line").textContent = "Nightly · —";
    el("nightly-line").className = "faint-small";
  } else {
    const done = Boolean(nightly.done_for) && nightly.done_for >= nightly.expected;
    el("nightly-line").textContent = done ? `Nightly Done For ${dayWord(nightly.expected)}`
                                          : `Nightly Not Done For ${dayWord(nightly.expected)}`
                                            + `${nightly.done_for ? ` · Last ${dayWord(nightly.done_for)}` : ""}`;
    el("nightly-line").className = done ? "faint-small" : "faint-small outside";
  }
}

// ---- Steps: the build-up, the checks, the answer -------------------------------------------------------------------

function renderSteps() {
  const read = explainedRead();
  const minutes = CHANNELS[state.channel];
  const usable = usableRead(read);
  el("steps-sub").textContent = readLine(read, minutes);
  note("steps-note", usable ? "" : emptyReason());
  renderStepBars(usable ? read : null, minutes);
  renderStepChecks(usable ? read : null, minutes);
}

function tag(kind) {
  return `<span class="tag tag-${kind}">${titleCase(kind)}</span>`;
}

function stepBar(number, name, kind, value, sub, svg) {
  return `<div class="step"><div class="step-head"><span class="name">${number} · ${name}${tag(kind)}</span>`
       + `<span class="value">${value}</span></div><span class="faint-small">${sub}</span>${svg}</div>`;
}

function renderStepBars(read, minutes) {
  if (!read) {
    el("steps-bars").innerHTML = "";
    return;
  }
  const bandHalf = halfWidth(read.bands, "80"), averageHalf = halfWidth(read.bands, "average_80");
  const scale = RANGE_BAR.reach / bandHalf;
  const edge = (reach) => (RANGE_BAR.centre - reach * scale).toFixed(1);
  const farEdge = (reach) => (RANGE_BAR.centre + reach * scale).toFixed(1);
  const rect = (reach, opacity) => svgRect(edge(reach), 8, (2 * reach * scale).toFixed(1), 14, 5, COLOUR.blue,
                                           opacity);
  const centreLine = svgLine(RANGE_BAR.centre, 5, RANGE_BAR.centre, 25, COLOUR.ink, 1.6);
  const funnel = (from, to) =>
    `<path d="M${edge(from)} 0 L${edge(to)} 8 M${farEdge(from)} 0 L${farEdge(to)} 8" stroke="${COLOUR.sand}" `
    + `stroke-width="1" stroke-dasharray="2 2" fill="none"/>`;
  const bar = (inner, height = 26) =>
    `<svg viewBox="0 0 ${RANGE_BAR.width} ${height}" aria-hidden="true">${inner}</svg>`;
  const bold = `font-size="10" font-weight="700" text-anchor="middle" fill="${COLOUR.blue}"`;
  const speed = read.speed, swing = read.swing, share = read.stays_inside_share, windowsCount = read.fit_windows;
  const neverSub = share === null ? "—"
    : windowsCount ? `${fmt(Math.round(share * windowsCount))} Of ${fmt(windowsCount)} Past Windows At Today's Speed`
    : `${fmt(share * 100)}% Of Past Windows At Today's Speed`;
  const rung = Number(read.rung_points);
  const promised = bandPortion();
  const bandName = promised === null ? "End Band" : `End Band · ${fmt(promised * 10)} In 10`;
  const dashedBox = Number.isFinite(rung) ? svgRect(edge(rung), 8, (2 * rung * scale).toFixed(1), 14, 5, "none", 1,
                                                    `stroke="${COLOUR.ink}" stroke-width="1.3" stroke-dasharray="3 2"`)
                                          : "";
  el("steps-bars").innerHTML =
    stepBar(1, "Speed", "today", fmt(speed, 2), "One Minute's Typical Move · 15-Min Memory",
            bar(rect(speed, 0.55) + centreLine))
    + stepBar(2, "Swing", "today", plusMinus(swing), `Over ${minutes} Minutes · ${fmt(speed, 2)} × √${minutes}`,
              bar(funnel(speed, swing) + rect(swing, 0.35) + centreLine))
    + stepBar("3–4", bandName, "history", plusMinus(bandHalf),
              `Stretched By Past Days · ${fmt(swing, 1)} × ${fmt(read.stretch, 2)} `
              + `× ${fmt(widenersApplied(read), 2)} Band Wideners`,
              bar(funnel(swing, bandHalf) + rect(bandHalf, 0.18) + centreLine
                  + svgText(edge(bandHalf), 34, fmt(read.bands["80"].low), bold)
                  + svgText(farEdge(bandHalf), 34, fmt(read.bands["80"].high), bold), 36))
    + stepBar("4b", "Average Band", "history", plusMinus(averageHalf),
              `Where The Average Lands · ${fmt(swing, 1)} × ${fmt(read.average_stretch, 2)}`,
              bar((averageHalf === null ? "" : rect(averageHalf, 0.3)) + centreLine))
    + stepBar(5, `Never Leaves ±${fmt(rung)}`, "history", share === null ? "—" : `${fmt(share * 100)}%`,
              neverSub, bar(dashedBox + centreLine));
}

function widenerGauge(applied) {
  const angle = applied === null ? 0 : clamp((applied - 1) * 150, -90, 90) * Math.PI / 180;
  const x = (28 + 17 * Math.sin(angle)).toFixed(1), y = (28 - 17 * Math.cos(angle)).toFixed(1);
  return `<path d="M8 28 A 20 20 0 0 1 48 28" fill="none" stroke="${COLOUR.rule}" stroke-width="5" `
       + `stroke-linecap="round"/>`
       + svgLine(28, 28, x, y, COLOUR.ink, 2, `stroke-linecap="round"`) + `<circle cx="28" cy="28" r="3" `
       + `fill="${COLOUR.ink}"/>`;
}

function stepCheck(number, name, kind, sub, svg, value) {
  return `<div class="step-check"><div class="text"><span class="name">${number} · ${name}${tag(kind)}</span>`
       + `<span class="sub">${sub}</span></div>${svg}<span class="value">${value}</span></div>`;
}

/** Steps' band wideners line: the ones on now by plain name and multiplier, or what they are for while none is. */
function widenersLine(read) {
  const on = Object.keys(WIDENER_NAMES).map((name) => [name, widenerStatus(name, read)])
    .filter(([, status]) => status.on);
  return on.length ? on.map(([name, status]) => `${WIDENER_NAMES[name]} ${status.by}`).join(" · ")
                   : "Widen The Band For Known Busy Moments · None Counted Yet";
}

function renderStepChecks(read, minutes) {
  if (!read) {
    el("steps-checks").innerHTML = "";
    return;
  }
  const options = optionsWord(read);
  const most = Math.max(read.options_swing || 0, read.swing || 0, 0.01);
  const used = read.minutes_left === null ? minutes : Math.min(minutes, read.minutes_left);
  const chance = read.chance_up_if_moves;
  const trust = read.trust_applied === null ? "—" : fmt(read.trust_applied, read.trust_applied ? 2 : 0);
  const applied = widenersApplied(read);
  el("steps-checks").innerHTML =
    stepCheck(6, "Options", "check", `${fmt(read.straddle, 1)} × √(${fmt(used)} ÷ ${fmt(read.minutes_left)})`,
              `<svg viewBox="0 0 56 34" role="img" aria-label="Options swing next to tape swing">`
              + svgRect(3, 7, (50 * (read.options_swing || 0) / most).toFixed(1), 8, 3, COLOUR.greenDeep, 0.55)
              + svgRect(3, 19, (50 * (read.swing || 0) / most).toFixed(1), 8, 3, COLOUR.blue, 0.45) + `</svg>`,
              `${plusMinus(read.options_swing)} ${options.mark}`)
    + stepCheck(7, "Band Wideners", "earned", widenersLine(read),
                `<svg viewBox="0 0 56 34" role="img" aria-label="Band wideners at times ${fmt(applied, 2)}">`
                + `${widenerGauge(applied)}</svg>`, `×${fmt(applied, 2)}`)
    + stepCheck(8, "Direction", "earned", chance === null ? "—" : `50 + ${trust} × (Lean − 50)`,
                `<svg viewBox="0 0 56 34" role="img" aria-label="Direction meter"><g transform="translate(3,11)">`
                + `${leanMeter(chance, METER.small)}</g>`
                + svgText(3, 32, "Down", `font-size="7" fill="${COLOUR.rust}"`)
                + svgText(53, 32, "Up", `font-size="7" text-anchor="end" fill="${COLOUR.blue}"`) + `</svg>`,
                chance === null ? "—" : `Up ${fmt(chance * 100)}%`);
}

function renderAnswer() {
  const read = explainedRead();
  const band = read ? read.bands["80"] : null, chance = read ? read.chance_up_if_moves : null;
  const between = band && band.half_width !== null ? `${fmt(band.low)} – ${fmt(band.high)}` : "—";
  el("answer").innerHTML =
    `<div><span>Ends Between</span><span>${between}</span></div>`
    + `<div><span>Average Within</span><span>${plusMinus(read && halfWidth(read.bands, "average_80"))}</span></div>`
    + `<div><span>Direction</span><span>${chance === null ? "—" : `Up ${fmt(chance * 100)}%`}</span></div>`;
}

// ---- tabs, switches, timers ---------------------------------------------------------------------------------------

function showTab(name) {
  remember("tab", name);
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${name}`; });
  document.querySelectorAll(".tabs button").forEach((button) => {
    if (button.dataset.tab === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  window.scrollTo(0, 0);
  // the strip lays its labels out by their drawn widths, which a hidden tab cannot measure
  if (name === "read" && state.feed) guarded(renderStrip, "strip-note");
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  const switchButton = event.target.closest("[data-channel]");
  if (tab) showTab(tab.dataset.tab);
  if (switchButton) {
    state.channel = switchButton.dataset.channel;
    state.showAll = false;
    remember("channel", state.channel);
    render();
  }
  if (event.target.closest("[data-show-all]")) {
    state.showAll = true;
    render();
  }
  const pushRow = event.target.closest("[data-push]");
  if (pushRow) {
    if (!state.openPush.delete(pushRow.dataset.push)) state.openPush.add(pushRow.dataset.push);
    guarded(renderPush, "push-note");
    keepFocus(pushRow.dataset.push);
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    closePriceStream();
  } else {
    loadFeed();
    openPriceStream();
  }
});
state.channel = CHANNELS[remembered("channel", state.channel)] ? remembered("channel", state.channel) : state.channel;
showTab(["read", "sight", "steps"].includes(remembered("tab", "read")) ? remembered("tab", "read") : "read");
loadFeed();
openPriceStream();
document.fonts.ready.then(() => guarded(renderStrip, "strip-note"));

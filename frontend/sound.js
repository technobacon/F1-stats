/* GridMaster — sound effects.
 *
 * Self-contained, like the rest of the project (first-party analytics, built-in
 * accounts): every effect is SYNTHESISED at runtime with the Web Audio API, so
 * there are NO binary audio assets to ship, host, license or fetch over the
 * network. The whole feel-good layer is a few hundred bytes of code.
 *
 * Design notes:
 *  - One shared AudioContext, created lazily on the first play. Browsers require
 *    a user gesture to start audio; since every play is triggered by a click,
 *    keypress or pointer drag, the context resumes on first use.
 *  - A single master gain keeps the whole palette at a comfortable level — loud
 *    enough to feel satisfying, quiet enough to never startle (MASTER ≈ 0.5).
 *    Each effect's own peak is tuned UNDER that so nothing clips or jumps out.
 *  - On/off is persisted in localStorage and honoured before any node is built,
 *    so muting is instant and total.
 *
 * Public API (window.Sound):
 *    Sound.play(name)   -> fire an effect by name (see EFFECTS)
 *    Sound.tick()       -> the throttled slider "click" (its own rate limit)
 *    Sound.isOn()       -> current on/off state
 *    Sound.toggle()     -> flip on/off, persist, return the new state
 *    Sound.setOn(bool)  -> set on/off explicitly, persist
 */
const Sound = (() => {
  const PREF_KEY = "f1sg_sound_on";
  const MASTER = 0.5;            // overall volume ceiling (0..1)

  let ctx = null, master = null, noise = null;
  let on = localStorage.getItem(PREF_KEY) !== "0";   // default ON

  function ensureCtx() {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;                            // no Web Audio — silently no-op
    try {
      ctx = new AC();
      master = ctx.createGain();
      master.gain.value = MASTER;
      master.connect(ctx.destination);
    } catch { ctx = null; }
    return ctx;
  }

  // Resume a context the browser auto-suspended (autoplay policy). Always called
  // from inside a user gesture, so it's allowed.
  function resume() { if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {}); }

  // A reusable 1-second white-noise buffer — the raw material for every engine
  // whoosh and percussive click.
  function noiseBuffer() {
    if (noise) return noise;
    const len = ctx.sampleRate;                      // ~1s
    noise = ctx.createBuffer(1, len, ctx.sampleRate);
    const data = noise.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    return noise;
  }

  // ── Low-level voices ──────────────────────────────────────────────────────

  /* A pitched blip: a single oscillator with an attack/decay envelope, optionally
   * gliding from one frequency to another (used for beeps, chimes, confirms). */
  function blip(t0, freq, peak, dur, { type = "triangle", glideTo = null } = {}) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (glideTo) osc.frequency.exponentialRampToValueAtTime(glideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.008);     // fast attack (a "click" edge)
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);     // smooth decay
    osc.connect(g).connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  /* A car drive-by: white noise through a band-pass filter whose centre frequency
   * rises as the car approaches and falls as it recedes (a Doppler sweep), with a
   * matching volume swell. Optional stereo pan places the car left/right so a pack
   * of them feels spread across the track. */
  function whoosh(t0, { dur = 0.7, peak = 0.3, center = 880, pan = 0 } = {}) {
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer();
    src.playbackRate.value = 1 + Math.random() * 0.2;
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 1.1;
    bp.frequency.setValueAtTime(center * 0.5, t0);
    bp.frequency.exponentialRampToValueAtTime(center * 1.7, t0 + dur * 0.45);   // approach
    bp.frequency.exponentialRampToValueAtTime(center * 0.55, t0 + dur);         // recede
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + dur * 0.45);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    let tail = g;
    if (ctx.createStereoPanner) {                    // spread the pack across the grid
      const p = ctx.createStereoPanner();
      p.pan.value = Math.max(-1, Math.min(1, pan));
      g.connect(p); tail = p;
    }
    src.connect(bp).connect(g);
    tail.connect(master);
    src.start(t0);
    src.stop(t0 + dur + 0.05);
  }

  // ── Effects ────────────────────────────────────────────────────────────────

  /* Slider notch: a tight, dry click — like a wheel ticking past a detent. Short
   * enough to fire on every integer the guess crosses without turning to mush. */
  function clickTick(t0) {
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer();
    src.playbackRate.value = 1.8;
    const hp = ctx.createBiquadFilter();
    hp.type = "highpass"; hp.frequency.value = 1800;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.16, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.025);
    src.connect(hp).connect(g).connect(master);
    src.start(t0);
    src.stop(t0 + 0.04);
  }

  /* Lock-in: a confident two-note confirm with a percussive edge — the guess is
   * committed. */
  function lockIn(t0) {
    blip(t0, 480, 0.22, 0.10, { type: "square", glideTo: 360 });
    blip(t0 + 0.05, 720, 0.20, 0.16, { type: "triangle", glideTo: 880 });
  }

  /* Riser: the anticipation build under the answer slide — a saw sweeping up in
   * pitch with an opening low-pass and a noise "air" layer, swelling toward the
   * reveal. Timed to land just as the answer marker arrives (~2.3s). */
  function riser(t0) {
    const dur = 2.3;
    const osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(110, t0);
    osc.frequency.exponentialRampToValueAtTime(880, t0 + dur);
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(300, t0);
    lp.frequency.exponentialRampToValueAtTime(4500, t0 + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.16, t0 + dur * 0.85);  // swell
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur + 0.12); // resolve at the top
    osc.connect(lp).connect(g).connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.2);

    // Air layer: rising filtered noise reinforces the build.
    const src = ctx.createBufferSource();
    src.buffer = noiseBuffer();
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass"; bp.Q.value = 0.8;
    bp.frequency.setValueAtTime(400, t0);
    bp.frequency.exponentialRampToValueAtTime(3500, t0 + dur);
    const ng = ctx.createGain();
    ng.gain.setValueAtTime(0.0001, t0);
    ng.gain.exponentialRampToValueAtTime(0.07, t0 + dur * 0.85);
    ng.gain.exponentialRampToValueAtTime(0.0001, t0 + dur + 0.12);
    src.connect(bp).connect(ng).connect(master);
    src.start(t0);
    src.stop(t0 + dur + 0.2);
  }

  /* Green sector (within 25%): a single car streaking past. */
  function greenSector(t0) {
    whoosh(t0, { dur: 0.75, peak: 0.34, center: 860, pan: 0 });
  }

  /* Purple sector (within 10%, the fastest): a whole pack thunders by — several
   * staggered drive-bys panned across the grid, capped with a bright sparkle so
   * the elite result feels celebratory. */
  function purpleSector(t0) {
    whoosh(t0,        { dur: 0.62, peak: 0.30, center: 820,  pan: -0.6 });
    whoosh(t0 + 0.10, { dur: 0.60, peak: 0.28, center: 1020, pan: 0.5 });
    whoosh(t0 + 0.22, { dur: 0.66, peak: 0.26, center: 700,  pan: -0.1 });
    whoosh(t0 + 0.34, { dur: 0.58, peak: 0.24, center: 1180, pan: 0.3 });
    blip(t0 + 0.40, 1568, 0.12, 0.22, { type: "triangle", glideTo: 2093 });  // sparkle
  }

  /* Lights out: the F1 start sequence — five red lights illuminate one by one,
   * then go OUT and we launch. Compressed to keep the drama tight (~1.8s): five
   * even beeps, a beat of silence, then an engine surge off the line. */
  function lightsOut(t0) {
    const gap = 0.30;
    for (let i = 0; i < 5; i++) blip(t0 + i * gap, 700, 0.18, 0.22, { type: "square" });
    const go = t0 + 5 * gap + 0.22;     // "...and they're away!"
    // Launch surge: low saw rising fast + a forward whoosh.
    const osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(90, go);
    osc.frequency.exponentialRampToValueAtTime(420, go + 0.5);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, go);
    g.gain.exponentialRampToValueAtTime(0.22, go + 0.06);
    g.gain.exponentialRampToValueAtTime(0.0001, go + 0.6);
    osc.connect(g).connect(master);
    osc.start(go);
    osc.stop(go + 0.7);
    whoosh(go + 0.05, { dur: 0.6, peak: 0.26, center: 900, pan: 0 });
  }

  /* Achievement unlocked: a quick, bright rising arpeggio (a major triad). */
  function achievement(t0) {
    [523.25, 659.25, 783.99].forEach((f, i) =>
      blip(t0 + i * 0.09, f, 0.18, 0.30, { type: "triangle" }));
  }

  /* Session complete: a fuller four-note ascending fanfare. */
  function sessionComplete(t0) {
    [523.25, 659.25, 783.99, 1046.5].forEach((f, i) =>
      blip(t0 + i * 0.12, f, 0.2, 0.45, { type: "triangle" }));
    blip(t0 + 0.12, 261.63, 0.12, 0.7, { type: "sine" });   // low body under the run
  }

  /* Arcade right answer: a snappy two-note "ding" up. */
  function correct(t0) {
    blip(t0, 880, 0.2, 0.12, { type: "triangle" });
    blip(t0 + 0.09, 1318.5, 0.2, 0.2, { type: "triangle" });
  }

  /* Arcade wrong answer: a short, soft descending buzz — clear but not harsh. */
  function wrong(t0) {
    blip(t0, 300, 0.18, 0.26, { type: "sawtooth", glideTo: 150 });
  }

  /* UI click: a barely-there tap for navigation, so the chrome feels responsive
   * without becoming chatty. */
  function uiClick(t0) {
    blip(t0, 660, 0.06, 0.05, { type: "sine" });
  }

  const EFFECTS = {
    lockIn, riser, greenSector, purpleSector, lightsOut,
    achievement, sessionComplete, correct, wrong, uiClick,
  };

  function play(name) {
    if (!on) return;
    if (!ensureCtx()) return;
    resume();
    const fn = EFFECTS[name];
    if (!fn) return;
    try { fn(ctx.currentTime); } catch { /* never let audio throw into the app */ }
  }

  // The slider can fire dozens of value changes a second while dragging; rate-limit
  // the click so it reads as crisp detents, not a buzz.
  let lastTick = 0;
  function tick() {
    if (!on) return;
    const now = performance.now();
    if (now - lastTick < 28) return;
    lastTick = now;
    if (!ensureCtx()) return;
    resume();
    try { clickTick(ctx.currentTime); } catch { /* no-op */ }
  }

  function isOn() { return on; }
  function setOn(v) {
    on = !!v;
    localStorage.setItem(PREF_KEY, on ? "1" : "0");
    return on;
  }
  function toggle() { return setOn(!on); }

  return { play, tick, isOn, setOn, toggle };
})();

// Silver-halide print of a bundled luminance plate. The random field stays fixed
// between frames; only a small layer of already-exposed grains moves on a wake.
export const BREATH_MS = 4800;

function rgb(canvas, token) {
  const hex = getComputedStyle(canvas).getPropertyValue(token).trim();
  return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
}

function stipple(source, side, graphite, silver) {
  const paper = document.createElement('canvas');
  const grains = document.createElement('canvas');
  for (const layer of [paper, grains]) layer.width = layer.height = side;
  const ctx = paper.getContext('2d', { willReadFrequently: true });
  const glints = grains.getContext('2d');
  if (!ctx || !glints) return null;
  ctx.drawImage(source, 0, 0, side, side);
  const luminance = ctx.getImageData(0, 0, side, side).data;
  const base = ctx.createImageData(side, side);
  const highlights = glints.createImageData(side, side);
  let row = new Float32Array(side + 2);
  let next = new Float32Array(side + 2);

  // A seeded threshold breaks up regular halftone rows. Serpentine
  // Floyd–Steinberg diffusion conserves tone across the cheek, eye and curls.
  let seed = 337;
  function noise() {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  }
  for (let y = 0; y < side; y++) {
    const direction = y % 2 === 0 ? 1 : -1;
    for (let col = 0; col < side; col++) {
      const x = direction === 1 ? col : side - 1 - col;
      const offset = (y * side + x) * 4;
      const gray = luminance[offset];
      if (gray < 7) continue;
      const density = Math.min(1, (gray - 6) / 235);
      const value = density + row[x + 1];
      const exposed = value >= 0.28 + 0.44 * noise();
      if (exposed) {
        const target = noise() < 0.16 ? highlights : base;
        const colour = noise() < density * density ? silver : graphite;
        target.data[offset] = colour[0];
        target.data[offset + 1] = colour[1];
        target.data[offset + 2] = colour[2];
        target.data[offset + 3] = 255;
      }
      const error = (value - Number(exposed)) / 16;
      row[x + 1 + direction] += error * 7;
      next[x + 1 - direction] += error * 3;
      next[x + 1] += error * 5;
      next[x + 1 + direction] += error;
    }
    [row, next] = [next, row];
    next.fill(0);
  }
  ctx.putImageData(base, 0, 0);
  glints.putImageData(highlights, 0, 0);
  return [paper, grains];
}

export function mountPortrait(canvas) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return () => {};
  const source = new Image();
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const portrait = canvas.closest('.portrait');
  let lastWake = null;
  let layers;
  let visible = false;
  let phaseStart = 0;
  let wakeUntil = 0;
  let frameID = 0;

  function paint() {
    cancelAnimationFrame(frameID);
    frameID = 0;
    if (!layers) return;
    const now = Date.now();
    const moving = visible && !document.hidden && !reduced.matches && phaseStart <= now && now < wakeUntil;
    const breath = moving ? Math.sin(Math.PI * (now - phaseStart) / BREATH_MS) ** 2 : 0;
    const side = canvas.width;
    ctx.clearRect(0, 0, side, side);
    ctx.drawImage(layers[0], 0, 0);
    const shift = breath * side * 0.0018;
    ctx.drawImage(layers[1], -shift, -shift, side + 2 * shift, side + 2 * shift);
    if (moving) frameID = requestAnimationFrame(paint);
  }

  function print() {
    if (!source.complete || !source.naturalWidth) return;
    const bounds = canvas.getBoundingClientRect();
    const pixels = Math.max(bounds.width, bounds.height) * Math.min(devicePixelRatio, 2);
    const side = Math.max(224, Math.min(640, Math.ceil(pixels / 32) * 32));
    if (layers && canvas.width === side) return;
    layers = stipple(source, side, rgb(canvas, '--silver'), rgb(canvas, '--highlight'));
    if (!layers) return;
    canvas.width = canvas.height = side;
    paint();
  }

  source.addEventListener('load', print);
  source.src = new URL('./portrait.webp', import.meta.url).href;
  new ResizeObserver(print).observe(canvas);
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    portrait.classList.toggle('in-view', visible);
    if (!visible) portrait.classList.remove('upload');
    paint();
  }).observe(canvas);
  canvas.addEventListener('animationend', () => portrait.classList.remove('upload'));
  canvas.addEventListener('animationcancel', () => {
    if (reduced.matches) portrait.classList.remove('upload');
  });
  reduced.addEventListener('change', paint);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) portrait.classList.remove('upload');
    paint();
  });
  return (start, expires, wake = null) => {
    if (!Number.isSafeInteger(wake)) portrait.classList.remove('upload');
    else if (wake !== lastWake) {
      lastWake = wake;
      if (visible && !document.hidden && !reduced.matches) {
        portrait.classList.remove('upload');
        void canvas.offsetWidth; // Restart only for a distinct ledger entry.
        portrait.classList.add('upload');
      }
    }
    if (start === phaseStart && expires === wakeUntil) return;
    phaseStart = start;
    wakeUntil = expires;
    paint();
  };
}

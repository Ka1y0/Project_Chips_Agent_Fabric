// Decorative, deterministic geometry. It never represents system health or capacity.
export function drawSculpture(canvas) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const layer = document.createElement('canvas');
  layer.width = 320;
  layer.height = 340;
  const ink = layer.getContext('2d', { willReadFrequently: true });
  if (!ink) return;
  const shape = (path, fill, stroke = null) => {
    const outline = new Path2D(path);
    ink.fillStyle = fill;
    ink.fill(outline);
    if (stroke) { ink.strokeStyle = stroke; ink.lineWidth = 1; ink.stroke(outline); }
  };
  const bust = 'M62 306 L80 274 Q101 266 126 250 L138 223 L132 198 Q106 181 106 158 L99 118 Q91 79 117 55 Q142 29 180 40 Q208 44 217 73 L221 99 L212 120 L228 141 L213 149 L212 171 Q206 184 187 190 L178 222 Q176 240 194 252 L234 274 L255 306 Z';
  const marble = ink.createLinearGradient(80, 60, 222, 220);
  marble.addColorStop(0, '#747267');
  marble.addColorStop(.36, '#d7d0b8');
  marble.addColorStop(.65, '#b8b29e');
  marble.addColorStop(1, '#55564f');
  shape(bust, marble);
  shape('M113 100 Q113 71 139 63 Q171 48 193 64 L199 91 L189 107 L200 131 L213 141 L194 148 L191 169 L171 181 L167 212 L169 235 L145 256 L126 250 L138 223 L132 198 Q108 178 113 155 L106 126 Z', '#cec8b1');
  shape('M190 106 L182 132 L194 146 L213 141 L201 127 Z', '#e2dac3');
  shape('M142 174 L162 185 L165 215 L152 230 L143 221 Z', '#949283');
  shape('M80 274 L126 252 Q150 268 173 260 L234 274 L246 292 L108 281 L62 306 Z', '#9a998a');
  shape('M100 118 Q91 82 116 57 Q140 35 176 40 Q211 42 217 73 L204 96 L190 89 L190 69 Q155 55 137 80 L126 104 L120 134 L109 147 Z', '#858678');
  ink.save();
  ink.clip(new Path2D(bust));
  // Engraved cross-hatching follows the silhouette, denser in the shadow half.
  ink.lineWidth = .7;
  ink.strokeStyle = '#333d3768';
  for (let x = -150; x < 340; x += 6) {
    ink.beginPath(); ink.moveTo(x, 34); ink.lineTo(x + 120, 320); ink.stroke();
  }
  ink.strokeStyle = '#f0e9d133';
  for (let x = 80; x < 370; x += 8) {
    ink.beginPath(); ink.moveTo(x, 35); ink.lineTo(x - 90, 320); ink.stroke();
  }
  ink.restore();
  // Hair and classical profile, with deliberately broken sketch lines.
  ink.strokeStyle = '#303832'; ink.lineWidth = 1.5;
  for (let n = 0; n < 9; n++) {
    const x = 112 + n * 10;
    const y = 64 - Math.sin(n / 8 * Math.PI) * 13;
    ink.beginPath(); ink.moveTo(x, y + 24);
    ink.bezierCurveTo(x - 12, y + 10, x + 5, y - 11, x + 11, y + 1);
    ink.bezierCurveTo(x + 16, y + 12, x, y + 18, x + 1, y + 8); ink.stroke();
  }
  ink.stroke(new Path2D('M170 111 Q182 106 194 111 M174 117 L188 117 M191 118 L184 135 L193 138 M188 153 Q201 150 209 153 M191 158 L205 159 M154 177 L168 184 M144 232 L166 240 M106 269 L192 291'));
  ink.fillStyle = '#292f2c'; ink.fillRect(182, 114, 3, 2);
  shape('M60 309 L258 309 L258 317 L60 317 Z', '#818376');
  shape('M76 318 L241 318 L241 323 L76 323 Z', '#53584f');
  const pixels = ink.getImageData(0, 0, 320, 340);
  const bayer = [[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]];
  for (let y = 0; y < 340; y++) for (let x = 0; x < 320; x++) {
    const i = (y * 320 + x) * 4;
    if (!pixels.data[i + 3]) continue;
    const threshold = (bayer[y % 4][x % 4] - 7.5) * 2.4;
    for (let c = 0; c < 3; c++) {
      pixels.data[i + c] = Math.round((pixels.data[i + c] + threshold) / 20) * 20;
    }
  }
  ink.putImageData(pixels, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#c8a25335'; ctx.lineWidth = .6;
  ctx.beginPath(); ctx.arc(158, 166, 123, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(158, 166, 129, Math.PI, Math.PI * 1.48); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(16,166);ctx.lineTo(302,166);ctx.moveTo(158,22);ctx.lineTo(158,334);ctx.stroke();
  ctx.drawImage(layer, 0, 0);
  ctx.fillStyle = '#0a0c0f0c';
  for (let y = 0; y < 340; y += 4) ctx.fillRect(0, y, 320, 1);
}

/**
 * Renders every app icon from app/icon.svg, so the set cannot drift from the
 * mark. Run after changing the logo:  node scripts/build-icons.mjs
 *
 * Why each file exists:
 *   app/favicon.ico            browsers and crawlers ask for /favicon.ico
 *                              whatever the page declares; without it every
 *                              visit logs a 404
 *   app/apple-icon.png         iOS home screen. iOS ignores SVG icons, which is
 *                              why the installed app showed no logo
 *   public/icons/icon-*.png    Android and desktop install prompts
 *   public/icons/maskable-*    Android masks icons to a circle or squircle and
 *                              only guarantees the middle 80%; the full mark
 *                              would have its compass points cut off
 */

import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = fileURLToPath(new URL("..", import.meta.url));
const PLATE = "#0a0a0a";
const svg = await readFile(`${root}app/icon.svg`, "utf8");

// The source has rounded corners for a browser tab. Home-screen platforms apply
// their own mask, so they get a square plate with no transparent corners —
// otherwise iOS fills those corners with its own black and the edge shows.
const square = svg.replace(/<rect([^>]*?)rx="[^"]*"/, "<rect$1");

// For maskable, shrink the mark into the guaranteed safe zone. The compass
// points reach 88 of 100 units from centre, so 0.72 puts them at ~63% radius —
// inside the 80% safe circle with room to spare.
const maskable = square.replace(
  /(<svg[^>]*>)([\s\S]*)(<\/svg>)/,
  (_, open, body, close) => {
    const [plate, ...rest] = body.trim().split(/\n(?=\s*<g|\s*<circle)/);
    return `${open}${plate}<g transform="translate(100 100) scale(0.72) translate(-100 -100)">${rest.join("\n")}</g>${close}`;
  },
);

async function png(source, size, { rgba = false } = {}) {
  const img = sharp(Buffer.from(source), { density: 72 * (size / 64) * 2 }).resize(size, size);
  if (rgba) {
    // Inside a .ico the PNG must be 32-bit RGBA — both the format and Next's
    // metadata pipeline reject palette images there. Transparent corners are
    // kept, since a browser tab shows the rounded mark as drawn.
    return img.ensureAlpha().png({ compressionLevel: 9, palette: false }).toBuffer();
  }
  return (
    img
      .flatten({ background: PLATE })
      // Palette PNG: the mark is two colours plus antialiasing, so 64 colours
      // is visually lossless at a fraction of the truecolour size.
      .png({ palette: true, colours: 64, compressionLevel: 9, effort: 10 })
      .toBuffer()
  );
}

/** A .ico is a tiny directory of embedded PNGs (supported since Vista). */
function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  const entries = [];
  let offset = 6 + 16 * images.length;
  for (const { size, data } of images) {
    const e = Buffer.alloc(16);
    e.writeUInt8(size >= 256 ? 0 : size, 0);
    e.writeUInt8(size >= 256 ? 0 : size, 1);
    e.writeUInt8(0, 2);
    e.writeUInt8(0, 3);
    e.writeUInt16LE(1, 4);
    e.writeUInt16LE(32, 6);
    e.writeUInt32LE(data.length, 8);
    e.writeUInt32LE(offset, 12);
    offset += data.length;
    entries.push(e);
  }
  return Buffer.concat([header, ...entries, ...images.map((i) => i.data)]);
}

const out = [
  ["app/apple-icon.png", await png(square, 180)],
  ["public/icons/icon-192.png", await png(square, 192)],
  ["public/icons/icon-512.png", await png(square, 512)],
  ["public/icons/maskable-192.png", await png(maskable, 192)],
  ["public/icons/maskable-512.png", await png(maskable, 512)],
  [
    "app/favicon.ico",
    ico(
      await Promise.all(
        [16, 32, 48].map(async (size) => ({ size, data: await png(svg, size, { rgba: true }) })),
      ),
    ),
  ],
];

for (const [path, data] of out) {
  await writeFile(`${root}${path}`, data);
  console.log(`${path.padEnd(32)} ${(data.length / 1024).toFixed(1)} KB`);
}

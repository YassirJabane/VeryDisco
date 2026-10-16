// Run with Node.js and sharp installed (or SHARP_MODULE pointing to sharp).
// SVGs are the approved originals; embed Sora so image logos work offline.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../frontend/public');
const brand = path.join(root, 'brand');
const { default: sharp } = await import(process.env.SHARP_MODULE || 'sharp');
const font = (await fs.readFile(path.join(brand, 'Sora.ttf'))).toString('base64');
const names = ['symbol', 'symbol-on-dark', 'symbol-black', 'symbol-white', 'logo-horizontal', 'logo-horizontal-on-dark', 'logo-horizontal-black', 'logo-horizontal-white', 'app-icon', 'app-icon-dark'];
for (const name of names) {
  const file = path.join(brand, `${name}.svg`);
  let svg = await fs.readFile(file, 'utf8');
  svg = svg.replace(/<metadata>[\s\S]*?<\/metadata>/g, '').replace(/ xmlns:c2pa="[^"]*"/g, '').replace(/<defs data-brand-font(?:="sora")?>[\s\S]*?<\/defs>/g, '');
  if (name.startsWith('logo-horizontal')) {
    svg = svg.replace(/(<svg[^>]*>)/, `$1<defs data-brand-font="sora"><style>@font-face{font-family:Sora;src:url(data:font/ttf;base64,${font}) format('truetype');font-weight:100 900;font-style:normal}</style></defs>`);
  }
  await fs.writeFile(file, svg);
}
const icon = await fs.readFile(path.join(brand, 'app-icon.svg'), 'utf8');
const maskable = icon.replace('rx="115"', 'rx="0"');
await fs.writeFile(path.join(root, 'icon.svg'), icon);
await fs.writeFile(path.join(root, 'icon-maskable.svg'), maskable);
for (const size of [32, 192, 512]) {
  await sharp(Buffer.from(icon)).resize(size, size).png().toFile(path.join(root, size === 32 ? 'favicon-32.png' : `icon-${size}.png`));
}
await sharp(Buffer.from(icon)).resize(180, 180).flatten({ background: '#6840dc' }).png().toFile(path.join(root, 'apple-touch-icon.png'));
await sharp(Buffer.from(maskable)).resize(512, 512).png().toFile(path.join(root, 'icon-maskable-512.png'));
console.log('Brand SVGs, favicon, Apple touch icon and PWA icons generated.');

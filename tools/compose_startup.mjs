// Authoring only: sample onetake's pure motion functions into native WAAPI tracks.
// onetake © 2026 Patrick (github.com/feitangyuan), PolyForm Noncommercial 1.0.0.
// No onetake runtime, fonts, sound or other library assets are bundled in the app.
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const skill = process.env.ONETAKE_SKILL_DIR || join(homedir(), '.codex/skills/onetake');
const OM = createRequire(import.meta.url)(join(skill, 'lib/motion.js'));
const duration = 1880, hold = 320, exitDuration = 800;
const round = n => Math.round(n * 100000) / 100000;
const samples = (fn, length = duration) => Array.from({length: Math.ceil(length / 20) + 1}, (_, i) => fn(i / Math.ceil(length / 20), i * 20 / 1000));
const paths = [...readFileSync(join(root, 'web/brand/npl-mark.svg'), 'utf8').matchAll(/<path data-part="([^"]+)" d="([^"]+)"\s*\/>/g)];
if (paths.length !== 5) throw new Error('Expected the five named production logo paths');
const side = name => ['wing-left', 'left-upper', 'left-lower'].includes(name) ? 'left' : 'right';
const fill = readFileSync(join(root, 'web/brand/npl-mark.svg'), 'utf8').match(/<g fill="(#[a-f0-9]{6})"/i)?.[1];
if (!fill) throw new Error('Production logo fill is missing');
// Each strand ends at a real book corner: its travel hands off to the drawn outline.
const orbits = [
  {name:'pl', color:fill, start:0, end:1.00, tail:.19, d:'M140 479C35 331 162 105 390 80C570 61 751 133 719 225C698 286 562 306 454 279'},
  {name:'n', color:'#b65b9e', start:.09, end:1.10, tail:.16, d:'M748 196C849 345 687 610 433 575C220 547 113 360 197 147'},
  {name:'wing', color:'#488ca9', start:.18, end:1.19, tail:.13, d:'M242 570C65 514 98 259 245 166C398 69 695 49 725 185C752 298 567 355 443 256'},
];
const orbitSvg = `<defs><filter id="npl-startup-glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="3"/></filter></defs><g class="startup-orbits">${orbits.map(o=>`<g data-orbit="${o.name}" fill="none" stroke="${o.color}" stroke-linecap="round" stroke-dasharray="${o.tail} 1"><path data-orbit-route="${o.name}" pathLength="1" d="${o.d}" stroke-width="12" opacity=".22" filter="url(#npl-startup-glow)"/><path pathLength="1" d="${o.d}" stroke-width="4"/></g><circle data-orbit-head="${o.name}" r="4.5" fill="${o.color}"/>`).join('')}</g>`;
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="160 70 580 510" aria-hidden="true" focusable="false">${orbitSvg}${['left','right'].map(s => `<g data-startup-side="${s}">${paths.filter(p => side(p[1]) === s).map(p => `<path data-part="${p[1]}" fill="${fill}" d="${p[2]}"/><path data-outline="${p[1]}" pathLength="1" fill="none" stroke="${fill}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" stroke-dasharray="1" d="${p[2]}"/>`).join('')}</g>`).join('')}</svg>`;
const tracks = [];
// Begin with the light sculpture centered; it glides left as the wordmark joins it.
tracks.push({selector:'.startup-mark',frames:samples((offset,t)=>({offset:round(offset),
  transform:`translateX(calc((var(--startup-copy) + var(--startup-gap)) * ${round(.5*(1-OM.tween(t,.58,1.38,OM.ease.cubicOut)))}))`,
}))});
for (const orbit of orbits) {
  const frames = samples((offset,t) => {
    const p = OM.tween(t,orbit.start,orbit.end,OM.ease.sineInOut);
    const opacity = OM.tween(t,orbit.start,orbit.start+.12,OM.ease.quadOut) * (1-OM.tween(t,orbit.end-.16,orbit.end+.10,OM.ease.cubicOut));
    return {offset:round(offset),progress:round(p),opacity:round(opacity)};
  });
  tracks.push({selector:`[data-orbit="${orbit.name}"]`,frames:frames.map(({progress,...f})=>({...f,strokeDashoffset:round(orbit.tail-progress)}))});
  tracks.push({selector:`[data-orbit-head="${orbit.name}"]`,along:`[data-orbit-route="${orbit.name}"]`,frames});
}
for (const [name,start,end] of [['right-pl',.43,1.30],['left-upper',.51,1.34],['left-lower',.63,1.44],['wing-left',.76,1.45],['wing-right',.83,1.51]]) {
  tracks.push({selector:`[data-outline="${name}"]`,frames:samples((offset,t)=>({
    offset:round(offset),strokeDashoffset:round(1-OM.tween(t,start,end,OM.ease.cubicInOut)),
    opacity:round(OM.tween(t,start,start+.15,OM.ease.quadOut)*(1-OM.tween(t,1.35,1.72,OM.ease.cubicOut))),
  }))});
}
for (const [name, start, end, dir] of [
  ['right-pl', .89, 1.54, 1], ['left-upper', .95, 1.60, -1],
  ['left-lower', 1.03, 1.64, -1], ['wing-left', 1.12, 1.68, -1], ['wing-right', 1.17, 1.72, 1],
]) {
  const wing = name.startsWith('wing');
  tracks.push({selector:`[data-part="${name}"]`, frames:samples((offset, t) => {
    const k = OM.tween(t, start, end, OM.ease.cubicOut);
    return {offset:round(offset), opacity:round(OM.tween(t, start, end, OM.ease.cubicInOut)),
      transform:`translate(${round(dir * 3 * (1-k))}px, ${round((wing ? 5 : 2) * (1-k))}px) rotate(${round(dir * (wing ? 3 : 1) * (1-k))}deg) scaleX(${round(.96 + .04 * k)})`};
  })});
}
for (const [selector, start, dist] of [['.startup-letter-n',1.04,19],['.startup-letter-p',1.11,19],['.startup-letter-l',1.18,19],['.startup-fullname',1.30,11],['.startup-tagline',1.43,8]]) {
  tracks.push({selector, frames:samples((offset,t) => {
    const m = OM.wordRise(t,start,{zeta:.82,omega:20,dist,s0:1,ds:0});
    return {offset:round(offset),opacity:round(m.op),transform:`translateY(${round(m.y)}px)`};
  })});
}
for (const track of tracks) {
  if (track.selector.includes('data-part') || track.selector.startsWith('.startup-')) track.frames[track.frames.length-1] = {offset:1,opacity:1,transform:'none'};
}
const exit = samples((offset) => ({offset:round(offset), progress:round(OM.ease.cubicInOut(offset))}), exitDuration);
const source = `// Generated by tools/compose_startup.mjs using onetake; do not edit sampled tracks.\nexport const STARTUP_MOTION = ${JSON.stringify({duration,hold,exitDuration,svg,tracks,exit})};\n`;
writeFileSync(join(root,'web/js/startup-motion.js'),source);
console.log(`Authored ${tracks.length} native tracks; assembly ${duration}ms + hold ${hold}ms + reveal ${exitDuration}ms`);

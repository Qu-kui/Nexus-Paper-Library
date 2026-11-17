/* Launch-only brand assembly. The main app loads concurrently behind two opening pages. */
import { STARTUP_MOTION as motion } from './startup-motion.js';

export function initStartup() {
  const state = window.__NPL_STARTUP__;
  if (!state?.requested) return {ready() {}};
  const root = document.documentElement;
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  const preview = window.__NPL_STARTUP_PREVIEW__ === true;
  const oldFocus = document.activeElement;
  const layer = document.createElement('div');
  layer.id = 'npl-startup';
  layer.className = 'npl-startup';
  layer.setAttribute('role', 'dialog');
  layer.setAttribute('aria-modal', 'true');
  layer.setAttribute('aria-label', 'Nexus Paper Library 启动');
  layer.innerHTML = `<div class="startup-curtain startup-curtain-left"></div><div class="startup-curtain startup-curtain-right"></div>
    <div class="startup-stage"><div class="startup-mark">${motion.svg}</div><div class="startup-copy">
      <div class="startup-wordmark" aria-hidden="true"><span class="startup-letter-n">N</span><span class="startup-letter-p">P</span><span class="startup-letter-l">L</span></div>
      <div class="startup-fullname">Nexus Paper Library</div><div class="startup-tagline">论文 · 阅读 · 知识</div>
    </div></div><button type="button" class="startup-skip" aria-label="跳过开屏动画">跳过 · Esc</button>`;
  const animations = [], timers = [];
  let finished = false, appReady = false, held = false, opening = false;
  state.inertElements ||= [];

  function finish() {
    if (finished) return;
    finished = true;
    clearTimeout(state.failsafe);
    timers.forEach(clearTimeout);
    animations.forEach(a => a.cancel());
    layer.remove();
    delete root.dataset.nplStartup;
    state.requested = false;
    const reader = document.getElementById('reader');
    const readerOpen = reader && !reader.classList.contains('hidden');
    for (const el of state.inertElements) if (!readerOpen) el.inert = false;
    state.inertElements.length = 0;
    document.removeEventListener('keydown', onKey, true);
    media.removeEventListener('change', onMotion);
    if (oldFocus?.isConnected && oldFocus !== document.body && !oldFocus.closest('[inert]')) oldFocus.focus({preventScroll:true});
    else if (!readerOpen) document.querySelector('.nav-item.active')?.focus({preventScroll:true});
    window.dispatchEvent(new CustomEvent('pw:startup-finished'));
  }
  function onKey(event) {
    if (event.key === 'Escape') {event.preventDefault(); event.stopImmediatePropagation(); finish();}
    else if (event.key === 'Tab') {event.preventDefault(); layer.querySelector('button').focus();}
  }
  function onMotion(event) {if (event.matches) finish();}
  function animate(el, frames, duration) {
    const animation = el.animate(frames, {duration,fill:'both',easing:'linear'});
    if (preview) animation.pause();
    animations.push(animation);
    return animation;
  }
  function openingFrames(direction, distance) {
    return motion.exit.map(({offset,progress}) => ({offset,transform:`translateX(${direction * distance * progress}px)`}));
  }
  function open() {
    if (finished || opening) return;
    opening = true;
    root.dataset.nplStartup = 'opening';
    layer.dataset.phase = 'opening';
    const svg = layer.querySelector('svg');
    const svgScale = 580 / svg.getBoundingClientRect().width;
    for (const side of ['left','right']) {
      const direction = side === 'left' ? -1 : 1;
      const curtain = layer.querySelector(`.startup-curtain-${side}`);
      const distance = curtain.getBoundingClientRect().width;
      animate(curtain, openingFrames(direction, distance), motion.exitDuration);
      animate(layer.querySelector(`[data-startup-side="${side}"]`), openingFrames(direction, distance * svgScale), motion.exitDuration);
      if (side === 'right') animate(layer.querySelector('.startup-copy'),openingFrames(1,distance),motion.exitDuration);
    }
    if (!preview) timers.push(setTimeout(finish,motion.exitDuration + 30));
  }
  state.finish = finish;
  try {
    document.body.append(layer);
    if (getComputedStyle(layer).position !== 'fixed') throw new Error('Startup stylesheet unavailable');
    for (const el of document.querySelectorAll('.main,.sidebar')) if (!el.inert) {el.inert = true; state.inertElements.push(el);}
    for (const track of motion.tracks) {
      let frames = track.frames;
      if (track.along) {
        // Sample SVG geometry once; the browser then animates the head natively, without a JS frame loop.
        const route = layer.querySelector(track.along), length = route.getTotalLength();
        frames = frames.map(({progress,...frame}) => {
          const p = route.getPointAtLength(progress * length);
          return {...frame,transform:`translate(${p.x}px,${p.y}px)`};
        });
      }
      animate(layer.querySelector(track.selector),frames,motion.duration);
    }
    layer.querySelector('button').addEventListener('click',finish);
    document.addEventListener('keydown',onKey,true);
    media.addEventListener('change',onMotion);
    layer.querySelector('button').focus({preventScroll:true});
    if (media.matches) {finish(); return {ready() {}};}
    if (preview) {
      clearTimeout(state.failsafe);
      state.seek = seconds => {
        open();
        const ms = Math.max(0,seconds * 1000);
        animations.forEach((a,i) => {a.pause(); a.currentTime = i < motion.tracks.length ? Math.min(ms,motion.duration) : Math.max(0,ms-motion.duration-motion.hold);});
        const phase = ms < motion.duration + motion.hold ? 'pending' : 'opening';
        root.dataset.nplStartup = phase; layer.dataset.phase = phase;
      };
      state.seek(0);
    } else {
      timers.push(setTimeout(() => {held = true; if (appReady) open();},motion.duration + motion.hold));
      timers.push(setTimeout(open,4500));
    }
  } catch (error) {finish(); console.warn('NPL startup animation unavailable',error);}
  return {ready() {appReady = true; if (held) open();}};
}

export type HTMLPosition = { fraction: number; anchor: number; offset: number };
export type ArticleGeometry = { height: number; viewport: number; blocks: { top: number; height: number }[] };

export function validHTMLPosition(value: unknown): value is HTMLPosition {
  if (!value || typeof value !== 'object') return false;
  const p = value as HTMLPosition;
  return Number.isFinite(p.fraction) && p.fraction >= 0 && p.fraction <= 1
    && Number.isInteger(p.anchor) && p.anchor >= 0 && p.anchor <= 100000
    && Number.isFinite(p.offset) && p.offset >= 0 && p.offset <= 1;
}

export function captureHTMLPosition(top: number, geometry: ArticleGeometry): HTMLPosition {
  const range = Math.max(1, geometry.height - geometry.viewport);
  const clamped = Math.max(0, Math.min(top, range));
  let anchor = 0;
  for (let index = 0; index < geometry.blocks.length; index++) {
    if (geometry.blocks[index].top <= clamped) anchor = index;
    else break;
  }
  const block = geometry.blocks[anchor];
  return { fraction: clamped / range, anchor,
    offset: block ? Math.max(0, Math.min(1, (clamped - block.top) / Math.max(1, block.height))) : 0 };
}

export function restoreHTMLPosition(position: HTMLPosition | null, geometry: ArticleGeometry): number {
  if (!validHTMLPosition(position)) return 0;
  const range = Math.max(0, geometry.height - geometry.viewport);
  const block = geometry.blocks[position.anchor];
  return Math.max(0, Math.min(range, block ? block.top + position.offset * block.height : position.fraction * range));
}

// Same-origin saved articles receive input inside their sandboxed iframe. Those
// events do not bubble to the parent window, so both surfaces must be observed.
export function listenForReadingInput(target: EventTarget, onInput: () => void): () => void {
  const events = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
  for (const event of events) target.addEventListener(event, onInput, {passive:true});
  return () => { for (const event of events) target.removeEventListener(event, onInput); };
}

export type CardBounds = { left: number; right: number; top: number; bottom: number };

/** Return an insertion gap in the unchanged grid, including gaps at row edges. */
export function shortcutDropSlot(cards: CardBounds[], x: number, y: number): number | null {
  if (!cards.length) return null;
  const left = Math.min(...cards.map(card => card.left));
  const right = Math.max(...cards.map(card => card.right));
  const top = Math.min(...cards.map(card => card.top));
  const bottom = Math.max(...cards.map(card => card.bottom));
  if (x < left - 12 || x > right + 12 || y < top - 12 || y > bottom + 12) return null;
  let closest = 0; let vertical = Infinity; let distance = Infinity;
  cards.forEach((card, index) => {
    const dx = Math.max(card.left - x, 0, x - card.right);
    const dy = Math.max(card.top - y, 0, y - card.bottom);
    // Prefer the nearest row before choosing a column, including a short final row.
    if (dy < vertical || (dy === vertical && dx < distance)) { vertical = dy; distance = dx; closest = index; }
  });
  const card = cards[closest];
  return closest + (x >= (card.left + card.right) / 2 ? 1 : 0);
}

export function arrangeShortcuts(ids: string[], id: string, slot: number | null) {
  const from = ids.indexOf(id);
  if (from < 0 || slot === null || slot < 0 || slot > ids.length) return [...ids];
  const result = ids.filter(value => value !== id);
  result.splice(slot - (from < slot ? 1 : 0), 0, id);
  return result;
}

export class ShortcutDrag {
  active = false;
  slot: number | null = null;
  x: number;
  y: number;
  constructor(readonly id: string, readonly pointerId: number, readonly revision: number,
    readonly ids: string[], readonly startX: number, readonly startY: number) {
    this.x = startX; this.y = startY;
  }
  move(pointerId: number, x: number, y: number, cards: CardBounds[]) {
    if (pointerId !== this.pointerId) return;
    this.x = x; this.y = y;
    if (Math.hypot(x - this.startX, y - this.startY) >= 6) this.active = true;
    if (this.active) this.slot = shortcutDropSlot(cards, x, y);
  }
  finish(pointerId: number) {
    if (pointerId !== this.pointerId || !this.active || this.slot === null) return null;
    const order = arrangeShortcuts(this.ids, this.id, this.slot);
    return order.some((id, index) => id !== this.ids[index]) ? order : null;
  }
}

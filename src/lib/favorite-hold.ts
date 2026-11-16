/** A touch hold reveals a favorite's name without following its link. */
export class FavoriteHold {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pointer: { id: number; x: number; y: number } | undefined;
  private held: string | null = null;
  constructor(private reveal: (id: string) => void,
    private schedule = setTimeout, private clear = clearTimeout) {}
  start(id: string, event: Pick<PointerEvent, 'pointerType' | 'pointerId' | 'clientX' | 'clientY'>) {
    this.cancel();
    if (event.pointerType !== 'touch') return;
    this.pointer = { id:event.pointerId, x:event.clientX, y:event.clientY };
    this.timer = this.schedule(() => { this.timer = undefined; this.held = id; this.reveal(id); }, 450);
  }
  move(event: Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY'>) {
    if (this.pointer?.id !== event.pointerId) return false;
    if (Math.hypot(event.clientX - this.pointer.x, event.clientY - this.pointer.y) <= 10) return false;
    this.cancel(); return true;
  }
  end(pointerId: number) {
    if (this.pointer?.id !== pointerId) return;
    if (this.timer !== undefined) this.clear(this.timer);
    this.timer = undefined; this.pointer = undefined;
  }
  isHeld(id: string) { return this.held === id; }
  consumeClick(id: string) {
    const held = this.isHeld(id); this.held = null; return held;
  }
  cancel() {
    if (this.timer !== undefined) this.clear(this.timer);
    this.timer = undefined; this.pointer = undefined; this.held = null;
  }
}

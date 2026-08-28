import { DOCUMENT } from '@angular/common';
import { Inject, Injectable, signal } from '@angular/core';

type ResizeTarget = 'sidebar' | 'right' | 'input';

/** Owns the shared layout resize state while App remains responsible for DOM events. */
@Injectable({ providedIn: 'root' })
export class LayoutResizeService {
  readonly sidebarWidth = signal(300);
  readonly rightWidth = signal(300);
  readonly inputHeight = signal(140);

  private target: ResizeTarget | null = null;
  private startPointer = 0;
  private startSize = 0;

  constructor(@Inject(DOCUMENT) private readonly document: Document) {}

  startSidebarResize(event: MouseEvent): void {
    this.begin('sidebar', event.clientX, this.sidebarWidth());
  }

  startRightResize(event: MouseEvent): void {
    this.begin('right', event.clientX, this.rightWidth());
  }

  startInputResize(event: MouseEvent): void {
    this.begin('input', event.clientY, this.inputHeight());
  }

  handleMouseMove(event: MouseEvent): boolean {
    if (!this.target) return false;

    const delta = this.pointerDelta(event);
    switch (this.target) {
      case 'sidebar':
        this.sidebarWidth.set(this.clamp(this.startSize + delta, 200, 560));
        break;
      case 'right':
        this.rightWidth.set(this.clamp(this.startSize - delta, 280, 700));
        break;
      case 'input':
        this.inputHeight.set(this.clamp(this.startSize - delta, 100, 400));
        break;
    }
    return true;
  }

  endResize(): void {
    if (this.target) this.document.body.style.cursor = '';
    this.target = null;
  }

  private begin(target: ResizeTarget, pointer: number, size: number): void {
    this.target = target;
    this.startPointer = pointer;
    this.startSize = size;
    this.document.body.style.cursor = target === 'input' ? 'row-resize' : 'col-resize';
  }

  private pointerDelta(event: MouseEvent): number {
    const pointer = this.target === 'input' ? event.clientY : event.clientX;
    return pointer - this.startPointer;
  }

  private clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
  }
}

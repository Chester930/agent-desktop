import { TestBed } from '@angular/core/testing';
import { LayoutResizeService } from './layout-resize.service';

describe('LayoutResizeService', () => {
  let service: LayoutResizeService;

  beforeEach(() => {
    service = TestBed.inject(LayoutResizeService);
  });

  afterEach(() => {
    service.endResize();
    document.body.style.cursor = '';
  });

  it('resizes the sidebar from the pointer delta and clamps its bounds', () => {
    service.startSidebarResize(new MouseEvent('mousedown', { clientX: 100 }));

    expect(service.handleMouseMove(new MouseEvent('mousemove', { clientX: 250 }))).toBe(true);
    expect(service.sidebarWidth()).toBe(450);

    service.handleMouseMove(new MouseEvent('mousemove', { clientX: -1000 }));
    expect(service.sidebarWidth()).toBe(200);
  });

  it('uses the correct axis and cursor for the input resize', () => {
    service.startInputResize(new MouseEvent('mousedown', { clientY: 100 }));

    expect(document.body.style.cursor).toBe('row-resize');
    service.handleMouseMove(new MouseEvent('mousemove', { clientY: 180 }));
    expect(service.inputHeight()).toBe(100);

    service.endResize();
    expect(document.body.style.cursor).toBe('');
  });

  it('returns false when no resize is active', () => {
    expect(service.handleMouseMove(new MouseEvent('mousemove', { clientX: 20 }))).toBe(false);
  });
});

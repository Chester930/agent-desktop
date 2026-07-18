import { Component, EventEmitter, Output, computed } from '@angular/core';
import { SettingsService } from '../../settings.service';

@Component({
  selector: 'app-recent-work-dirs',
  standalone: true,
  templateUrl: './recent-work-dirs.html',
})
export class RecentWorkDirsComponent {
  // Named pickDir, not select: 'select' is a native, bubbling DOM event
  // name (fires on text-selection in descendant form controls) and can
  // collide with an @Output of the same name — confirmed as a real bug
  // in soul-panel (Phase 2), fixed defensively here too even though this
  // component's template has no text-selectable descendants to trigger
  // it currently.
  @Output() pickDir = new EventEmitter<string>();

  recentWorkDirs = computed(() => this.settings.get().recentWorkDirs);

  constructor(private settings: SettingsService) {}
}

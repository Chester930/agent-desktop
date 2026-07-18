import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SlicePipe } from '@angular/common';
import { SoulProfile } from '../../claude.service';

@Component({
  selector: 'app-soul-panel',
  standalone: true,
  imports: [FormsModule, SlicePipe],
  templateUrl: './soul-panel.html',
})
export class SoulPanelComponent implements OnChanges {
  // App-owned: souls is also consumed by agent-panel; selectedSoulId is
  // mutated by App-level concerns that can't move (activateAgent
  // auto-selects a soul); soulSplitRatio is driven by a global
  // document:mousemove listener. soulDraftSaved stays App-owned too, but
  // unlike the draft text itself it's safe to round-trip: it only flips
  // once per edit session (true -> false) rather than changing on every
  // keystroke, so there's no stale-echo race.
  @Input() souls: SoulProfile[] = [];
  @Input() selectedSoulId = '';
  @Input() soulDraftSaved = true;
  @Input() soulSplitRatio = 0.5;

  // Named pickSoul, not select: a native DOM 'select' event fires on
  // text-selection inside descendant form controls (e.g. Ctrl+A in the
  // textarea below) and was bubbling up to collide with an @Output
  // named `select` on the host element, misrouting the native Event
  // object into App's handler instead of the intended soul id string
  // (caught via e2e — App.selectedSoulId ended up holding an Event).
  @Output() pickSoul = new EventEmitter<string>();
  // Named soulWheel, not wheel: same native-DOM-event collision risk as
  // pickSoul above — 'wheel' is a real bubbling DOM event, and scrolling
  // anywhere inside this component (e.g. over the textarea) would
  // otherwise misroute into the host's (wheel) binding.
  @Output() soulWheel = new EventEmitter<WheelEvent>();
  @Output() add = new EventEmitter<void>();
  @Output() remove = new EventEmitter<string>();
  @Output() confirmRename = new EventEmitter<{ oldId: string; rawInput: string }>();
  @Output() edit = new EventEmitter<void>();
  @Output() save = new EventEmitter<string>();
  @Output() discard = new EventEmitter<void>();
  @Output() dividerMousedown = new EventEmitter<{ event: MouseEvent; panelHeight: number }>();

  // Rename-in-place state is purely local UI state — nothing outside this
  // tab reads or writes it (unlike expandedSkillId/expandedAgentId, which
  // turned out to be cross-tab).
  renamingSoulId = signal<string | null>(null);
  renameInput = '';

  // The draft text itself is local too — round-tripping it through App on
  // every keystroke (@Input soulDraft + @Output draftChange) caused a
  // stale-echo race under rapid typing (confirmed via e2e: characters got
  // dropped/truncated). Only the "is it saved" boolean round-trips.
  localDraft = '';

  ngOnChanges(changes: SimpleChanges) {
    if (changes['selectedSoulId']) {
      this.resetDraftFromSource();
    }
  }

  private resetDraftFromSource() {
    const s = this.souls.find(x => x.id === this.selectedSoulId);
    this.localDraft = s?.content ?? '';
  }

  startRename(id: string, event: Event) {
    event.stopPropagation();
    this.renamingSoulId.set(id);
    this.renameInput = id;
  }

  cancelRename() {
    this.renamingSoulId.set(null);
  }

  onConfirmRename(oldId: string) {
    this.renamingSoulId.set(null);
    this.confirmRename.emit({ oldId, rawInput: this.renameInput });
  }

  onDraftEdit(newValue: string) {
    this.localDraft = newValue;
    this.edit.emit();
  }

  onDiscard() {
    this.resetDraftFromSource();
    this.discard.emit();
  }

  onSave() {
    this.save.emit(this.localDraft);
  }
}

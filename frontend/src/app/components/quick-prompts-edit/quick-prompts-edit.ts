import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SettingsService, QuickPrompt } from '../../settings.service';

@Component({
  selector: 'app-quick-prompts-edit',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './quick-prompts-edit.html',
})
export class QuickPromptsEditComponent {
  showQuickPromptsEdit = false;
  quickPromptsForm: QuickPrompt[] = [];

  constructor(private settings: SettingsService) {}

  openQuickPromptsEdit() {
    this.quickPromptsForm = [...this.settings.get().quickPrompts];
    this.showQuickPromptsEdit = true;
  }

  saveQuickPrompts() {
    this.settings.save({ quickPrompts: this.quickPromptsForm });
    this.showQuickPromptsEdit = false;
  }

  addQuickPrompt() {
    this.quickPromptsForm.push({ label: '✨ 新提示', text: '' });
  }

  removeQuickPrompt(i: number) {
    this.quickPromptsForm.splice(i, 1);
  }
}

import { Component, Input, OnInit, signal } from '@angular/core';
import { ClaudeService } from '../../claude.service';

@Component({
  selector: 'app-memory-editor',
  standalone: true,
  templateUrl: './memory-editor.html',
})
export class MemoryEditorComponent implements OnInit {
  @Input() resolvedClaudeHome = '';

  memoryOverview = signal<any>(null);
  memViewExpanded = signal<Record<string, boolean>>({});
  memEditMode = signal<Record<string, boolean>>({});
  memEditContent = signal<Record<string, string>>({});

  constructor(private claude: ClaudeService) {}

  ngOnInit() {
    this.loadMemoryOverview();
  }

  loadMemoryOverview() {
    this.claude.getMemoryOverview().subscribe(data => {
      this.memoryOverview.set(data);
      this.memEditContent.update(m => ({
        ...m,
        user:   data?.user?.content   ?? '',
        system: data?.system?.content ?? '',
      }));
    });
  }

  toggleMemViewSection(key: string) {
    this.memViewExpanded.update(m => ({ ...m, [key]: !m[key] }));
  }

  memViewIsOpen(key: string): boolean {
    return !!this.memViewExpanded()[key];
  }

  memViewFilePath(type: string, ...parts: string[]): string {
    const base = this.resolvedClaudeHome || '~/.claude';
    const sep = base.includes('\\') ? '\\' : '/';
    return [base, 'memory', ...parts].join(sep);
  }

  startMemEdit(key: string, currentContent: string) {
    this.memEditContent.update(m => ({ ...m, [key]: currentContent || '' }));
    this.memEditMode.update(m => ({ ...m, [key]: true }));
  }

  cancelMemEdit(key: string) {
    this.memEditMode.update(m => ({ ...m, [key]: false }));
  }

  saveMemEdit(key: string) {
    const content = this.memEditContent()[key] ?? '';
    const save$ = key === 'user'
      ? this.claude.putMemoryUser(content)
      : this.claude.putMemorySystem(content);

    save$.subscribe(() => {
      this.memEditMode.update(m => ({ ...m, [key]: false }));
      this.loadMemoryOverview();
    });
  }
}

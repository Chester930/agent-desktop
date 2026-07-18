import { Component, EventEmitter, Output, signal } from '@angular/core';
import { ClaudeService } from '../../claude.service';

@Component({
  selector: 'app-agency-import-panel',
  standalone: true,
  templateUrl: './agency-import-panel.html',
})
export class AgencyImportPanelComponent {
  @Output() imported = new EventEmitter<void>();

  importingAgency = signal(false);
  importResult = signal<string | null>(null);

  constructor(private claude: ClaudeService) {}

  importAgencyAgents() {
    this.importingAgency.set(true);
    this.importResult.set('正在下載並導入 Agency Agents，這可能需要一至兩分鐘，請稍候…');
    this.claude.importAgencyAgents().subscribe({
      next: (res) => {
        this.importingAgency.set(false);
        if (res.ok) {
          this.importResult.set(res.message);
          this.imported.emit();
        } else {
          this.importResult.set(`導入失敗: ${res.message}`);
        }
      },
      error: (err) => {
        this.importingAgency.set(false);
        this.importResult.set(`導入出錯: ${err?.error?.message || err?.message || err || '網路或伺服器錯誤'}`);
      }
    });
  }
}

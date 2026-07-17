import { Component, EventEmitter, OnInit, Output, signal } from '@angular/core';
import { ClaudeService } from '../../claude.service';

@Component({
  selector: 'app-diagnostics-panel',
  standalone: true,
  templateUrl: './diagnostics-panel.html',
  styleUrl: './diagnostics-panel.scss',
})
export class DiagnosticsPanelComponent implements OnInit {
  @Output() logMessage = new EventEmitter<{ role: 'system'; text: string }>();

  doctorOutput = signal<string | null>(null);
  doctorRunning = signal(false);
  backendLogs = signal<string[]>([]);

  constructor(private claude: ClaudeService) {}

  ngOnInit() {
    this.loadLogs();
  }

  loadLogs() {
    this.claude.getLogs().subscribe(l => this.backendLogs.set(l));
  }

  runDoctor() {
    this.doctorRunning.set(true);
    this.doctorOutput.set('執行中…');
    this.claude.runCliCommand(['doctor']).subscribe({
      next: out => { this.doctorOutput.set(out); this.doctorRunning.set(false); },
      error: err => { this.doctorOutput.set(String(err)); this.doctorRunning.set(false); },
    });
  }

  runClaudeUpdate() {
    this.logMessage.emit({ role: 'system', text: '正在檢查 Claude Code 更新…' });
    this.claude.runCliCommand(['update']).subscribe({
      next: out => this.logMessage.emit({ role: 'system', text: out || '已是最新版本' }),
      error: err => this.logMessage.emit({ role: 'system', text: String(err) }),
    });
  }
}

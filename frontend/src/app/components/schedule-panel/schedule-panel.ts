import { Component, EventEmitter, OnInit, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { ClaudeService, Schedule } from '../../claude.service';

@Component({
  selector: 'app-schedule-panel',
  standalone: true,
  imports: [FormsModule, DatePipe],
  templateUrl: './schedule-panel.html',
})
export class SchedulePanelComponent implements OnInit {
  @Output() toast = new EventEmitter<{ text: string; type: 'success' | 'error' | 'info' | 'warn' }>();

  schedules = signal<Schedule[]>([]);
  newSchedulePrompt = '';
  newScheduleCron = '';
  aiParsing = signal(false);

  readonly CRON_PRESETS = [
    { label: '每 5 分鐘', value: '*/5 * * * *' },
    { label: '每小時', value: '0 * * * *' },
    { label: '每天 9:00', value: '0 9 * * *' },
    { label: '每週一早上', value: '0 9 * * 1' },
  ];

  constructor(private claude: ClaudeService) {}

  ngOnInit() {
    this.loadSchedules();
  }

  loadSchedules() {
    this.claude.getSchedules().subscribe(s => this.schedules.set(s));
  }

  translateCron(cron: string): string {
    if (!cron) return '';
    const trimmed = cron.trim();
    const preset = this.CRON_PRESETS.find(p => p.value === trimmed);
    if (preset) return preset.label;

    const parts = trimmed.split(/\s+/);
    if (parts.length === 5) {
      const [min, hour, dom, month, dow] = parts;
      if (min === '*' && hour === '*' && dom === '*' && month === '*' && dow === '*') {
        return '每分鐘';
      }
      if (min.startsWith('*/') && hour === '*' && dom === '*' && month === '*' && dow === '*') {
        const m = min.split('/')[1];
        return `每 ${m} 分鐘`;
      }
      if (hour.startsWith('*/') && min === '0' && dom === '*' && month === '*' && dow === '*') {
        const h = hour.split('/')[1];
        return `每 ${h} 小時`;
      }
      if (min === '0' && hour === '*' && dom === '*' && month === '*' && dow === '*') {
        return '每小時';
      }
      if (dom === '*' && month === '*' && dow === '*') {
        const mStr = min.padStart(2, '0');
        const hStr = hour.padStart(2, '0');
        return `每天 ${hStr}:${mStr}`;
      }
      if (dom === '*' && month === '*' && dow !== '*') {
        const days = ['日', '一', '二', '三', '四', '五', '六'];
        const dayNames = dow.split(',').map(d => {
          const idx = parseInt(d, 10);
          return isNaN(idx) ? d : `週${days[idx]}`;
        }).join('、');
        const mStr = min.padStart(2, '0');
        const hStr = hour.padStart(2, '0');
        return `每${dayNames} ${hStr}:${mStr}`;
      }
    }
    return cron;
  }

  isNaturalLanguage(text: string): boolean {
    if (!text) return false;
    const trimmed = text.trim();
    if (!trimmed) return false;
    const hasChinese = /[一-龥]/.test(trimmed);
    if (hasChinese) return true;

    const isCronChars = /^[0-9\s*\/,\-?LW#]+$/.test(trimmed);
    if (!isCronChars) return true;

    const parts = trimmed.split(/\s+/);
    if (parts.length !== 5) return true;

    return false;
  }

  parseCronFromAI() {
    const text = this.newScheduleCron.trim();
    if (!text) return;
    this.aiParsing.set(true);
    this.claude.parseCron(text).subscribe({
      next: (res) => {
        this.aiParsing.set(false);
        if (res && res.cron) {
          this.newScheduleCron = res.cron;
        } else {
          alert('AI 無法解析該頻率，請嘗試更具體的描述。');
        }
      },
      error: (err) => {
        this.aiParsing.set(false);
        alert('AI 轉換失敗：' + (err?.message || err));
      }
    });
  }

  addSchedule() {
    if (!this.newSchedulePrompt.trim() || !this.newScheduleCron.trim()) return;
    this.claude.addSchedule(this.newSchedulePrompt, this.newScheduleCron).subscribe({
      next: () => { this.newSchedulePrompt = ''; this.newScheduleCron = ''; this.loadSchedules(); },
      error: (e) => this.toast.emit({ text: `新增排程失敗: ${e.message ?? e}`, type: 'error' }),
    });
  }

  deleteSchedule(id: string) {
    this.claude.deleteSchedule(id).subscribe({
      next: () => this.loadSchedules(),
      error: (e) => this.toast.emit({ text: `刪除排程失敗: ${e.message ?? e}`, type: 'error' }),
    });
  }

  toggleSchedule(id: string, enabled: boolean) {
    this.claude.toggleSchedule(id, !enabled).subscribe({
      next: () => this.loadSchedules(),
      error: (e) => this.toast.emit({ text: `更新排程失敗: ${e.message ?? e}`, type: 'error' }),
    });
  }

  runScheduleNow(id: string) {
    this.claude.runSchedule(id).subscribe({
      next: () => this.loadSchedules(),
      error: (e) => this.toast.emit({ text: `執行排程失敗: ${e.message ?? e}`, type: 'error' }),
    });
  }
}

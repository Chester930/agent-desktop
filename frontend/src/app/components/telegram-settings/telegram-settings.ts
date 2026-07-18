import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ClaudeService } from '../../claude.service';

@Component({
  selector: 'app-telegram-settings',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './telegram-settings.html',
})
export class TelegramSettingsComponent implements OnInit {
  telegramToken = '';
  telegramEnabled = signal(false);
  telegramRunning = signal(false);
  telegramSaving = signal(false);

  constructor(private claude: ClaudeService) {}

  ngOnInit() {
    this.loadTelegramSettings();
  }

  loadTelegramSettings() {
    this.claude.getTelegram().subscribe(r => {
      this.telegramToken = r.token;
      this.telegramEnabled.set(r.enabled);
      this.telegramRunning.set(r.running);
    });
  }

  saveTelegramSettings() {
    this.telegramSaving.set(true);
    this.claude.setTelegram({
      token: this.telegramToken,
      enabled: this.telegramEnabled(),
    }).subscribe({
      next: r => {
        this.telegramRunning.set(r.running);
        this.telegramSaving.set(false);
      },
      error: () => this.telegramSaving.set(false),
    });
  }
}

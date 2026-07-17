import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AppSettings } from '../../settings.service';

@Component({
  selector: 'app-stt-settings',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './stt-settings.html',
})
export class SttSettingsComponent {
  // Same AppSettings object App holds — [(ngModel)] mutates it in place,
  // same pattern as ProviderSettingsComponent; no @Output needed.
  @Input() settingsForm!: AppSettings;
}

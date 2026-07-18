import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AppSettings } from '../../settings.service';

@Component({
  selector: 'app-provider-settings',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './provider-settings.html',
})
export class ProviderSettingsComponent {
  // Same AppSettings object App holds — [(ngModel)] mutates it in place,
  // same pattern App itself already uses; no @Output needed.
  @Input() settingsForm!: AppSettings;
}

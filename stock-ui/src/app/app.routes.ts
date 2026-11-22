import { Routes } from '@angular/router';
import { StockChatComponent } from './stock-chat/stock-chat.component';

export const routes: Routes = [
  // The chat shell now hosts the terminal in its main panel.
  { path: '', component: StockChatComponent },
  { path: 'terminal', redirectTo: '', pathMatch: 'full' },
  { path: 'chat', redirectTo: '', pathMatch: 'full' },
  { path: '**', redirectTo: '' },
];

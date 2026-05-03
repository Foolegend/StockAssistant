import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Helloworld } from './helloworld/helloworld';
@Component({
  selector: 'app-root',
  imports: [RouterOutlet,Helloworld],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  protected title = 'stock-ui';
}

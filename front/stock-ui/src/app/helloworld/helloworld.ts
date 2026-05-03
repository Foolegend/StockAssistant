import { Component, OnInit,ChangeDetectorRef} from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { CommonModule, JsonPipe } from '@angular/common';

@Component({
  selector: 'app-helloworld',
  imports: [CommonModule, JsonPipe],
  templateUrl: './helloworld.html',
  styleUrl: './helloworld.css',
})
export class Helloworld implements OnInit {

  // 用来接收后端数据
  apiResult: any = null;

  // 注入 HttpClient
  constructor(private http: HttpClient, private cdr: ChangeDetectorRef) { }

  ngOnInit(): void {
    // 页面加载时调用 FastAPI
    this.callFastApi();
  }

  // 调用 FastAPI POST 接口
  callFastApi() {
    const url = 'http://127.0.0.1:8000/posthello';

    // 要传给后端的 JSON 数据
    const postData = {
      name: "Angular",
      age: 18,
      message: "I got you"
    };

    this.http.post(url, postData).subscribe({
      next: (res) => {
        console.log('接口返回:', res);
        this.apiResult = res;
        this.cdr.detectChanges(); // 手动触发变更检测
      },
      error: (err) => {
        console.error('调用失败:', err);
        this.cdr.detectChanges(); // 手动触发变更检测
      }
    });
  }
}

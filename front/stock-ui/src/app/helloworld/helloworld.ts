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

  // 调用 Agent Chat 接口
  callFastApi() {
    const url = 'http://127.0.0.1:8015/agent/chat';

    const postData = {
      question: '请分析一下股票000063，并简单说明它的投资价值。',
      model: 'gpt-oss:120b-cloud',
      use_skills: true,
      use_mcp: false,
      thread_id: 'demo-angular'
    };

    this.http.post(url, postData).subscribe({
      next: (res) => {
        console.log('接口返回:', res);
        this.apiResult = res;
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('调用失败:', err);
        this.cdr.detectChanges();
      }
    });
  }
}

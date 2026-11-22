import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ViewChild,
  signal,
} from '@angular/core';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';

type TerminalClientMessage =
  | { type: 'input'; data: string }
  | { type: 'resize'; cols: number; rows: number };

@Component({
  selector: 'app-terminal',
  imports: [],
  templateUrl: './terminal.component.html',
  styleUrl: './terminal.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TerminalComponent
  implements AfterViewInit, OnChanges, OnDestroy
{
  @ViewChild('host', { static: true }) private host!: ElementRef<HTMLDivElement>;
  @Input() sessionId: string | null = null;

  private term?: Terminal;
  private fitAddon?: FitAddon;
  private socket?: WebSocket;
  private resizeObserver?: ResizeObserver;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private decoder = new TextDecoder();
  private disposed = false;
  private viewReady = false;
  private attempt = 0;

  readonly statusText = signal('正在连接终端…');
  readonly connected = signal(false);

  constructor(private readonly cdr: ChangeDetectorRef) {}

  ngAfterViewInit(): void {
    const term = new Terminal({
      fontFamily: "'Cascadia Code', Consolas, 'Courier New', monospace",
      fontSize: 14,
      lineHeight: 1.2,
      cursorBlink: true,
      convertEol: false,
      scrollback: 8000,
      theme: {
        background: '#0b0f14',
        foreground: '#d7dde8',
        cursor: '#38bdf8',
        selectionBackground: '#1e3a5f',
        black: '#0b0f14',
        red: '#f87171',
        green: '#4ade80',
        yellow: '#facc15',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#e2e8f0',
        brightBlack: '#64748b',
        brightRed: '#fca5a5',
        brightGreen: '#86efac',
        brightYellow: '#fde68a',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#67e8f9',
        brightWhite: '#f8fafc',
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(this.host.nativeElement);

    this.term = term;
    this.fitAddon = fitAddon;

    requestAnimationFrame(() => this.fit());
    this.resizeObserver = new ResizeObserver(() => this.fit());
    this.resizeObserver.observe(this.host.nativeElement);

    term.onData((data) => this.send({ type: 'input', data }));
    term.onResize(({ cols, rows }) =>
      this.send({ type: 'resize', cols, rows }),
    );

    this.viewReady = true;
    if (this.sessionId) {
      this.connect();
    } else {
      this.setStatus('正在准备会话…', false);
    }
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['sessionId'] || !this.viewReady) return;
    this.closeSocket();
    this.term?.reset();
    this.connect();
  }

  ngOnDestroy(): void {
    this.disposed = true;
    this.closeSocket();
    this.resizeObserver?.disconnect();
    this.term?.dispose();
  }

  reconnect(): void {
    this.closeSocket();
    this.term?.reset();
    this.connect();
  }

  private connect(): void {
    if (this.disposed) return;
    if (!this.sessionId) {
      this.setStatus('正在准备会话…', false);
      return;
    }

    const cols = this.term?.cols ?? 120;
    const rows = this.term?.rows ?? 40;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Prefer IPv4: the backend binds 127.0.0.1, while `localhost` may resolve
    // to ::1 first in some browsers.
    const host =
      window.location.hostname === 'localhost'
        ? '127.0.0.1'
        : window.location.hostname;
    const session = encodeURIComponent(this.sessionId);
    const url = `${protocol}//${host}:8015/terminal/ws?cols=${cols}&rows=${rows}&session=${session}`;

    this.setStatus('正在连接终端…', false);
    const socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.attempt = 0;
      // Clear stale output; the server replays the session buffer next.
      this.term?.reset();
      this.setStatus('终端已连接', true);
      this.fit();
    };

    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      if (typeof event.data === 'string') {
        this.term?.write(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        this.term?.write(
          this.decoder.decode(new Uint8Array(event.data), { stream: true }),
        );
      }
    };

    socket.onerror = () => {
      if (this.socket !== socket) return;
      this.setStatus('终端连接异常', false);
    };

    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = undefined;
      if (this.disposed) return;
      if (event.code === 4400 || event.code === 4404 || event.code === 4001) {
        this.setStatus(
          event.code === 4001
            ? '会话已删除'
            : event.code === 4400
              ? '缺少会话，请新建会话'
              : '会话不存在，请新建会话',
          false,
        );
        return;
      }
      this.setStatus('终端已断开，正在重连…', false);
      const delay = Math.min(1000 * 2 ** this.attempt, 10000);
      this.attempt += 1;
      this.reconnectTimer = setTimeout(() => this.connect(), delay);
    };
  }

  private closeSocket(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
    }
    const socket = this.socket;
    this.socket = undefined;
    try {
      socket?.close();
    } catch {
      // Ignore.
    }
  }

  private send(message: TerminalClientMessage): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  private fit(): void {
    try {
      this.fitAddon?.fit();
    } catch {
      // The container may not be laid out yet.
    }
  }

  private setStatus(text: string, connected: boolean): void {
    this.statusText.set(text);
    this.connected.set(connected);
    this.cdr.markForCheck();
  }
}

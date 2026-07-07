import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';

// T44 健檢修復：langLabel 直接來自 fenced code block 的 info string
// （```<lang> 之後那段文字），未經跳脫就內插進回傳的 HTML 字串裡。目前
// 靠下游 MarkdownPipe.transform() 對整段 HTML 跑 DOMPurify.sanitize() 擋下
// 任何真的注入的標籤，不算是能直接利用的漏洞，但不該只靠下游清洗器兜底
// ——在來源就跳脫，才不會因為 DOMPurify 設定調整或潛在繞過而失去防護。
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

marked.use({
  breaks: true,
  renderer: {
    code({ text, lang }: { text: string; lang?: string }) {
      const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
      const highlighted = hljs.highlight(text, { language }).value;
      const langLabel = escapeHtml(lang ? lang : 'code');
      return `<div class="code-block-wrap"><div class="code-block-header"><span class="code-lang">${langLabel}</span><button class="copy-code-btn" data-copy-code="1" title="複製此程式碼">⎘ 複製</button></div><pre><code class="hljs language-${language}">${highlighted}</code></pre></div>`;
    }
  }
});

@Pipe({ name: 'markdown', standalone: true })
export class MarkdownPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string): SafeHtml {
    if (!value) return '';
    const html = marked.parse(value) as string;
    const clean = DOMPurify.sanitize(html, {
      ADD_ATTR: ['class', 'data-copy-code'],
      ADD_TAGS: ['button'],
    });
    return this.sanitizer.bypassSecurityTrustHtml(clean);
  }
}

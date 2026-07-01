import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';

marked.use({
  breaks: true,
  renderer: {
    code({ text, lang }: { text: string; lang?: string }) {
      const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
      const highlighted = hljs.highlight(text, { language }).value;
      const langLabel = lang ? lang : 'code';
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

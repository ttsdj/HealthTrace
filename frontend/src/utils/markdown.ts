import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import css from 'highlight.js/lib/languages/css';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import plaintext from 'highlight.js/lib/languages/plaintext';
import python from 'highlight.js/lib/languages/python';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';

hljs.registerLanguage('bash', bash);
hljs.registerLanguage('css', css);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('json', json);
hljs.registerLanguage('plaintext', plaintext);
hljs.registerLanguage('python', python);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('xml', xml);

// Customize the code renderer in marked for syntax highlighting
const renderer = new marked.Renderer();
renderer.code = (code, language) => {
  const validLanguage = language && hljs.getLanguage(language) ? language : 'plaintext';
  const highlighted = hljs.highlight(code, { language: validLanguage }).value;
  return `<pre><code class="hljs language-${validLanguage}">${highlighted}</code></pre>`;
};

marked.use({
  renderer,
  breaks: true,
  gfm: true
});

export function parseMarkdown(text: string, msgIndex?: number | null): string {
  const html = marked.parse(text || '', { async: false }) as string;

  if (msgIndex === undefined || msgIndex === null) {
    return html;
  }

  let inCode = false;
  return html.split(/(<[^>]*>)/).map(part => {
    if (part.startsWith('<')) {
      if (part.startsWith('<code') || part.startsWith('<pre')) inCode = true;
      if (part.startsWith('</code') || part.startsWith('</pre')) inCode = false;
      return part;
    }
    if (!inCode) {
      return part.replace(/\[([\d\s,]+)\]/g, (match: string, p1: string) => {
        const numbers = p1.split(',').map((n: string) => n.trim()).filter((n: string) => /^\d+$/.test(n));
        if (numbers.length === 0) return match;
        return numbers.map(
          (n: string) => `<sup class="cite-ref" data-msg-index="${msgIndex}" data-chunk-index="${n}">[${n}]</sup>`
        ).join('');
      });
    }
    return part;
  }).join('');
}

export function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

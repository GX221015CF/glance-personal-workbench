(function (global) {
  'use strict';
  function inline(value) {
    const output = [], regex = /(\*\*([^*\n]+)\*\*|`([^`\n]+)`)/g;
    let last = 0, match;
    while ((match = regex.exec(value))) {
      if (match.index > last) output.push({type:'text',text:value.slice(last,match.index)});
      output.push({type:match[2] ? 'strong' : 'code',text:match[2] || match[3]});
      last = regex.lastIndex;
    }
    if (last < value.length) output.push({type:'text',text:value.slice(last)});
    return output;
  }
  function parse(markdown) {
    const blocks = [], lines = String(markdown).replace(/\r\n/g,'\n').split('\n');
    let paragraph = [], list = null, code = null;
    const flush = () => { if (paragraph.length) blocks.push({type:'p',children:inline(paragraph.join(' '))}); paragraph=[]; list=null; };
    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        flush();
        if (code) { blocks.push({type:'pre',text:code.join('\n')}); code=null; } else code=[];
        continue;
      }
      if (code) { code.push(line); continue; }
      if (!line.trim()) { flush(); continue; }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      const bullet = /^\s*(?:([-+*])|\d+[.)])\s+(.+)$/.exec(line);
      const quote = /^>\s?(.*)$/.exec(line);
      if (heading) { flush(); blocks.push({type:'h'+heading[1].length,children:inline(heading[2])}); }
      else if (bullet) {
        if (paragraph.length) flush();
        const type = bullet[1] ? 'ul' : 'ol';
        if (!list || list.type!==type) { list={type,children:[]}; blocks.push(list); }
        list.children.push({type:'li',children:inline(bullet[2])});
      } else if (quote) { flush(); blocks.push({type:'blockquote',children:inline(quote[1])}); }
      else { list=null; paragraph.push(line); }
    }
    flush();
    if (code) blocks.push({type:'pre',text:code.join('\n')});
    return blocks;
  }
  function render(markdown, container) {
    container.replaceChildren();
    const doc = container.ownerDocument;
    function append(nodes, parent) {
      for (const item of nodes) {
        if (item.type==='text') { parent.append(doc.createTextNode(item.text)); continue; }
        const node = doc.createElement(item.type);
        if (item.text !== undefined) node.textContent=item.text;
        if (item.children) append(item.children,node);
        parent.append(node);
      }
    }
    append(parse(markdown),container);
  }
  const api = {parse,render};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else global.WeChatMarkdown=api;
})(typeof window !== 'undefined' ? window : globalThis);

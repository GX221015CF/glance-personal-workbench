const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parse,render}=require('../assets/wechat-markdown.js');
test('parses headings lists bold quotes and code',()=>{
  const nodes=parse('## 总结\n\n- **重点**\n- 数字\n\n> 说明\n\n```\nconst a=1\n```');
  assert.deepEqual(nodes.map(n=>n.type),['h2','ul','blockquote','pre']);
  assert.equal(nodes[1].children[0].children[0].type,'strong');
});
test('external HTML URLs images and scripts are always text, no resource elements',()=>{
  const created=[];
  const doc={createElement(type){created.push(type);return {append(){},set textContent(value){}};},createTextNode(text){return {text};}};
  const container={ownerDocument:doc,replaceChildren(){},append(){}};
  render('<script>alert(1)</script>\n\n![x](https://evil.test/x) [x](javascript:alert(1))\n\n**<img src=x onerror=bad()>**',container);
  for(const tag of created)assert.ok(!['script','img','a','iframe','style'].includes(tag));
  assert.ok(JSON.stringify(parse('<script>bad()</script>')).includes('<script>'));
});

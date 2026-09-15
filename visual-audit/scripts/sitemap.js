'use strict';
const { XMLParser, XMLValidator } = require('fast-xml-parser');
const zlib = require('zlib');
const {url,fail}=require('./scenarios');
const list=x=>x===undefined?[]:Array.isArray(x)?x:[x];
async function discover(options) {
  const start=url(options.url), hosts=new Set(options.hosts||[start.host]);
  const maxMaps=options.maxMaps||20,maxDepth=options.maxDepth||3,limit=options.limit||100;
  const queue=[[start.href,0]],seen=new Set(),routes=new Set();
  const parser=new XMLParser({ignoreAttributes:true,removeNSPrefix:true,processEntities:false});
  while(queue.length) {
    const [address,depth]=queue.shift();
    if(seen.has(address)) continue;
    if(seen.size>=maxMaps) fail('Sitemap traversal limit reached',2);
    seen.add(address);
    const target=url(address);
    if(!hosts.has(target.host)) fail('Sitemap child outside host scope',2);
    const response=await fetch(target,{signal:AbortSignal.timeout(options.timeoutMs||10000),redirect:'manual'});
    if(!response.ok) fail(`Sitemap fetch failed: HTTP ${response.status}`,2);
    const maxBytes=options.maxBytes||5_000_000;
    const chunks=[];let size=0;
    for await (const chunk of response.body) {size+=chunk.length;if(size>maxBytes) fail('Sitemap exceeds byte limit',2);chunks.push(chunk);}
    let bytes=Buffer.concat(chunks);
    if(bytes[0]===0x1f && bytes[1]===0x8b) bytes=zlib.gunzipSync(bytes,{maxOutputLength:maxBytes});
    const xml=bytes.toString('utf8');
    if(/<!DOCTYPE|<!ENTITY/i.test(xml)) fail('DTD and entity declarations are prohibited',2);
    if(XMLValidator.validate(xml)!==true) fail('Invalid sitemap XML',2);
    const parsed=parser.parse(xml);
    if(parsed.sitemapindex) {
      const children=list(parsed.sitemapindex.sitemap);
      if(children.length && depth>=maxDepth) fail('Sitemap depth limit reached',2);
      for(const child of children) if(typeof child.loc==='string') queue.push([new URL(child.loc,target).href,depth+1]);
    } else if(parsed.urlset) {
      for(const item of list(parsed.urlset.url)) {
        if(typeof item.loc!=='string') continue;
        const u=url(item.loc); u.hash='';
        if(!hosts.has(u.host) || /\.(?:png|jpe?g|gif|svg|webp|pdf|zip|css|js|mp4|woff2?|xml|gz)$/i.test(u.pathname)) continue;
        u.searchParams.sort();routes.add(u.pathname+u.search);
      }
    } else fail('Expected sitemapindex or urlset',2);
  }
  const result=[...routes].sort().slice(0,limit);
  if(!result.length) fail('Sitemap produced no usable routes',2);
  return {routes:result,visited:seen.size,coverage:'Sitemap supplements the scenario catalog; it does not establish complete coverage.'};
}
module.exports={discover};

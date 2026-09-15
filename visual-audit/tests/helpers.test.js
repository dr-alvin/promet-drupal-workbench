const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),zlib=require('node:zlib');
const {validate,selected}=require('../scripts/scenarios');
const {discover}=require('../scripts/sitemap');
const scenario={id:'home',label:'Home',path:'/',expectedUrl:'/',requiredElements:['h1'],viewport:{width:100,height:100},ready:{selector:'h1'}};
test('reject empty and duplicate scenarios',()=>{assert.throws(()=>validate({scenarios:[]}));assert.throws(()=>validate({environment:{kind:'local',authorized:true},scenarios:[scenario,scenario]}));});
test('reject unknown selection and identical absolute endpoints',()=>{assert.throws(()=>selected({scenarios:[scenario]},'missing'));assert.throws(()=>validate({environment:{kind:'local',authorized:true},scenarios:[{...scenario,path:undefined,referenceUrl:'https://example.test',testUrl:'https://example.test'}]}));});
test('production and unauthorized fixture writes are blocked',()=>{assert.throws(()=>validate({environment:{kind:'production',authorized:true},scenarios:[scenario]}));assert.throws(()=>validate({environment:{kind:'test',authorized:true},scenarios:[{...scenario,mutates:true}]}));});
test('sitemap namespace index, gzip, query parameters, limits, empty and XXE',async()=>{
 let origin;const server=http.createServer((req,res)=>{
  const xml=req.url==='/index.xml'?`<s:sitemapindex xmlns:s="http://www.sitemaps.org/schemas/sitemap/0.9"><s:sitemap><s:loc>${origin}/child.xml.gz</s:loc></s:sitemap></s:sitemapindex>`:req.url==='/empty.xml'?'<urlset></urlset>':req.url==='/xxe.xml'?'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><urlset/>':`<urlset><url><loc>${origin}/b?x=1</loc></url><url><loc>${origin}/a</loc></url><url><loc>${origin}/a</loc></url><url><loc>${origin}/image.png</loc></url></urlset>`;
  res.end(req.url.endsWith('.gz')?zlib.gzipSync(xml):xml);
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));origin=`http://127.0.0.1:${server.address().port}`;
 try{assert.deepEqual((await discover({url:origin+'/index.xml'})).routes,['/a','/b?x=1']);await assert.rejects(discover({url:origin+'/empty.xml'}));await assert.rejects(discover({url:origin+'/xxe.xml'}));await assert.rejects(discover({url:origin+'/index.xml',maxMaps:1}));}finally{server.close();}
});

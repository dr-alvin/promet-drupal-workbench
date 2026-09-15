// Pinned upstream 6.3.25 ignores a false ignoreHTTPSErrors value. Fix default
// before any browser context is created. Fail build if upstream source differs.
const fs=require('fs');
const file='/usr/local/lib/node_modules/backstopjs/core/util/runPlaywright.js';
const old='const ignoreHTTPSErrors = engineOptions.ignoreHTTPSErrors ? engineOptions.ignoreHTTPSErrors : true;';
const code=fs.readFileSync(file,'utf8');
if(code.split(old).length!==2)throw new Error('Unexpected Backstop TLS implementation; review patch');
fs.writeFileSync(file,code.replace(old,'const ignoreHTTPSErrors = false;'));

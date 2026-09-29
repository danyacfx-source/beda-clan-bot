const fs = require("fs");
const path = require("path");
const vm = require("vm");

// All element ids referenced in panel.js via $("...") — verify against index.html
const htmlPath = path.join(__dirname, "index.html");
const jsPath = path.join(__dirname, "panel.js");
const src = fs.readFileSync(jsPath, "utf8");
const html = fs.readFileSync(htmlPath, "utf8");

const usedIds = new Set();
const idRe = /\$\("([^"]+)"\)/g;
let m;
while ((m = idRe.exec(src))) usedIds.add(m[1]);

const htmlIds = new Set();
const hidRe = /id="([^"]+)"/g;
while ((m = hidRe.exec(html))) htmlIds.add(m[1]);

const missing = [...usedIds].filter((id) => !htmlIds.has(id));
console.log("ids referenced in JS:", usedIds.size);
console.log("ids present in HTML:", htmlIds.size);
if (missing.length) {
  console.log("MISSING IN HTML:", missing.join(", "));
  process.exit(1);
} else {
  console.log("OK: every $('...') id used by panel.js exists in index.html");
}

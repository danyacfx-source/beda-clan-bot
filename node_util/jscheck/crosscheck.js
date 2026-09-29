const fs = require("fs");
const path = require("path");
const vm = require("vm");

const file = path.resolve(__dirname, "../../app/core/webpanel/panel.js");
const src = fs.readFileSync(file, "utf8");
new vm.Script(src, { filename: file });
console.log("OK: panel.js parses successfully");

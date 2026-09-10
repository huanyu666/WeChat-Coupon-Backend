'use strict';

// Redirect pt-passport's one fixed Linux session file to the current flow.
// The hook runs only inside the short-lived passport child process.
const fs = require('fs');
const fsPromises = require('fs/promises');

const source = process.env.PT_PASSPORT_GLOBAL_SESSION_FILE || '';
const target = process.env.PT_PASSPORT_SESSION_FILE || '';

function redirected(value) {
  if (!source || !target) return value;
  if (typeof value === 'string' && value === source) return target;
  if (Buffer.isBuffer(value) && value.toString() === source) return Buffer.from(target);
  return value;
}

function wrapFirst(object, name) {
  const original = object[name];
  if (typeof original !== 'function') return;
  object[name] = function () {
    const args = Array.prototype.slice.call(arguments);
    args[0] = redirected(args[0]);
    return original.apply(this, args);
  };
}

function wrapTwo(object, name) {
  const original = object[name];
  if (typeof original !== 'function') return;
  object[name] = function () {
    const args = Array.prototype.slice.call(arguments);
    args[0] = redirected(args[0]);
    args[1] = redirected(args[1]);
    return original.apply(this, args);
  };
}

[
  'access', 'accessSync', 'appendFile', 'appendFileSync', 'chmod', 'chmodSync',
  'createReadStream', 'createWriteStream', 'existsSync', 'lstat', 'lstatSync',
  'open', 'openSync', 'readFile', 'readFileSync', 'rm', 'rmSync', 'stat',
  'statSync', 'truncate', 'truncateSync', 'unlink', 'unlinkSync', 'writeFile',
  'writeFileSync'
].forEach((name) => wrapFirst(fs, name));

['copyFile', 'copyFileSync', 'rename', 'renameSync'].forEach((name) => wrapTwo(fs, name));

[
  'access', 'appendFile', 'chmod', 'lstat', 'open', 'readFile', 'rm', 'stat',
  'truncate', 'unlink', 'writeFile'
].forEach((name) => wrapFirst(fsPromises, name));

['copyFile', 'rename'].forEach((name) => wrapTwo(fsPromises, name));

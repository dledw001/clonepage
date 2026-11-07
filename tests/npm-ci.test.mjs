import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';

const installer = fileURLToPath(new URL('../scripts/npm-ci.sh', import.meta.url)).replaceAll('\\', '/');
const shell = process.platform === 'win32'
  ? join(process.env.ProgramFiles ?? 'C:/Program Files', 'Git', 'bin', 'bash.exe') : '/bin/sh';
const runnable = existsSync(shell);

function run(mode) {
  const folder = mkdtempSync(join(tmpdir(), 'clonepage-npm-ci-'));
  const fixturePath = folder.replaceAll('\\', '/');
  mkdirSync(join(folder, 'bin'));
  mkdirSync(join(folder, 'cache', '_logs'), { recursive: true });
  const packageText = '{"name":"install-fixture","private":true}';
  const lockText = '{"lockfileVersion":3}';
  writeFileSync(join(folder, 'package.json'), packageText);
  writeFileSync(join(folder, 'package-lock.json'), lockText);
  const executable = (name, source) => {
    const path = join(folder, 'bin', name);
    writeFileSync(path, '#!/bin/sh\n' + source, { mode: 0o755 });
    chmodSync(path, 0o755);
  };
  executable('node', "printf 'v22.23.4\\n'\n");
  executable('npm', `
case "$1" in
  --version) printf '10.9.9\\n'; exit 0 ;;
  config) printf '%s/cache\\n' "$CI_FAKE_ROOT"; exit 0 ;;
esac
printf '%s\\n' "$*" >> "$CI_FAKE_ROOT/calls"
if [ "$1" != "ci" ]; then printf 'Unexpected command\\n' >&2; exit 99; fi
count=0
if [ -f "$CI_FAKE_ROOT/count" ]; then count=$(cat "$CI_FAKE_ROOT/count"); fi
count=$((count + 1))
printf '%s' "$count" > "$CI_FAKE_ROOT/count"
printf 'Helpful npm diagnostic\\n' > "$CI_FAKE_ROOT/cache/_logs/test-debug-0.log"
case "$CI_FAKE_MODE" in
  success) printf 'Installed locked dependencies\\n'; exit 0 ;;
  crash) if [ "$count" -eq 1 ]; then printf 'npm error Exit handler never called!\\n'; exit 1; fi ;;
  network) if [ "$count" -eq 1 ]; then printf 'npm error ECONNRESET registry request\\n'; exit 23; fi ;;
  exhausted) printf 'npm error ETIMEDOUT registry request\\n'; exit 23 ;;
  lockfile) printf 'npm error EUSAGE package.json and lockfile are not in sync\\n'; exit 2 ;;
  auth) printf 'npm error E401 authentication required\\n'; exit 3 ;;
  unknown) printf 'Unexpected lifecycle script failure\\n'; exit 7 ;;
esac
printf 'Installed locked dependencies\\n'
`);
  try {
    const result = spawnSync(shell, [installer], {
      cwd: folder,
      env: { ...process.env, PATH: join(folder, 'bin') + delimiter + process.env.PATH,
        CI_FAKE_ROOT: fixturePath, CI_FAKE_MODE: mode, TMPDIR: fixturePath },
      encoding: 'utf8', timeout: 10000,
    });
    assert.ifError(result.error);
    const calls = readFileSync(join(folder, 'calls'), 'utf8').trim().split('\n');
    assert.equal(readFileSync(join(folder, 'package.json'), 'utf8'), packageText);
    assert.equal(readFileSync(join(folder, 'package-lock.json'), 'utf8'), lockText);
    assert.equal(readdirSync(folder).some(name => name.startsWith('tmp.')), false, 'temporary install logs are removed');
    return { ...result, calls: calls.filter(call => call.startsWith('ci ')), retries: (result.stdout.match(/retrying once/g) ?? []).length };
  } finally {
    // Only delete the exact test-owned directory created under the system temp folder.
    assert.equal(dirname(realpathSync(folder)), realpathSync(tmpdir()));
    rmSync(folder, { recursive: true, force: true });
  }
}

test('Docker installer succeeds once with locked dev and native optional dependencies retained', { skip: !runnable }, () => {
  const result = run('success');
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(result.calls.length, 1);
  assert.match(result.calls[0], /--include=dev/);
  assert.match(result.calls[0], /--no-audit/);
  assert.match(result.calls[0], /--fetch-retries=2/);
  assert.doesNotMatch(result.calls[0], /--omit|--ignore-scripts|install/);
  assert.match(result.stdout, /v22.23.4/);
  assert.match(result.stdout, /10.9.9/);
});

for (const mode of ['crash', 'network']) {
  test(`Docker installer recovers from a transient ${mode} failure once`, { skip: !runnable }, () => {
    const result = run(mode);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.equal(result.calls.length, 2);
    assert.equal(result.retries, 1);
    assert.match(result.stdout, /Helpful npm diagnostic/);
    assert.match(result.stdout, /retrying once/);
  });
}

test('Docker installer stops after two transient failures and retains npm exit status and diagnostics', { skip: !runnable }, () => {
  const result = run('exhausted');
  assert.equal(result.status, 23, result.stdout + result.stderr);
  assert.equal(result.calls.length, 2);
  assert.equal(result.retries, 1);
  assert.match(result.stdout, /ETIMEDOUT/);
  assert.match(result.stdout, /Helpful npm diagnostic/);
});

for (const [mode, code] of [['lockfile',2], ['auth',3], ['unknown',7]]) {
  test(`Docker installer fails immediately for ${mode} errors`, { skip: !runnable }, () => {
    const result = run(mode);
    assert.equal(result.status, code, result.stdout + result.stderr);
    assert.equal(result.calls.length, 1);
    assert.equal(result.retries, 0);
  });
}

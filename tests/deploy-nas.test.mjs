import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';
import { test } from 'node:test';

const script = fileURLToPath(new URL('../scripts/deploy-nas.sh', import.meta.url));
const runnable = process.platform === 'linux' && spawnSync('sh', ['-c', 'command -v git && command -v flock']).status === 0;

function fixture() {
  const folder = mkdtempSync(join(tmpdir(), 'clonepage-deploy-'));
  const origin = join(folder, 'origin.git');
  const source = join(folder, 'source');
  const checkout = join(folder, 'checkout');
  const bin = join(folder, 'bin');
  mkdirSync(bin);
  const git = (cwd, ...args) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git(folder, 'init', '--bare', origin);
  git(folder, 'init', '--initial-branch=main', source);
  writeFileSync(join(source, '.gitignore'), 'data/\n');
  writeFileSync(join(source, 'app.txt'), 'version one\n');
  git(source, 'add', '.');
  git(source, '-c', 'user.name=Deployment test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Initial');
  git(source, 'remote', 'add', 'origin', origin);
  git(source, 'push', 'origin', 'main');
  git(folder, 'clone', '--branch', 'main', origin, checkout);
  const executable = (name, content) => {
    const path = join(bin, name);
    writeFileSync(path, '#!/bin/sh\n' + content);
    chmodSync(path, 0o755);
  };
  executable('id', 'printf "0\\n"\n');
  executable('su', 'printf "%s\\n" "$3" >> "$DEPLOY_TEST_ROOT/owners"\nexec /bin/sh -c "$5"\n');
  executable('docker', `
printf '%s\\n' "$*" >> "$DEPLOY_TEST_ROOT/docker-calls"
case "$*" in
  'compose config --quiet') [ "$DEPLOY_TEST_FAIL" != config ] || exit 41 ;;
  'compose build') [ "$DEPLOY_TEST_FAIL" != build ] || exit 42 ;;
  'compose up -d --no-build') [ "$DEPLOY_TEST_FAIL" != start ] || exit 43 ;;
  'compose ps -q '*) printf 'container-%s\\n' "$4" ;;
  inspect*) if [ "$DEPLOY_TEST_FAIL" = worker ] && [ "$4" = container-worker ]; then printf 'false\\n'; else printf 'true\\n'; fi ;;
esac
`);
  executable('curl', '[ "$DEPLOY_TEST_FAIL" != health ]\n');
  executable('sleep', 'exit 0\n');
  const state = join(checkout, 'data', 'deployment');
  return {
    folder, checkout, source, state, git,
    revision: () => git(checkout, 'rev-parse', 'HEAD'),
    marker: () => existsSync(join(state, 'deployed-commit')) ? readFileSync(join(state, 'deployed-commit'), 'utf8').trim() : null,
    calls: () => existsSync(join(folder, 'docker-calls')) ? readFileSync(join(folder, 'docker-calls'), 'utf8').trim().split('\n') : [],
    run: (fail = '') => spawnSync('/bin/sh', [script], {
      env: { ...process.env, PATH: bin + ':' + process.env.PATH, CLONEPAGE_DEPLOY_DIR: checkout,
        CLONEPAGE_DEPLOY_USER: 'fixture-owner', DEPLOY_TEST_ROOT: folder, DEPLOY_TEST_FAIL: fail },
      encoding: 'utf8', timeout: 20000,
    }),
    push() {
      writeFileSync(join(source, 'app.txt'), 'version two\n');
      git(source, 'add', '.');
      git(source, '-c', 'user.name=Deployment test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Update');
      git(source, 'push', 'origin', 'main');
      return git(source, 'rev-parse', 'HEAD');
    },
    close() {
      assert.equal(dirname(realpathSync(folder)), realpathSync(tmpdir()));
      rmSync(folder, { recursive: true, force: true });
    },
  };
}

function check(name, body) {
  test(name, { skip: !runnable }, () => {
    const f = fixture();
    try { body(f); } finally { f.close(); }
  });
}

check('NAS deployment builds before restarting, checks readiness, then skips the deployed commit', f => {
  let result = f.run();
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(f.marker(), f.revision());
  const calls = f.calls();
  assert.ok(calls.includes('compose build'));
  assert.ok(calls.includes('compose up -d --no-build'));
  assert.ok(calls.indexOf('compose build') < calls.indexOf('compose up -d --no-build'));
  assert.ok(!calls.some(call => call.includes('--force-recreate')), 'unchanged services are not forcibly replaced');
  assert.ok(calls.some(call => call.includes('container-worker')));
  assert.equal(readFileSync(join(f.folder, 'owners'), 'utf8').trim(), 'fixture-owner');
  result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(f.calls(), calls, 'unchanged commits do not rebuild or restart');
});

check('NAS deployment fetches and fast-forwards to a new remote commit', f => {
  assert.equal(f.run().status, 0);
  const previous = f.marker();
  const target = f.push();
  assert.notEqual(target, previous);
  assert.equal(f.run().status, 0);
  assert.equal(f.marker(), target);
  assert.equal(f.revision(), target);
});

for (const [mode, status] of [['config', 41], ['build', 42], ['start', 43], ['health', 1], ['worker', 1]]) {
  check(`NAS deployment retains the previous successful commit after ${mode} failure and retries without another push`, f => {
    assert.equal(f.run().status, 0);
    const previous = f.marker();
    const target = f.push();
    const before = f.calls().length;
    const result = f.run(mode);
    assert.equal(result.status, status, result.stderr);
    assert.equal(f.marker(), previous);
    assert.equal(f.revision(), target, 'checkout may advance even when deployment fails');
    if (mode === 'build' || mode === 'config') {
      assert.ok(!f.calls().slice(before).some(call => call.startsWith('compose up')), 'no restart after failed build/config');
    }
    assert.match(result.stderr, /Deployment failed/);
    assert.equal(f.run().status, 0);
    assert.equal(f.marker(), target);
  });
}

for (const mode of ['dirty', 'branch', 'ahead']) {
  check(`NAS deployment refuses ${mode} checkout without touching running containers`, f => {
    if (mode === 'dirty') writeFileSync(join(f.checkout, 'untracked.txt'), 'local work');
    if (mode === 'branch') f.git(f.checkout, 'checkout', '-b', 'experiment');
    if (mode === 'ahead') {
      writeFileSync(join(f.checkout, 'local.txt'), 'local work');
      f.git(f.checkout, 'add', '.');
      f.git(f.checkout, '-c', 'user.name=Deployment test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Local change');
    }
    const result = f.run();
    assert.notEqual(result.status, 0, result.stderr);
    assert.equal(f.marker(), null);
    assert.deepEqual(f.calls(), []);
  });
}

check('NAS deployment skips a run while another process holds its lock', f => {
  mkdirSync(f.state, { recursive: true });
  const result = spawnSync('flock', [join(f.state, 'deploy.lock'), '/bin/sh', script], {
    env: { ...process.env, PATH: join(f.folder, 'bin') + ':' + process.env.PATH,
      CLONEPAGE_DEPLOY_DIR: f.checkout, CLONEPAGE_DEPLOY_USER: 'fixture-owner', DEPLOY_TEST_ROOT: f.folder, DEPLOY_TEST_FAIL: '' },
    encoding: 'utf8', timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Another Clonepage deployment/);
  assert.deepEqual(f.calls(), []);
  assert.equal(f.marker(), null);
});

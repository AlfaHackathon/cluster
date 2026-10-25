import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const endpoint = 'https://inference.cheremha.ru/v1';
// Explicit synthetic interpolation values; never read cluster/AgentService .env,
// resolve service env_file contents, contact the daemon or call remote models.
const synthetic = {
  PATH: process.env.PATH,
  HOME: '/tmp',
  JWT_SECRET: 'synthetic-compose-validation-only',
  JWT_ISSUER: 'synthetic',
  JWT_AUDIENCE: 'synthetic',
  POSTGRES_DB: 'synthetic',
  POSTGRES_USER: 'synthetic',
  POSTGRES_PASSWORD: 'synthetic-only',
  AGENT_POSTGRES_PASSWORD: 'synthetic-only',
  INTERNAL_SECRET: 'synthetic-only',
};

function render(file, overrides = {}) {
  return JSON.parse(execFileSync('docker', [
    'compose', '--env-file', '/dev/null', '-f', file,
    'config', '--no-env-resolution', '--format', 'json',
  ], { cwd: root, env: { ...synthetic, ...overrides }, encoding: 'utf8' }));
}

for (const file of ['docker-compose-dev.yaml', 'docker-compose-prod.yaml']) {
  test(`${file}: remote inference works without a local model directory`, () => {
    const config = render(file);
    const services = config.services;
    const context = services['context-service'];
    assert.equal(context.environment.Embeddings__BaseUrl, endpoint);
    assert.equal(context.environment.Reranker__BaseUrl, endpoint);
    assert.equal(context.environment.Embeddings__Model, 'multilingual-e5-small');
    assert.equal(context.environment.Reranker__Model, 'mmarco-reranker');
    assert.equal(context.environment.Embeddings__Dimensions, '384');
    assert.equal(context.environment.Embeddings__QueryPrefix, 'query: ');
    assert.equal(context.environment.Embeddings__PassagePrefix, 'passage: ');
    assert.equal(context.environment.Embeddings__ApiKey, '');
    assert.equal(context.environment.Reranker__ApiKey, '');
    assert.equal(Object.hasOwn(services, 'context-inference'), false);
    assert.equal(services['agent-service'].depends_on['context-service'].condition, 'service_healthy');
    for (const service of Object.values(services)) {
      assert.equal(service.network_mode, undefined);
      assert.equal((service.volumes ?? []).some(volume => volume.target === '/models'), false);
      for (const dependency of Object.keys(service.depends_on ?? {})) {
        assert.ok(Object.hasOwn(services, dependency), `Missing dependency ${dependency}`);
      }
    }
  });

  test(`${file}: independent remote endpoints, model IDs and optional keys propagate`, () => {
    const config = render(file, {
      CONTEXT_EMBEDDINGS_BASE_URL: 'https://embed.example.invalid/v1',
      CONTEXT_RERANKER_BASE_URL: 'https://rank.example.invalid/v1',
      CONTEXT_EMBEDDINGS_MODEL: 'existing-served-embedding-id',
      CONTEXT_RERANKER_MODEL: 'existing-served-reranker-id',
      CONTEXT_EMBEDDINGS_API_KEY: 'synthetic-embedding-key',
      CONTEXT_RERANKER_API_KEY: 'synthetic-reranker-key',
    });
    const env = config.services['context-service'].environment;
    assert.equal(env.Embeddings__BaseUrl, 'https://embed.example.invalid/v1');
    assert.equal(env.Reranker__BaseUrl, 'https://rank.example.invalid/v1');
    assert.equal(env.Embeddings__Model, 'existing-served-embedding-id');
    assert.equal(env.Reranker__Model, 'existing-served-reranker-id');
    assert.equal(env.Embeddings__ApiKey, 'synthetic-embedding-key');
    assert.equal(env.Reranker__ApiKey, 'synthetic-reranker-key');
  });

  test(`${file}: no historical local inference interpolation remains`, () => {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /CONTEXT_MODEL_DIRECTORY|context-inference|127\.0\.0\.1:5183|\/models/);
  });
}

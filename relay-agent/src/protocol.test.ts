import assert from 'node:assert/strict';
import test from 'node:test';
import { isAllowedApiPath, parseRelayCommand } from './protocol.js';

test('only fixed read API resources can be sent through the relay agent', () => {
  assert.equal(isAllowedApiPath('/api/v1/search?q=国债'), true);
  assert.equal(isAllowedApiPath('/api/v1/news/0f8fad5b-d9cb-469f-a165-70867728950e/analysis'), true);
  assert.equal(isAllowedApiPath('/api/v1/internal/ingestion'), false);
  assert.equal(isAllowedApiPath('//other.example/api/v1/news'), false);
  assert.equal(isAllowedApiPath('/api/v1/news/uuid'), false);
  assert.equal(isAllowedApiPath('/api/v1/news?target=http://127.0.0.1'), false);
  assert.equal(isAllowedApiPath('/api/v1/news?limit=1&limit=2'), false);
});

test('relay commands require a bounded bearer credential and cannot choose a target origin', () => {
  const valid = JSON.stringify({ id: 'request-1', method: 'GET', path: '/api/v1/news?limit=1', authorization: 'Bearer test-client-token' });
  assert.deepEqual(parseRelayCommand(valid), {
    id: 'request-1', method: 'GET', path: '/api/v1/news?limit=1', authorization: 'Bearer test-client-token',
  });
  assert.equal(parseRelayCommand(valid.replace('Bearer test-client-token', 'token-without-scheme')), null);
  assert.equal(parseRelayCommand(valid.replace('/api/v1/news?limit=1', 'https://evil.invalid/api/v1/news')), null);
  assert.equal(parseRelayCommand(valid.replace('"GET"', '"POST"')), null);
});

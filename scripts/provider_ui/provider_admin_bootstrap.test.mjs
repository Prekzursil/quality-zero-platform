import test from 'node:test';
import assert from 'node:assert/strict';
import { runCommand } from './provider_admin_bootstrap.mjs';

test('runCommand dispatches bootstrap requests through provider normalization', async () => {
  const calls = [];
  await runCommand(
    { command: 'bootstrap', provider: 'Chromatic' },
    {
      normalizeProvider(provider) {
        calls.push(['normalize', provider]);
        return 'chromatic';
      },
      bootstrap: async (args) => {
        calls.push(['bootstrap', args.provider]);
      },
      listProviders: async () => {
        calls.push(['list']);
      },
      openOrInspect: async () => {
        calls.push(['openOrInspect']);
      },
      log: () => {
        calls.push(['log']);
      },
      renderHelp: () => 'help'
    }
  );

  assert.deepEqual(calls, [
    ['normalize', 'Chromatic'],
    ['bootstrap', 'Chromatic']
  ]);
});

test('runCommand prints help for default commands without provider normalization', async () => {
  const calls = [];
  await runCommand(
    { command: 'help' },
    {
      normalizeProvider: (provider) => {
        calls.push(['normalize', provider]);
        return provider;
      },
      bootstrap: async () => {
        calls.push(['bootstrap']);
      },
      listProviders: async () => {
        calls.push(['list']);
      },
      openOrInspect: async () => {
        calls.push(['openOrInspect']);
      },
      log: (message) => {
        calls.push(['log', message]);
      },
      renderHelp: () => 'provider help'
    }
  );

  assert.deepEqual(calls, [['log', 'provider help']]);
});

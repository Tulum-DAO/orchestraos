/**
 * Imported FIRST by server.ts. Route and service modules read the data dir at import (lib/config.ts
 * dataDir()); with no orchestra.toml that throws ConfigError while ES modules are still evaluating,
 * before any code in server.ts runs, and Node printed a stack trace. Static imports evaluate in order,
 * so this handler is in place before any of them: a ConfigError becomes ONE plain line naming the
 * missing file and the fix, exit 1. Anything else is rethrown untouched.
 */
process.on('uncaughtException', (err: Error) => {
  if (err && err.name === 'ConfigError') {
    console.error(`orchestra api: ${err.message}`);
    process.exit(1);
  }
  throw err;
});

export {};

import * as path from 'node:path';
import { runTests } from '@vscode/test-electron';

async function main(): Promise<void> {
  // out/test/runTests.js -> out/test -> out -> the extension root.
  const extensionDevelopmentPath = path.resolve(__dirname, '../..');
  await runTests({
    extensionDevelopmentPath,
    extensionTestsPath: path.resolve(__dirname, './integration/index'),
    // Forwarded so the suite can point the extension at a known-good
    // interpreter. Not a convenience: a machine whose PATH `python` is 3.9
    // builds a venv that installs none of the bundled wheels, and the suite
    // should be testing the extension rather than the host's PATH.
    extensionTestsEnv: {
      PYSQLSUGGESTIONS_TEST_PYTHON: process.env.PYSQLSUGGESTIONS_TEST_PYTHON,
    },
  });
}

void main().catch((error: unknown) => {
  console.error('integration tests failed', error);
  process.exit(1);
});

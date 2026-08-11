import * as path from 'node:path';
import Mocha from 'mocha';

/** Entry point VS Code calls inside the test instance. */
export function run(): Promise<void> {
  // Generous: the first run builds the venv from the bundled wheels, and that
  // is the thing most worth testing rather than most worth skipping.
  const mocha = new Mocha({ ui: 'tdd', color: true, timeout: 180000 });
  mocha.addFile(path.resolve(__dirname, 'completion.test.js'));
  return new Promise((resolve, reject) => {
    mocha.run((failures) => {
      if (failures === 0) {
        resolve();
      } else {
        reject(new Error(`${String(failures)} failing`));
      }
    });
  });
}

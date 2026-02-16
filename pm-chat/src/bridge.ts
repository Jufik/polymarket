import { spawn } from "node:child_process";
import { resolve } from "node:path";

/** Root of the polymarket project (parent of pm-chat/) */
export const PROJECT_ROOT = resolve(import.meta.dirname, "../..");

/** Root of strategy directories */
export const STRATEGIES_ROOT = resolve(PROJECT_ROOT, "strategies");

export interface CallPythonOptions {
  timeoutMs?: number;
  cwd?: string;
}

/**
 * Call a Python function via bridge.py subprocess.
 * Returns parsed JSON output.
 */
export async function callPython(
  module: string,
  func: string,
  args: Record<string, unknown>,
  options: CallPythonOptions = {}
): Promise<unknown> {
  const { timeoutMs = 120_000, cwd = PROJECT_ROOT } = options;

  return new Promise((promiseResolve, reject) => {
    const proc = spawn(
      "uv",
      [
        "run",
        "python",
        "-m",
        "polymarket_pipeline.cli.bridge",
        "--module",
        module,
        "--func",
        func,
        "--args",
        JSON.stringify(args),
      ],
      { cwd, stdio: ["ignore", "pipe", "pipe"] }
    );

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (data: Buffer) => {
      stdout += data.toString();
    });
    proc.stderr.on("data", (data: Buffer) => {
      stderr += data.toString();
    });

    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error(`Python bridge timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(
          new Error(
            `Python bridge exited with code ${code}: ${stderr.trim()}`
          )
        );
        return;
      }
      try {
        promiseResolve(JSON.parse(stdout));
      } catch {
        reject(
          new Error(`Failed to parse bridge output: ${stdout.slice(0, 200)}`)
        );
      }
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Failed to spawn Python bridge: ${err.message}`));
    });
  });
}

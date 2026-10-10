// Cold-box telemetry for Scenario 2 (S20). With an MQTT broker on MQTT_HOST:MQTT_PORT
// (default 127.0.0.1:1883), runs the real simulator, `scripts/simulate_telemetry.py --profile
// excursion`, whose readings reach the hub through `make ingest` (which must be running). With
// no broker (e.g. CI), posts the same profile straight to the hub's ingest endpoint
// (`POST /internal/telemetry`, the ingest token), as iot-ingest would. E2E_TELEMETRY=mqtt|hub
// forces one path.
import { type ChildProcess, spawn } from "node:child_process";
import { connect } from "node:net";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../..", import.meta.url));
const MQTT_HOST = process.env.MQTT_HOST ?? "127.0.0.1";
const MQTT_PORT = Number(process.env.MQTT_PORT ?? 1883);
const HUB = process.env.E2E_HUB_API ?? "http://127.0.0.1:8000/api/v1";
// The hub's dev default (services/hub-api/app/config.py); a hub outside dev refuses it.
const INGEST_TOKEN = process.env.INGEST_TOKEN ?? "dev-only-ingest-token-change-me";
const INTERVAL_S = 2;

// The simulator's excursion profile: normal readings, then 9.1 and 9.4 °C, then normal again.
const NORMAL = [3.5, 5.5] as const;
const EXCURSION = [9.1, 9.4] as const;
// Normal readings first: the simulator's are fixed (60 s); posting directly needs only enough
// to show a reading before the excursion.
const HUB_WARMUP_READINGS = 3;

export type Telemetry = {
  /** "mqtt": the simulator through the broker and iot-ingest; "hub": posted directly. */
  mode: "mqtt" | "hub";
  /** Seconds until the first out-of-range reading. */
  warmupSeconds: number;
  /** What was published, for the test report. */
  log: string[];
  stop: () => Promise<void>;
};

function brokerUp(): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host: MQTT_HOST, port: MQTT_PORT, timeout: 1000 });
    const done = (up: boolean) => {
      socket.destroy();
      resolve(up);
    };
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

const normal = () => Math.round((NORMAL[0] + Math.random() * (NORMAL[1] - NORMAL[0])) * 100) / 100;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function simulator(device: string): Telemetry {
  const log: string[] = [];
  const child: ChildProcess = spawn(
    "uv",
    [
      "run",
      "scripts/simulate_telemetry.py",
      ...["--device", device, "--profile", "excursion", "--interval", String(INTERVAL_S)],
      ...["--host", MQTT_HOST, "--port", String(MQTT_PORT)],
    ],
    { cwd: ROOT, detached: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  const record = (chunk: Buffer) => log.push(...chunk.toString().trim().split("\n"));
  child.stdout?.on("data", record);
  child.stderr?.on("data", record);
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  return {
    mode: "mqtt",
    warmupSeconds: 60, // scripts/simulate_telemetry.py WARMUP_S
    log,
    stop: async () => {
      if (child.exitCode === null && child.pid !== undefined) {
        process.kill(-child.pid, "SIGINT"); // uv and the script: the script exits cleanly
        await Promise.race([exited, sleep(5000)]);
        if (child.exitCode === null) process.kill(-child.pid, "SIGKILL");
      }
    },
  };
}

function poster(device: string): Telemetry {
  const log: string[] = [];
  let running = true;
  const post = async (temp_c: number) => {
    const ts = new Date().toISOString().slice(0, 19) + "Z";
    const reading = { device_id: device, ts, temp_c, battery: 82 };
    const response = await fetch(`${HUB}/internal/telemetry`, {
      method: "POST",
      headers: { Authorization: `Bearer ${INGEST_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ readings: [reading] }),
    });
    const result = await response.text();
    log.push(`${JSON.stringify(reading)} -> ${response.status} ${result}`);
    if (!response.ok) throw new Error(`POST /internal/telemetry: ${response.status} ${result}`);
  };
  // A failed post stops the loop; `stop` reports it.
  const loop = (async () => {
    const profile = [...Array.from({ length: HUB_WARMUP_READINGS }, normal), ...EXCURSION];
    for (let i = 0; running; i++) {
      await post(i < profile.length ? profile[i]! : normal());
      await sleep(INTERVAL_S * 1000);
    }
  })().then(
    () => undefined,
    (error: unknown) => error,
  );
  return {
    mode: "hub",
    warmupSeconds: HUB_WARMUP_READINGS * INTERVAL_S,
    log,
    stop: async () => {
      running = false;
      const error = await loop;
      if (error) throw error;
    },
  };
}

/** Starts the excursion profile for `device`. */
export async function startExcursion(device: string): Promise<Telemetry> {
  const forced = process.env.E2E_TELEMETRY;
  const mode = forced === "mqtt" || forced === "hub" ? forced : (await brokerUp()) ? "mqtt" : "hub";
  return mode === "mqtt" ? simulator(device) : poster(device);
}

const BASE = "http://localhost:8000";

export type ProcessStatus = { label: string; running: boolean };
export type Device = {
  device_id: string;
  tipo?: string;
  atuador?: string;
  ip?: string;
  estado?: string;
};
export type Reading = {
  device_id: string;
  value: string;
  unit: string;
  ts: string;
};

export async function getProcesses(): Promise<Record<string, ProcessStatus>> {
  const res = await fetch(`${BASE}/processes`, { cache: "no-store" });
  return res.json();
}

export async function startProcess(slot: string) {
  const res = await fetch(`${BASE}/processes/${slot}/start`, { method: "POST" });
  return res.json();
}

export async function stopProcess(slot: string) {
  const res = await fetch(`${BASE}/processes/${slot}/stop`, { method: "POST" });
  return res.json();
}

export async function pingGateway(): Promise<{ online: boolean }> {
  try {
    const res = await fetch(`${BASE}/gateway/ping`, { cache: "no-store" });
    return res.json();
  } catch {
    return { online: false };
  }
}

export async function listDevices(): Promise<{ devices: Device[]; count: number }> {
  const res = await fetch(`${BASE}/gateway/devices`, { cache: "no-store" });
  return res.json();
}

export async function getAvg(deviceId: string): Promise<{ message: string }> {
  const res = await fetch(`${BASE}/gateway/avg/${deviceId}`, { cache: "no-store" });
  return res.json();
}

export async function sendCommand(deviceId: string, state: boolean): Promise<{ message: string }> {
  const res = await fetch(`${BASE}/gateway/command`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: deviceId, state }),
  });
  return res.json();
}

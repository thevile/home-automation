#!/usr/bin/env node
// Install or update one separate dashboard from a JSON file; never edit Default.
//   node scripts/ha-install-dashboard.mjs <url-path> <title> <icon> <config.json>
import { readFile, mkdir, writeFile } from "node:fs/promises";

const [url_path, title, icon, file] = process.argv.slice(2);
if (!file || !url_path.includes("-")) {
  console.error("Usage: ha-install-dashboard.mjs <url-path-with-dash> <title> <icon> <config.json>");
  process.exit(2);
}
const root = new URL("../", import.meta.url);
const token = (await readFile(new URL(".local/ha-token", root), "utf8")).trim();
const config = JSON.parse(await readFile(file, "utf8"));
const server = process.env.HASS_SERVER ?? "http://homeassistant.local:8123";
const socket = new WebSocket(server.replace(/^http/, "ws") + "/api/websocket");
const pending = new Map();
let counter = 1;
const auth = new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("HA authentication timed out")), 15000);
  socket.addEventListener("error", () => reject(new Error("HA WebSocket connection failed")));
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.type === "auth_required") socket.send(JSON.stringify({ type: "auth", access_token: token }));
    else if (message.type === "auth_ok") { clearTimeout(timer); resolve(); }
    else if (message.type === "auth_invalid") { clearTimeout(timer); reject(new Error("HA authentication failed")); }
    else if (pending.has(message.id)) {
      const { resolve, reject, timer } = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(timer);
      message.success ? resolve(message.result) : reject(new Error(JSON.stringify(message.error)));
    }
  });
});

function request(type, params = {}) {
  const id = counter++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${type} timed out`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, type, ...params }));
  });
}

try {
  await auth;
  const dashboards = await request("lovelace/dashboards/list");
  const existing = dashboards.find((item) => item.url_path === url_path);
  if (existing) {
    if (existing.title !== title) throw new Error("Dashboard path is already used by an unrelated dashboard");
    const previous = await request("lovelace/config", { url_path });
    const backupDir = new URL(".local/dashboard-backups/", root);
    await mkdir(backupDir, { recursive: true, mode: 0o700 });
    await writeFile(new URL(`${url_path}-${Date.now()}.json`, backupDir), JSON.stringify(previous, null, 2), { mode: 0o600 });
  } else {
    await request("lovelace/dashboards/create", {
      url_path, title, icon, show_in_sidebar: true, require_admin: false,
    });
  }
  await request("lovelace/config/save", { url_path, config });
  const saved = await request("lovelace/config", { url_path });
  if (JSON.stringify(saved) !== JSON.stringify(config)) throw new Error("Dashboard read-back mismatch");
  console.log(`Verified dashboard: ${server}/${url_path}`);
} finally {
  socket.close();
}

#!/usr/bin/env node

import { readFile } from "node:fs/promises";

const server = process.env.HASS_SERVER ?? "http://homeassistant.local:8123";
const tokenFile = new URL("../.local/ha-token", import.meta.url);
const token = (await readFile(tokenFile, "utf8")).trim();
const socketUrl = server.replace(/^http/, "ws").replace(/\/$/, "") + "/api/websocket";
const socket = new WebSocket(socketUrl);

function receive() {
  return new Promise((resolve, reject) => {
    socket.addEventListener("message", (event) => resolve(JSON.parse(event.data)), { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
}

function send(message) {
  socket.send(JSON.stringify(message));
}

await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let message = await receive();
if (message.type !== "auth_required") throw new Error(`Unexpected message: ${message.type}`);
send({ type: "auth", access_token: token });
message = await receive();
if (message.type !== "auth_ok") throw new Error(`Authentication failed: ${message.message ?? message.type}`);

send({ id: 1, type: "lovelace/config", url_path: "dashboard-default" });
message = await receive();
if (!message.success) throw new Error(`Could not read dashboard: ${JSON.stringify(message.error)}`);

const config = message.result;
let changed = 0;
for (const view of config.views ?? []) {
  for (const section of view.sections ?? []) {
    for (const card of section.cards ?? []) {
      if (card.type !== "custom:apexcharts-card") continue;
      const series = card.series?.[0];
      if (series?.entity !== "sensor.nordpool_kwh_fi_eur_3_10_0255") continue;

      const isTomorrow = card.span?.offset === "+1d";
      card.header.title = `Energy price ${isTomorrow ? "tomorrow" : "today"} (snt/kWh)`;
      series.unit = "snt/kWh";
      series.float_precision = 1;
      series.color_threshold = [
        { value: 5, color: "green" },
        { value: 10, color: "gray" },
        { value: 15, color: "red" },
      ];
      series.data_generator = isTomorrow
        ? `if (!entity.attributes.tomorrow_valid) {
  return [];
}
return entity.attributes.raw_tomorrow.map((period) => {
  return [new Date(period.start).getTime(), period.value * 100];
});
`
        : `return entity.attributes.raw_today.map((period) => {
  return [new Date(period.start).getTime(), period.value * 100];
});
`;
      changed += 1;
    }
  }
}

if (changed !== 2) throw new Error(`Expected 2 Nord Pool cards, found ${changed}`);

send({ id: 2, type: "lovelace/config/save", url_path: "dashboard-default", config });
message = await receive();
if (!message.success) throw new Error(`Could not save dashboard: ${JSON.stringify(message.error)}`);

send({ id: 3, type: "lovelace/config", url_path: "dashboard-default" });
message = await receive();
if (!message.success) throw new Error(`Could not verify dashboard: ${JSON.stringify(message.error)}`);

const cards = message.result.views.flatMap((view) =>
  (view.sections ?? []).flatMap((section) =>
    (section.cards ?? []).filter((card) => card.type === "custom:apexcharts-card"),
  ),
);
const verified = cards.filter((card) => {
  const series = card.series?.[0];
  return (
    series?.entity === "sensor.nordpool_kwh_fi_eur_3_10_0255" &&
    series.unit === "snt/kWh" &&
    series.data_generator.includes("period.value * 100")
  );
});
if (verified.length !== 2) throw new Error(`Saved dashboard verification failed`);

console.log("Updated and verified 2 Nord Pool dashboard cards.");
socket.close();


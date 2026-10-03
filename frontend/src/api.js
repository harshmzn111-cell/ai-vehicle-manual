import axios from "axios";

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || "http://localhost:4000";

const client = axios.create({
  baseURL: GATEWAY_URL,
  timeout: 35000,
});

export async function fetchVehicleCatalog() {
  const { data } = await client.get("/api/vehicles");
  return data;
}

export async function requestDiagnosis({ vehicle, input, debug = false }) {
  const { data } = await client.post("/api/diagnose", { vehicle, input, debug });
  return data;
}

/**
 * Free-text mode: "Swift 2023 Petrol ZXi is showing P0128" -> extracts
 * { vehicle: {brand, model, year, fuel, variant}, problem, missing_fields, is_complete }.
 * Fields the assistant couldn't confidently extract come back null rather than guessed.
 */
export async function extractVehicleInfo(query) {
  const { data } = await client.post("/api/extract", { query });
  return data;
}

/**
 * Uploads a manual PDF + vehicle fields to the gateway, which proxies to
 * the AI service's /api/v1/upload-manual (save to disk, register, chunk,
 * embed, ingest into ChromaDB). onProgress receives 0-100 as the upload
 * streams, via axios's onUploadProgress.
 */
export async function uploadManual({ file, vehicle, onProgress }) {
  const form = new FormData();
  form.append("file", file);
  form.append("brand", vehicle.brand);
  form.append("model", vehicle.model);
  form.append("year", String(vehicle.year));
  form.append("fuel", vehicle.fuel);
  form.append("variant", vehicle.variant || "Base");

  const { data } = await client.post("/api/upload-manual", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000, // PDFs + server-side ingestion take longer than a diagnose call
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) {
        onProgress(Math.round((evt.loaded * 100) / evt.total));
      }
    },
  });
  return data;
}

/** Lists registered manuals, optionally filtered to one vehicle configuration. */
export async function fetchManuals(vehicle) {
  const params = vehicle ? { brand: vehicle.brand, model: vehicle.model, year: vehicle.year, fuel: vehicle.fuel } : {};
  const { data } = await client.get("/api/manuals", { params });
  return data;
}

export default client;

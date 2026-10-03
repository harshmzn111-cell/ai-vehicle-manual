/**
 * server.js
 * =========
 * Phase 3 — Node.js + Express API Gateway
 *
 * Responsibilities:
 *   - Serve available vehicle metadata (brand/model/year/fuel options) to the frontend.
 *   - Validate and forward diagnosis requests to the Python FastAPI AI microservice.
 *   - Centralize CORS, rate limiting, logging, and error handling for the frontend.
 *
 * Run locally:
 *   npm install
 *   npm run dev      (nodemon, auto-reload)
 *   npm start         (plain node)
 */

require("dotenv").config();

const express = require("express");
const cors = require("cors");
const helmet = require("helmet");
const morgan = require("morgan");
const rateLimit = require("express-rate-limit");
const axios = require("axios");
const multer = require("multer");
const FormData = require("form-data");

const vehicleData = require("./data/vehicles.json");

const app = express();

const PORT = process.env.PORT || 4000;
const AI_SERVICE_URL = process.env.AI_SERVICE_URL || "http://localhost:8000";
const AI_SERVICE_TIMEOUT_MS = Number(process.env.AI_SERVICE_TIMEOUT_MS || 30000);
const UPLOAD_TIMEOUT_MS = Number(process.env.UPLOAD_TIMEOUT_MS || 120000); // PDFs take longer than a diagnose call
const MAX_UPLOAD_BYTES = Number(process.env.MAX_UPLOAD_BYTES || 62914560); // 60MB, matches ai-service default
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || "http://localhost:5173").split(",");

// --------------------------------------------------------------------------
// Middleware
// --------------------------------------------------------------------------
app.use(helmet());
app.use(morgan(process.env.NODE_ENV === "production" ? "combined" : "dev"));
app.use(express.json({ limit: "1mb" }));
app.use(
  cors({
    origin: ALLOWED_ORIGINS,
    methods: ["GET", "POST"],
  })
);

// Basic abuse protection — diagnosis calls hit an LLM + optional web search, so keep it tight.
const diagnoseLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: Number(process.env.DIAGNOSE_RATE_LIMIT || 20),
  message: { error: "Too many diagnosis requests. Please wait a moment and try again." },
});

// --------------------------------------------------------------------------
// Health
// --------------------------------------------------------------------------
app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "gateway" });
});

// --------------------------------------------------------------------------
// GET /api/vehicles
// Returns the brand -> model -> years -> fuel options tree used to
// populate the frontend's cascading dropdowns.
// --------------------------------------------------------------------------
app.get("/api/vehicles", (_req, res) => {
  res.json(vehicleData);
});

// --------------------------------------------------------------------------
// POST /api/diagnose
// Body: { vehicle: { brand, model, year, fuel }, input: string }
// Forwards to FastAPI's /api/v1/diagnose and relays the structured response.
// --------------------------------------------------------------------------
function validateDiagnosePayload(body) {
  const errors = [];
  const { vehicle, input } = body || {};

  if (!vehicle || typeof vehicle !== "object") {
    errors.push("vehicle object is required");
  } else {
    // variant is intentionally excluded from this required list: it's
    // strongly recommended (accuracy suffers without it) but some
    // vehicles/manuals genuinely have a single "Base" configuration.
    for (const field of ["brand", "model", "year", "fuel"]) {
      if (vehicle[field] === undefined || vehicle[field] === null || vehicle[field] === "") {
        errors.push(`vehicle.${field} is required`);
      }
    }
    if (vehicle.year !== undefined && Number.isNaN(Number(vehicle.year))) {
      errors.push("vehicle.year must be a number");
    }
  }

  if (!input || typeof input !== "string" || !input.trim()) {
    errors.push("input (text problem or fault code) is required");
  }

  return errors;
}

app.post("/api/diagnose", diagnoseLimiter, async (req, res) => {
  const errors = validateDiagnosePayload(req.body);
  if (errors.length) {
    return res.status(400).json({ error: "Invalid request", details: errors });
  }

  const { vehicle, input, debug } = req.body;

  try {
    const aiResponse = await axios.post(
      `${AI_SERVICE_URL}/api/v1/diagnose`,
      {
        vehicle: {
          brand: vehicle.brand,
          model: vehicle.model,
          year: Number(vehicle.year),
          fuel: vehicle.fuel,
          variant: vehicle.variant || "Base",
        },
        input: input.trim(),
        // Only ever honored when the gateway itself is run in dev mode —
        // never lets a frontend request leak internal RAG/web-search
        // details in production.
        debug: Boolean(debug) && process.env.NODE_ENV !== "production",
      },
      { timeout: AI_SERVICE_TIMEOUT_MS }
    );

    return res.json(aiResponse.data);
  } catch (err) {
    if (err.response) {
      // AI service responded with an error status
      console.error("AI service error:", err.response.status, err.response.data);
      return res.status(err.response.status).json({
        error: "AI diagnostics service returned an error",
        details: err.response.data,
      });
    }
    if (err.code === "ECONNABORTED") {
      console.error("AI service timeout");
      return res.status(504).json({ error: "AI diagnostics service timed out. Please try again." });
    }
    console.error("AI service unreachable:", err.message);
    return res.status(502).json({ error: "AI diagnostics service is unavailable." });
  }
});

// --------------------------------------------------------------------------
// POST /api/extract
// Body: { query: string }
// Free-text mode: "Swift 2023 Petrol ZXi is showing P0128". Forwards to
// FastAPI's /api/v1/extract, which pulls out vehicle fields + the problem
// without guessing anything the user didn't say. The frontend uses
// `missing_fields` to prompt for just what's needed before calling /api/diagnose.
// --------------------------------------------------------------------------
const extractLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: Number(process.env.EXTRACT_RATE_LIMIT || 30),
  message: { error: "Too many requests. Please wait a moment and try again." },
});

app.post("/api/extract", extractLimiter, async (req, res) => {
  const { query } = req.body || {};
  if (!query || typeof query !== "string" || !query.trim()) {
    return res.status(400).json({ error: "Invalid request", details: ["query is required"] });
  }

  try {
    const aiResponse = await axios.post(
      `${AI_SERVICE_URL}/api/v1/extract`,
      { query: query.trim() },
      { timeout: AI_SERVICE_TIMEOUT_MS }
    );
    return res.json(aiResponse.data);
  } catch (err) {
    if (err.response) {
      console.error("AI service error:", err.response.status, err.response.data);
      return res.status(err.response.status).json({
        error: "AI diagnostics service returned an error",
        details: err.response.data,
      });
    }
    if (err.code === "ECONNABORTED") {
      console.error("AI service timeout");
      return res.status(504).json({ error: "AI diagnostics service timed out. Please try again." });
    }
    console.error("AI service unreachable:", err.message);
    return res.status(502).json({ error: "AI diagnostics service is unavailable." });
  }
});

// --------------------------------------------------------------------------
// POST /api/upload-manual
// multipart/form-data: file (PDF) + brand, model, year, fuel, variant
// Received here via multer (memory storage — file never touches this
// server's disk, just passes through to the AI service), then re-wrapped
// as multipart and forwarded to FastAPI's /api/v1/upload-manual, which does
// the actual disk storage + registry + ChromaDB ingestion.
// --------------------------------------------------------------------------
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: MAX_UPLOAD_BYTES },
});

const uploadLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: Number(process.env.UPLOAD_RATE_LIMIT || 10),
  message: { error: "Too many uploads. Please wait a moment and try again." },
});

app.post("/api/upload-manual", uploadLimiter, upload.single("file"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: "Invalid request", details: ["file is required"] });
  }
  const { brand, model, year, fuel, variant } = req.body || {};
  const missing = ["brand", "model", "year", "fuel"].filter((f) => !req.body?.[f]);
  if (missing.length) {
    return res.status(400).json({ error: "Invalid request", details: missing.map((f) => `${f} is required`) });
  }

  try {
    const form = new FormData();
    form.append("file", req.file.buffer, { filename: req.file.originalname, contentType: req.file.mimetype });
    form.append("brand", brand);
    form.append("model", model);
    form.append("year", String(year));
    form.append("fuel", fuel);
    form.append("variant", variant || "Base");

    const aiResponse = await axios.post(`${AI_SERVICE_URL}/api/v1/upload-manual`, form, {
      headers: form.getHeaders(),
      timeout: UPLOAD_TIMEOUT_MS,
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
    });
    return res.json(aiResponse.data);
  } catch (err) {
    if (err.response) {
      console.error("AI service upload error:", err.response.status, err.response.data);
      return res.status(err.response.status).json({
        error: "Manual upload failed",
        details: err.response.data,
      });
    }
    if (err.code === "ECONNABORTED") {
      console.error("AI service upload timeout");
      return res.status(504).json({ error: "Upload timed out — the file may be large or the AI service is slow. Please try again." });
    }
    console.error("AI service unreachable:", err.message);
    return res.status(502).json({ error: "AI diagnostics service is unavailable." });
  }
});

// Multer errors (e.g. file too large) land here, not in the route handler above.
app.use((err, req, res, next) => {
  if (err instanceof multer.MulterError) {
    if (err.code === "LIMIT_FILE_SIZE") {
      return res.status(413).json({ error: `File exceeds the ${Math.floor(MAX_UPLOAD_BYTES / (1024 * 1024))}MB limit` });
    }
    return res.status(400).json({ error: `Upload error: ${err.message}` });
  }
  next(err);
});

// --------------------------------------------------------------------------
// GET /api/manuals — lists registered manuals, optionally filtered by
// ?brand=&model=&year=&fuel=
// --------------------------------------------------------------------------
app.get("/api/manuals", async (req, res) => {
  try {
    const aiResponse = await axios.get(`${AI_SERVICE_URL}/api/v1/manuals`, {
      params: req.query,
      timeout: AI_SERVICE_TIMEOUT_MS,
    });
    return res.json(aiResponse.data);
  } catch (err) {
    if (err.response) {
      return res.status(err.response.status).json({ error: "AI diagnostics service returned an error", details: err.response.data });
    }
    console.error("AI service unreachable:", err.message);
    return res.status(502).json({ error: "AI diagnostics service is unavailable." });
  }
});

// --------------------------------------------------------------------------
// 404 + error handling
// --------------------------------------------------------------------------
app.use((req, res) => {
  res.status(404).json({ error: `Route not found: ${req.method} ${req.originalUrl}` });
});

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  console.error("Unhandled error:", err);
  res.status(500).json({ error: "Internal server error" });
});

app.listen(PORT, () => {
  console.log(`Gateway listening on http://localhost:${PORT}`);
  console.log(`Forwarding diagnosis requests to AI service at ${AI_SERVICE_URL}`);
});

module.exports = app;

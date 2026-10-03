import { useRef, useState } from "react";
import { X, UploadCloud, FileText, CheckCircle2, TriangleAlert, Loader2 } from "lucide-react";
import { uploadManual } from "../api.js";

export default function ManualUploadModal({ vehicle, onClose, onUploaded }) {
  const [file, setFile] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState("idle"); // idle | uploading | success | error
  const [resultMessage, setResultMessage] = useState("");
  const fileInputRef = useRef(null);

  const vehicleReady = Boolean(vehicle.brand && vehicle.model && vehicle.year && vehicle.fuel);

  function handleFiles(fileList) {
    const picked = fileList?.[0];
    if (!picked) return;
    if (picked.type !== "application/pdf" && !picked.name.toLowerCase().endsWith(".pdf")) {
      setStatus("error");
      setResultMessage("Only PDF files are accepted.");
      return;
    }
    setFile(picked);
    setStatus("idle");
    setResultMessage("");
  }

  function onDrop(e) {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
  }

  async function handleUpload() {
    if (!file || !vehicleReady) return;
    setStatus("uploading");
    setProgress(0);
    try {
      const data = await uploadManual({
        file,
        vehicle,
        onProgress: (pct) => setProgress(pct),
      });
      setStatus("success");
      setResultMessage(data.message || "Manual uploaded and indexed.");
      onUploaded?.(data);
    } catch (err) {
      console.error(err);
      setStatus("error");
      setResultMessage(
        err.response?.data?.error || err.response?.data?.detail || "Upload failed. Please try again."
      );
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
      <div className="w-full max-w-md rounded-xl border border-ink-line bg-ink-panel shadow-glow p-5 sm:p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-display text-sm font-semibold tracking-[0.15em] text-mist uppercase">
            Upload Vehicle Manual
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-mist-faint hover:text-mist transition-colors"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {!vehicleReady && (
          <p className="text-sm text-signal-amber flex items-center gap-2 mb-4">
            <TriangleAlert size={14} />
            Select brand, model, year, and fuel above before uploading a manual for that vehicle.
          </p>
        )}

        <p className="text-xs text-mist-faint mb-4">
          Uploading for:{" "}
          <span className="text-mist-dim">
            {vehicle.brand || "—"} {vehicle.model || ""} {vehicle.year || ""} {vehicle.fuel || ""}
            {vehicle.variant ? ` · ${vehicle.variant}` : ""}
          </span>
        </p>

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`rounded-lg border-2 border-dashed p-6 flex flex-col items-center justify-center gap-2 text-center cursor-pointer transition-colors ${
            isDragging
              ? "border-signal-amber bg-signal-amber/5"
              : "border-ink-line hover:border-signal-amber/40"
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
          {file ? (
            <>
              <FileText size={24} className="text-signal-amber" />
              <p className="text-sm text-mist">{file.name}</p>
              <p className="text-[11px] text-mist-faint">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
            </>
          ) : (
            <>
              <UploadCloud size={24} className="text-mist-faint" />
              <p className="text-sm text-mist-dim">Drag and drop a PDF here, or click to browse</p>
              <p className="text-[11px] text-mist-faint">Owner/user manual, up to 60MB</p>
            </>
          )}
        </div>

        {/* Progress bar */}
        {status === "uploading" && (
          <div className="mt-4">
            <div className="h-1.5 w-full rounded-full bg-ink-raised overflow-hidden">
              <div
                className="h-full bg-signal-amber transition-all duration-150"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="mt-1.5 text-[11px] text-mist-faint flex items-center gap-1.5">
              <Loader2 size={11} className="animate-spin" />
              {progress < 100 ? `Uploading… ${progress}%` : "Processing & indexing manual…"}
            </p>
          </div>
        )}

        {status === "success" && (
          <p className="mt-4 text-sm text-signal-teal flex items-start gap-2">
            <CheckCircle2 size={15} className="shrink-0 mt-0.5" /> {resultMessage}
          </p>
        )}

        {status === "error" && (
          <p className="mt-4 text-sm text-signal-red flex items-start gap-2">
            <TriangleAlert size={15} className="shrink-0 mt-0.5" /> {resultMessage}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2.5">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-md text-xs font-display tracking-wide uppercase text-mist-dim hover:text-mist transition-colors"
          >
            {status === "success" ? "Close" : "Cancel"}
          </button>
          {status !== "success" && (
            <button
              type="button"
              onClick={handleUpload}
              disabled={!file || !vehicleReady || status === "uploading"}
              className="px-4 py-2 rounded-md text-xs font-display font-semibold tracking-wide uppercase
                         bg-signal-amber/15 text-signal-amber border border-signal-amber/30
                         hover:bg-signal-amber/25 transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {status === "uploading" ? <Loader2 size={13} className="animate-spin" /> : null}
              {status === "uploading" ? "Uploading…" : "Upload & Index"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

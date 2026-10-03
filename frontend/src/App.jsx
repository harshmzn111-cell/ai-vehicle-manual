import { useEffect, useRef, useState } from "react";
import { Gauge, Loader2, TriangleAlert, RadioTower, Info, UploadCloud, FileText } from "lucide-react";
import VehicleSelector from "./components/VehicleSelector.jsx";
import QuickAsk from "./components/QuickAsk.jsx";
import ChatInput from "./components/ChatInput.jsx";
import DiagnosisCard from "./components/DiagnosisCard.jsx";
import ManualUploadModal from "./components/ManualUploadModal.jsx";
import { fetchVehicleCatalog, requestDiagnosis, extractVehicleInfo } from "./api.js";

const FIELD_LABELS = { brand: "brand", model: "model", year: "year", fuel: "fuel type" };

export default function App() {
  const [catalog, setCatalog] = useState(null);
  const [catalogError, setCatalogError] = useState(null);
  const [vehicle, setVehicle] = useState({ brand: "", model: "", year: "", fuel: "", variant: "" });
  const [history, setHistory] = useState([]); // { id, userQuery, vehicle, result }
  const [isScanning, setIsScanning] = useState(false);
  const [requestError, setRequestError] = useState(null);
  const [debugMode, setDebugMode] = useState(false);
  const [pendingProblem, setPendingProblem] = useState(""); // problem text extracted by QuickAsk, awaiting missing vehicle fields
  const [missingFieldNote, setMissingFieldNote] = useState(null);
  const [showUploadModal, setShowUploadModal] = useState(false);

  const feedEndRef = useRef(null);

  useEffect(() => {
    fetchVehicleCatalog()
      .then(setCatalog)
      .catch((err) => {
        console.error(err);
        setCatalogError("Couldn't load vehicle list. Is the gateway running?");
      });
  }, []);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, isScanning]);

  const vehicleReady = Boolean(
    vehicle.brand && vehicle.model && vehicle.year && vehicle.fuel && vehicle.variant
  );

  async function runDiagnosis(vehicleForRequest, query) {
    setRequestError(null);
    setIsScanning(true);
    try {
      const result = await requestDiagnosis({ vehicle: vehicleForRequest, input: query, debug: debugMode });
      setHistory((prev) => [
        ...prev,
        { id: crypto.randomUUID(), userQuery: query, vehicle: { ...vehicleForRequest }, result },
      ]);
      setPendingProblem("");
      setMissingFieldNote(null);
    } catch (err) {
      console.error(err);
      const message =
        err.response?.data?.error ||
        "Diagnosis failed. Check that the gateway and AI service are running.";
      setRequestError(message);
    } finally {
      setIsScanning(false);
    }
  }

  function handleSubmit(query) {
    return runDiagnosis(vehicle, query);
  }

  async function handleQuickAsk(text) {
    setRequestError(null);
    setMissingFieldNote(null);
    try {
      const { vehicle: extracted, problem, missing_fields, is_complete } = await extractVehicleInfo(text);
      const merged = {
        brand: extracted.brand || vehicle.brand || "",
        model: extracted.model || vehicle.model || "",
        year: extracted.year ? String(extracted.year) : vehicle.year || "",
        fuel: extracted.fuel || vehicle.fuel || "",
        variant: extracted.variant || vehicle.variant || "",
      };
      setVehicle(merged);

      if (is_complete) {
        await runDiagnosis(merged, problem);
      } else {
        setPendingProblem(problem);
        setMissingFieldNote(
          `Got it — just need the ${missing_fields.map((f) => FIELD_LABELS[f] || f).join(", ")} to run this diagnosis.`
        );
      }
    } catch (err) {
      console.error(err);
      const message =
        err.response?.data?.error ||
        "Couldn't read that. Please try rephrasing, or use the vehicle fields below.";
      setRequestError(message);
    }
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-ink-line bg-ink-panel/70 backdrop-blur px-4 sm:px-6 py-3.5 sticky top-0 z-20">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-md bg-signal-amber/10 border border-signal-amber/30 flex items-center justify-center">
              <Gauge size={17} className="text-signal-amber" />
            </div>
            <div>
              <h1 className="font-display text-base font-bold tracking-[0.08em] text-mist leading-none">
                DRIVE<span className="text-signal-amber">DX</span>
              </h1>
              <p className="text-[10px] text-mist-faint tracking-[0.15em] uppercase">
                Emergency Manual &amp; Diagnostics
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setDebugMode((d) => !d)}
              title="Toggle developer/debug mode (shows internal retrieval details)"
              className={`text-[10px] font-display tracking-[0.15em] uppercase px-2.5 py-1 rounded border transition-colors ${
                debugMode
                  ? "border-signal-amber/50 text-signal-amber bg-signal-amber/10"
                  : "border-ink-line text-mist-faint hover:text-mist-dim"
              }`}
            >
              Debug {debugMode ? "On" : "Off"}
            </button>
            <div className="flex items-center gap-1.5 text-[11px] font-display tracking-wide text-mist-dim">
              <span className="relative flex h-2 w-2">
                <span className="animate-pulseDot absolute inline-flex h-full w-full rounded-full bg-signal-teal" />
              </span>
              System Ready
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 flex flex-col max-w-4xl w-full mx-auto px-4 sm:px-6 py-5 gap-5">
        <QuickAsk onExtract={handleQuickAsk} disabled={isScanning} />

        <VehicleSelector
          catalog={catalog}
          selection={vehicle}
          onChange={setVehicle}
        />

        {/* Dedicated Prominent Manual Upload Block */}
        <div className="rounded-lg border border-signal-amber/30 bg-ink-panel/90 p-4 shadow-lg backdrop-blur flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 transition-all">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded bg-signal-amber/10 border border-signal-amber/20 text-signal-amber mt-0.5">
              <FileText size={20} />
            </div>
            <div>
              <h3 className="font-display text-xs font-bold tracking-[0.12em] text-mist uppercase">
                Custom Vehicle Manual (PDF)
              </h3>
              <p className="text-xs text-mist-dim mt-0.5">
                Have an official owner or service manual? Upload it here for exact page-referenced local RAG diagnosis.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setShowUploadModal(true)}
            className="w-full sm:w-auto flex items-center justify-center gap-2 text-xs font-display tracking-[0.1em] uppercase px-4 py-2.5 rounded bg-signal-amber text-ink-bg font-bold hover:bg-signal-amber/90 transition-all shadow-md shrink-0 cursor-pointer"
          >
            <UploadCloud size={16} />
            Upload PDF Manual
          </button>
        </div>

        {missingFieldNote && (
          <p className="text-sm text-signal-amber flex items-center gap-2 -mt-2">
            <Info size={14} /> {missingFieldNote}
          </p>
        )}

        {catalogError && (
          <p className="text-sm text-signal-red flex items-center gap-2">
            <TriangleAlert size={15} /> {catalogError}
          </p>
        )}

        {/* Diagnosis feed */}
        <div className="flex-1 thin-scroll overflow-y-auto min-h-[240px] flex flex-col gap-6 pb-2">
          {history.length === 0 && !isScanning && (
            <div className="flex-1 flex flex-col items-center justify-center text-center py-14 gap-3">
              <RadioTower size={28} className="text-mist-faint" />
              <p className="font-display text-sm tracking-wide text-mist-dim max-w-sm">
                Select your vehicle, then describe a problem or enter a fault code
                — we'll check the manual and, if needed, search the web for a fix.
              </p>
            </div>
          )}

          {history.map((entry) => (
            <DiagnosisCard
              key={entry.id}
              userQuery={entry.userQuery}
              vehicle={entry.vehicle}
              result={entry.result}
            />
          ))}

          {isScanning && (
            <div className="self-start flex items-center gap-2.5 rounded-lg border border-ink-line bg-ink-panel px-4 py-3">
              <Loader2 size={16} className="animate-spin text-signal-amber" />
              <span className="font-display text-xs tracking-[0.15em] text-mist-dim uppercase">
                Scanning manual &amp; running diagnosis…
              </span>
            </div>
          )}

          {requestError && (
            <p className="text-sm text-signal-red flex items-center gap-2">
              <TriangleAlert size={15} /> {requestError}
            </p>
          )}

          <div ref={feedEndRef} />
        </div>
      </main>

      <ChatInput
        onSubmit={handleSubmit}
        disabled={isScanning}
        vehicleReady={vehicleReady}
        prefill={pendingProblem}
      />

      {showUploadModal && (
        <ManualUploadModal
          vehicle={vehicle}
          onClose={() => setShowUploadModal(false)}
          onUploaded={() => {
            // A freshly-ingested manual means the next diagnose call for this
            // vehicle can go through Scenario A (local-only) instead of C —
            // nothing else to do here, ChromaDB/the registry already reflect it.
          }}
        />
      )}
    </div>
  );
}
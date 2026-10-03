import {
  AlertTriangle,
  BookOpen,
  Globe,
  Wrench,
  ShieldCheck,
  ShieldAlert,
  Hash,
  DollarSign,
  Bug,
} from "lucide-react";
import SeverityGauge from "./SeverityGauge.jsx";

const SEVERITY_STYLES = {
  LOW: "border-signal-teal/40 bg-signal-teal/5",
  MEDIUM: "border-signal-amber/40 bg-signal-amber/5",
  HIGH: "border-signal-red/40 bg-signal-red/5",
  CRITICAL: "border-signal-red/70 bg-signal-red/10",
};

export default function DiagnosisCard({ userQuery, vehicle, result }) {
  const severity = String(result.severity || "MEDIUM").toUpperCase();
  const severityStyle = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.MEDIUM;
  const debug = result.debug;

  return (
    <div className="flex flex-col gap-3">
      {/* echoed user query bubble */}
      <div className="self-end max-w-[85%] rounded-lg rounded-tr-sm bg-ink-raised border border-ink-line px-4 py-2.5 text-sm text-mist">
        {userQuery}
      </div>

      <div
        className={`rounded-xl border ${severityStyle} bg-ink-panel p-5 sm:p-6 shadow-glow`}
      >
        {/* header row */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <p className="text-[11px] font-display tracking-[0.2em] text-mist-faint uppercase mb-1">
              {vehicle.brand} {vehicle.model} · {vehicle.year} · {vehicle.fuel}
              {vehicle.variant ? ` · ${vehicle.variant}` : ""}
            </p>
            <h3 className="font-display text-xl sm:text-2xl font-semibold text-mist leading-snug">
              {result.fault_name}
            </h3>
            {result.fault_code && (
              <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-ink-raised border border-ink-line px-2 py-1">
                <Hash size={12} className="text-signal-amber" />
                <span className="font-mono text-xs tracking-wide text-signal-amber">
                  {result.fault_code}
                </span>
              </div>
            )}
          </div>
          <SeverityGauge severity={severity} />
        </div>

        <p className="text-xs text-mist-faint italic mt-1 mb-4">{result.severity_reason}</p>

        {/* summary */}
        <p className="text-sm text-mist/90 leading-relaxed mb-4">{result.summary}</p>

        {/* relevant image, shown only when a reliably relevant one was found */}
        {result.image?.url && (
          <div className="mb-5 rounded-lg overflow-hidden border border-ink-line bg-ink-raised">
            <img
              src={result.image.url}
              alt={result.image.caption || result.fault_name}
              className="w-full max-h-64 object-cover"
              onError={(e) => {
                e.currentTarget.parentElement.style.display = "none";
              }}
            />
            {result.image.caption && (
              <p className="text-[11px] text-mist-faint px-3 py-2">{result.image.caption}</p>
            )}
          </div>
        )}

        {/* safe to drive + cost */}
        <div className="flex flex-wrap gap-2 mb-5">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-display font-semibold tracking-wide ${
              result.is_safe_to_drive
                ? "bg-signal-teal/10 text-signal-teal border border-signal-teal/30"
                : "bg-signal-red/10 text-signal-red border border-signal-red/30"
            }`}
          >
            {result.is_safe_to_drive ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
            {result.is_safe_to_drive ? "Safe to continue driving" : "Not safe to continue driving"}
          </span>

          {result.estimated_repair_cost && result.estimated_repair_cost !== "Unknown" && (
            <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-display font-semibold tracking-wide bg-ink-raised border border-ink-line text-mist-dim">
              <DollarSign size={14} className="text-signal-amber" />
              {result.estimated_repair_cost}
            </span>
          )}

          {result.recommend_professional_help && (
            <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-display font-semibold tracking-wide bg-ink-raised border border-ink-line text-mist-dim">
              <Wrench size={14} className="text-signal-amber" />
              Mechanic recommended
            </span>
          )}
        </div>

        {/* actionable steps */}
        {result.actionable_steps?.length > 0 && (
          <div className="mb-5">
            <h4 className="flex items-center gap-1.5 text-[11px] font-display font-bold tracking-[0.2em] text-mist-dim uppercase mb-2.5">
              <AlertTriangle size={13} className="text-signal-amber" />
              Actionable Steps
            </h4>
            <ol className="flex flex-col gap-2.5">
              {result.actionable_steps.map((step) => (
                <li key={step.step_number} className="flex gap-3">
                  <span className="shrink-0 mt-0.5 w-5 h-5 rounded-full bg-ink-raised border border-ink-line flex items-center justify-center font-mono text-[11px] text-signal-amber">
                    {step.step_number}
                  </span>
                  <div>
                    <p className="text-sm text-mist/90 leading-relaxed">{step.instruction}</p>
                    {step.caution && (
                      <p className="text-xs text-signal-red/90 mt-1 flex items-start gap-1">
                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                        {step.caution}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* references */}
        {(result.manual_references?.length > 0 || result.web_references?.length > 0) && (
          <div className="grid sm:grid-cols-2 gap-4 pt-4 border-t border-ink-line">
            {result.manual_references?.length > 0 && (
              <div>
                <h4 className="flex items-center gap-1.5 text-[11px] font-display font-bold tracking-[0.2em] text-mist-dim uppercase mb-2">
                  <BookOpen size={13} className="text-signal-blue" />
                  Manual References
                </h4>
                <ul className="flex flex-col gap-1.5">
                  {result.manual_references.map((ref, i) => (
                    <li key={i} className="text-xs text-mist-dim">
                      <span className="font-mono text-signal-blue">p.{ref.page_number}</span>{" "}
                      — {ref.note}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.web_references?.length > 0 && (
              <div>
                <h4 className="flex items-center gap-1.5 text-[11px] font-display font-bold tracking-[0.2em] text-mist-dim uppercase mb-2">
                  <Globe size={13} className="text-signal-teal" />
                  Web References
                </h4>
                <ul className="flex flex-col gap-1.5">
                  {result.web_references.map((ref, i) => (
                    <li key={i} className="text-xs">
                      <a
                        href={ref.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-signal-teal hover:underline break-words"
                      >
                        {ref.title || ref.url}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* developer/debug panel — only present when the request set debug:true */}
        {debug && (
          <div className="mt-5 pt-4 border-t border-dashed border-ink-line">
            <h4 className="flex items-center gap-1.5 text-[11px] font-display font-bold tracking-[0.2em] text-mist-faint uppercase mb-2">
              <Bug size={13} />
              Debug
            </h4>
            <pre className="text-[10px] leading-relaxed text-mist-faint whitespace-pre-wrap break-words bg-ink-raised rounded-md p-3 border border-ink-line">
              {JSON.stringify(debug, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

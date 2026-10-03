import { useEffect, useState } from "react";
import { Send, Siren } from "lucide-react";

const QUICK_CHIPS = [
  "Engine overheating",
  "P0300",
  "ABS Warning",
  "P0171",
  "Brake pedal feels soft",
  "Battery light on",
];

export default function ChatInput({ onSubmit, disabled, vehicleReady, prefill }) {
  const [value, setValue] = useState("");

  // When QuickAsk extracts a problem but some vehicle fields are still
  // missing, it hands the problem text here so the user doesn't have to
  // retype it once they fill in the missing fields above.
  useEffect(() => {
    if (prefill) setValue(prefill);
  }, [prefill]);

  function handleSubmit(e) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled || !vehicleReady) return;
    onSubmit(trimmed);
    setValue("");
  }

  return (
    <div className="border-t border-ink-line bg-ink-panel/95 backdrop-blur px-4 sm:px-6 py-4">
      {!vehicleReady && (
        <p className="flex items-center gap-1.5 text-xs text-signal-amber mb-2.5 font-display tracking-wide">
          <Siren size={13} />
          Select your vehicle above before running a diagnosis.
        </p>
      )}

      <div className="flex flex-wrap gap-1.5 mb-3">
        {QUICK_CHIPS.map((chip) => (
          <button
            key={chip}
            type="button"
            onClick={() => setValue(chip)}
            disabled={disabled}
            className="text-[11px] font-mono px-2.5 py-1 rounded-full border border-ink-line bg-ink-raised
                       text-mist-dim hover:border-signal-amber/50 hover:text-signal-amber transition-colors
                       disabled:opacity-40"
          >
            {chip}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder='Describe the problem, or enter a code like "P0300"'
          disabled={disabled}
          className="flex-1 rounded-lg bg-ink-raised border border-ink-line px-4 py-3 text-sm text-mist
                     placeholder:text-mist-faint focus-visible:border-signal-amber
                     disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={disabled || !value.trim() || !vehicleReady}
          className="shrink-0 flex items-center justify-center w-11 h-11 rounded-lg
                     bg-signal-amber text-ink font-semibold
                     hover:brightness-110 active:brightness-95 transition
                     disabled:opacity-30 disabled:cursor-not-allowed"
          aria-label="Run diagnosis"
        >
          <Send size={17} />
        </button>
      </form>
    </div>
  );
}

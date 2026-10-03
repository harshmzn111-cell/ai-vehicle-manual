import { useState } from "react";
import { Wand2, Loader2 } from "lucide-react";

export default function QuickAsk({ onExtract, disabled }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await onExtract(text.trim());
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-ink-line bg-ink-panel/60 p-3.5 sm:p-4 flex gap-2.5">
      <div className="flex-1 flex items-center gap-2 rounded-md bg-ink-raised border border-ink-line px-3">
        <Wand2 size={15} className="text-signal-amber shrink-0" />
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='Or describe it in one line — "Swift 2023 Petrol ZXi is showing P0128"'
          disabled={disabled || busy}
          className="w-full bg-transparent py-2.5 text-sm text-mist placeholder:text-mist-faint outline-none disabled:opacity-50"
        />
      </div>
      <button
        type="submit"
        disabled={disabled || busy || !text.trim()}
        className="shrink-0 rounded-md px-4 font-display text-xs font-semibold tracking-[0.1em] uppercase
                   bg-signal-amber/15 text-signal-amber border border-signal-amber/30
                   hover:bg-signal-amber/25 transition-colors
                   disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : null}
        {busy ? "Reading…" : "Auto-fill"}
      </button>
    </form>
  );
}

import { useId } from "react";
import { Car, CheckCircle2 } from "lucide-react";

function ComboField({ label, value, onChange, options, disabled, placeholder }) {
  const listId = useId();
  return (
    <label className="flex flex-col gap-1.5 flex-1 min-w-[140px]">
      <span className="text-[11px] font-display font-semibold tracking-[0.18em] text-mist-dim uppercase">
        {label}
      </span>
      <input
        list={listId}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        className="w-full rounded-md bg-ink-raised border border-ink-line text-mist
                   font-display text-[15px] font-medium tracking-wide py-2.5 px-3
                   disabled:opacity-40 disabled:cursor-not-allowed
                   hover:border-signal-amber/50 transition-colors
                   focus-visible:border-signal-amber outline-none"
      />
      <datalist id={listId}>
        {options.map((opt) => (
          <option key={opt} value={opt} />
        ))}
      </datalist>
    </label>
  );
}

const COMMON_FUEL_TYPES = ["Petrol", "Diesel", "CNG", "EV", "Hybrid"];

export default function VehicleSelector({ catalog, selection, onChange }) {
  const brands = catalog?.brands ?? [];
  const brandObj = brands.find((b) => b.name.toLowerCase() === (selection.brand || "").toLowerCase());
  const models = brandObj?.models ?? [];
  const modelObj = models.find((m) => m.name.toLowerCase() === (selection.model || "").toLowerCase());
  const years = modelObj?.years ?? [];
  const fuelTypes = modelObj?.fuelTypes?.length ? modelObj.fuelTypes : COMMON_FUEL_TYPES;
  const variants = modelObj?.variants ?? [];

  const isComplete =
    selection.brand && selection.model && selection.year && selection.fuel && selection.variant;

  function update(field, value) {
    const next = { ...selection, [field]: value };
    onChange(next);
  }

  return (
    <div className="rounded-xl border border-ink-line bg-ink-panel/80 grid-texture p-4 sm:p-5 shadow-glow">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Car size={18} className="text-signal-amber" />
          <h2 className="font-display text-sm font-semibold tracking-[0.2em] text-mist uppercase">
            Vehicle Profile
          </h2>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] font-display tracking-wider uppercase">
          {isComplete ? (
            <>
              <CheckCircle2 size={14} className="text-signal-teal" />
              <span className="text-signal-teal">Locked in</span>
            </>
          ) : (
            <span className="text-mist-faint">Fill in all fields</span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        <ComboField
          label="Brand"
          placeholder="e.g. Maruti Suzuki"
          value={selection.brand}
          onChange={(v) => update("brand", v)}
          options={brands.map((b) => b.name)}
        />
        <ComboField
          label="Model"
          placeholder="e.g. Swift"
          value={selection.model}
          onChange={(v) => update("model", v)}
          options={models.map((m) => m.name)}
        />
        <ComboField
          label="Year"
          placeholder="e.g. 2023"
          value={selection.year}
          onChange={(v) => update("year", v)}
          options={years.map(String)}
        />
        <ComboField
          label="Fuel"
          placeholder="e.g. Petrol"
          value={selection.fuel}
          onChange={(v) => update("fuel", v)}
          options={fuelTypes}
        />
        <ComboField
          label="Variant"
          placeholder="e.g. ZXi"
          value={selection.variant}
          onChange={(v) => update("variant", v)}
          options={variants}
        />
      </div>
      <p className="mt-3 text-[11px] text-mist-faint">
        Pick a suggestion or type your own — any Indian-market vehicle is supported, not just the ones listed.
      </p>
    </div>
  );
}

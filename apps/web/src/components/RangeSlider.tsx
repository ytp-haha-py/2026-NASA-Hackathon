import type { CSSProperties } from "react";
import type { SliderRange } from "../lib/dimensions";

type RangeSliderProps = {
  id: string;
  range: [number, number];
  slider: SliderRange;
  minLabel: string;
  maxLabel: string;
  disabled?: boolean;
  onChange: (next: [number, number]) => void;
};

function percent(value: number, slider: SliderRange): number {
  const span = slider.hi - slider.lo;
  return span > 0 ? ((value - slider.lo) / span) * 100 : 0;
}

/** Two native range inputs over one track: accessible, keyboard-operable, no extra library. */
export function RangeSlider({
  id,
  range,
  slider,
  minLabel,
  maxLabel,
  disabled = false,
  onChange,
}: RangeSliderProps) {
  const [low, high] = range;
  const fill: CSSProperties = {
    left: `calc(7px + (100% - 14px) * ${percent(low, slider) / 100})`,
    width: `calc((100% - 14px) * ${(percent(high, slider) - percent(low, slider)) / 100})`,
  };

  return (
    <div className={`dual-range${disabled ? " is-disabled" : ""}`}>
      <span className="range-track" aria-hidden="true" />
      <span className="range-fill" aria-hidden="true" style={fill} />
      <input
        id={`${id}-min`}
        className="range-input"
        type="range"
        min={slider.lo}
        max={slider.hi}
        step={slider.step}
        value={low}
        disabled={disabled}
        aria-label={minLabel}
        onChange={(event) => onChange([Math.min(Number(event.target.value), high), high])}
      />
      <input
        id={`${id}-max`}
        className="range-input"
        type="range"
        min={slider.lo}
        max={slider.hi}
        step={slider.step}
        value={high}
        disabled={disabled}
        aria-label={maxLabel}
        onChange={(event) => onChange([low, Math.max(Number(event.target.value), low)])}
      />
    </div>
  );
}

type SingleSliderProps = {
  id?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  label: string;
  tone?: "weight" | "target";
  disabled?: boolean;
  onChange: (value: number) => void;
};

/** Single-thumb slider with a filled track, styled like the dual range. */
export function SingleSlider({
  id,
  value,
  min,
  max,
  step,
  label,
  tone = "weight",
  disabled = false,
  onChange,
}: SingleSliderProps) {
  const span = max - min;
  const ratio = span > 0 ? (Math.min(max, Math.max(min, value)) - min) / span : 0;
  return (
    <div className={`single-range tone-${tone}${disabled ? " is-disabled" : ""}`}>
      <span className="range-track" aria-hidden="true" />
      <span
        className="range-fill"
        aria-hidden="true"
        style={{ left: "7px", width: `calc((100% - 14px) * ${ratio})` }}
      />
      <input
        id={id}
        className="range-input"
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

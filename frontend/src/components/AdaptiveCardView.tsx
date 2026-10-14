import { useEffect, useRef, useState } from "react";
import * as AC from "adaptivecards";

function hostConfig() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  const fg = v("--c-fg", "#f0f0f0"), fg2 = v("--c-fg2", "#a0a0a0"), primary = v("--c-primary", "#d04a02");
  const colors = {
    default: { default: fg, subtle: fg2 }, dark: { default: fg, subtle: fg2 }, light: { default: fg2, subtle: fg2 },
    accent: { default: primary, subtle: primary }, good: { default: v("--c-success", "#2d8c3c"), subtle: v("--c-success", "#2d8c3c") },
    warning: { default: v("--c-warn", "#ffb600"), subtle: v("--c-warn", "#ffb600") },
    attention: { default: v("--c-danger", "#e0301e"), subtle: v("--c-danger", "#e0301e") },
  };
  return {
    fontFamily: "Inter, -apple-system, sans-serif", supportsInteractivity: true,
    containerStyles: {
      default: { backgroundColor: "#00000000", foregroundColors: colors },
      emphasis: { backgroundColor: v("--c-raised", "#2a2a2a"), foregroundColors: colors },
    },
  };
}

/** Renders a card with Microsoft's own Adaptive Cards SDK (same renderer family as Teams / Web Chat). */
export default function AdaptiveCardView({ card, onSubmit, disabled }: {
  card: unknown; onSubmit?: (data: Record<string, unknown>) => void; disabled?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = useRef(onSubmit);
  submit.current = onSubmit;

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    el.innerHTML = "";
    try {
      const ac = new AC.AdaptiveCard();
      ac.hostConfig = new AC.HostConfig(hostConfig());
      ac.onExecuteAction = (action) => {
        if (action instanceof AC.SubmitAction || action instanceof AC.ExecuteAction) {
          submit.current?.({ ...((action.data as Record<string, unknown>) ?? {}) });
        } else if (action instanceof AC.OpenUrlAction && action.url) {
          window.open(action.url, "_blank", "noopener");
        }
      };
      ac.parse(card);
      const rendered = ac.render();
      if (rendered) el.appendChild(rendered);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [card]);

  return (
    <div className={`ac-host${disabled ? " submitted" : ""}`} style={disabled ? { pointerEvents: "none" } : undefined}>
      {error && <div className="small" style={{ padding: 8, color: "var(--c-danger)" }}>Card could not be rendered: {error}</div>}
      <div ref={host} />
    </div>
  );
}

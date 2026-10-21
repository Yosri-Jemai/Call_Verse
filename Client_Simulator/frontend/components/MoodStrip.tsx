type Props = { mood: number; trail: number[] };

const pos = (m: number) => `${((Math.max(-1, Math.min(1, m)) + 1) / 2) * 100}%`;

export function moodLabel(m: number) {
  if (m <= -0.6) return "furious";
  if (m <= -0.2) return "irritated";
  if (m < 0.2) return "neutral";
  if (m < 0.6) return "reassured";
  return "satisfied";
}

export default function MoodStrip({ mood, trail }: Props) {
  return (
    <div className="mood">
      <div className="mood-head">
        <span>Client mood</span>
        <strong>
          {moodLabel(mood)} <span className="num">({mood.toFixed(2)})</span>
        </strong>
      </div>
      <div className="mood-track" role="img" aria-label={`Client mood ${moodLabel(mood)}`}>
        {trail.slice(0, -1).map((m, i) => (
          <span key={i} className="mood-tick" style={{ left: pos(m) }} />
        ))}
        <span className="mood-needle" style={{ left: pos(mood) }} />
      </div>
      <div className="mood-scale">
        <span>hangs up at -1</span>
        <span>0</span>
        <span>+1</span>
      </div>
    </div>
  );
}

import styles from './pages.module.css';

interface TrackItem {
  id: string;
  name: string;
  real_m: number;
}

/** 트랙 고르기 (계약 v1.4 sim.world.tracks → REST-36). 트랙이 10개라 단추 대신 고르기 상자 */
export function SimTrackSelect({
  tracks,
  current,
  onPick,
  disabled = false,
}: {
  tracks: TrackItem[];
  current: string;
  onPick: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <select
      className={styles.trackSelect}
      value={current}
      aria-label="트랙 고르기"
      disabled={disabled}
      onChange={(e) => onPick(e.target.value)}
    >
      {tracks.map((t) => (
        <option key={t.id} value={t.id}>
          {t.name} · {(t.real_m / 1000).toFixed(3)} km
        </option>
      ))}
    </select>
  );
}

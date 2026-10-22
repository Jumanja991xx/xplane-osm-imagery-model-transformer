import { useEffect, useRef, useState } from "react";
import { Map as MapLibreMap, Marker } from "maplibre-gl";
import { api, ApiError, type Place } from "../api/client";
import { useT } from "../i18n";

const DEBOUNCE_MS = 350;

/** Search box on the map: suggestions while typing, the map flies to the chosen place. */
export function PlaceSearch({ map }: { map: MapLibreMap }) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[] | null>(null);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);
  const marker = useRef<Marker | null>(null);
  const chosen = useRef<string | null>(null); // the name put into the box by a choice
  const resultsFor = useRef(""); // the text the shown suggestions belong to

  const run = async (text: string, final: boolean) => {
    const mine = ++seq.current;
    if (text.trim().length < 2) {
      setResults(null);
      return;
    }
    setLoading(true);
    try {
      const c = map.getCenter();
      const found = await api.searchPlaces(text, c.lng, c.lat, final);
      if (mine !== seq.current) return; // an older answer arriving late
      setResults(found);
      resultsFor.current = text;
      setError(null);
      setActive(found.length ? 0 : -1);
      setOpen(true);
      if (final && found.length) choose(found[0]);
    } catch (e) {
      if (mine !== seq.current) return;
      setError(e instanceof ApiError ? t(e.key, e.params) : String(e));
      setResults(null);
      setOpen(true);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  };

  useEffect(() => {
    if (query === chosen.current) return; // not a new search
    const id = window.setTimeout(() => void run(query, false), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  useEffect(() => () => void marker.current?.remove(), []);

  const choose = (p: Place) => {
    seq.current++; // answers still on their way do not reopen the list
    setOpen(false);
    chosen.current = p.name;
    setQuery(p.name);
    marker.current?.remove();
    marker.current = new Marker({ color: "#2b8ad8" }).setLngLat([p.lon, p.lat]).addTo(map);
    const [w, s, e, n] = p.bbox ?? [0, 0, 0, 0];
    // Stadiums, squares, buildings: their whole outline in view; cities not too far out.
    if (p.bbox && e - w < 2 && n - s < 2) {
      map.fitBounds([w, s, e, n], { padding: 60, maxZoom: 17.5, duration: 1200 });
    } else {
      map.flyTo({ center: [p.lon, p.lat], zoom: p.bbox ? 12 : 16.5, duration: 1200 });
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const n = results?.length ?? 0;
    if (e.key === "ArrowDown" && n) {
      e.preventDefault();
      setOpen(true);
      setActive((a) => (a + 1) % n);
    } else if (e.key === "ArrowUp" && n) {
      e.preventDefault();
      setActive((a) => (a - 1 + n) % n);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const fresh = resultsFor.current === query;
      if (fresh && open && results && active >= 0 && results[active]) choose(results[active]);
      else void run(query, true);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="absolute top-3 left-3 z-10 w-80 max-w-[calc(100%-5rem)]" data-testid="place-search">
      <div className="flex items-center rounded-full bg-white px-3 py-1.5 shadow-md ring-1 ring-black/10 dark:bg-zinc-800">
        <span className="mr-2 text-zinc-400">🔍</span>
        <input
          className="min-w-0 flex-1 bg-transparent text-sm text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100"
          value={query}
          placeholder={t("map.search_placeholder")}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => results && setOpen(true)}
          onKeyDown={onKey}
          aria-label={t("map.search_placeholder")}
          data-testid="place-search-input"
        />
        {loading && <span className="ml-2 h-3 w-3 animate-spin rounded-full border-2 border-zinc-300 border-t-accent-600" />}
        {query && (
          <button
            type="button"
            className="ml-2 text-zinc-400 hover:text-zinc-600"
            onClick={() => {
              seq.current++;
              setQuery("");
              setResults(null);
              setError(null);
              marker.current?.remove();
              marker.current = null;
            }}
            aria-label={t("map.search_clear")}
          >
            ✕
          </button>
        )}
      </div>
      {open && (results || error) && (
        <div className="mt-1 overflow-hidden rounded-xl bg-white shadow-lg ring-1 ring-black/10 dark:bg-zinc-800">
          {error && <div className="px-3 py-2 text-xs text-red-600">{error}</div>}
          {results && results.length === 0 && !error && (
            <div className="px-3 py-2 text-xs text-zinc-500">{t("map.search_none")}</div>
          )}
          <ul data-testid="place-results">
            {results?.map((p, i) => (
              <li key={`${p.lon},${p.lat},${i}`}>
                <button
                  type="button"
                  className={`block w-full px-3 py-2 text-left ${
                    i === active ? "bg-accent-600/10" : "hover:bg-zinc-100 dark:hover:bg-zinc-700"
                  }`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(p)}
                >
                  <div className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">{p.name}</div>
                  {p.detail && <div className="truncate text-xs text-zinc-500">{p.detail}</div>}
                </button>
              </li>
            ))}
          </ul>
          <div className="border-t border-zinc-100 px-3 py-1 text-[10px] text-zinc-400 dark:border-zinc-700">
            {t("map.search_credit")}
          </div>
        </div>
      )}
    </div>
  );
}

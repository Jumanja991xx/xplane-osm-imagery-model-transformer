import { createContext, useContext, useMemo, type ReactNode } from "react";
import { de, type MessageKey } from "./de";
import { en } from "./en";

export type Language = "de" | "en";
type Params = Record<string, unknown> | undefined;

const dictionaries: Record<Language, Record<MessageKey, string>> = { de, en };

function format(template: string, params: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    params[name] === undefined || params[name] === null ? match : String(params[name]),
  );
}

export type Translate = (key: string, params?: Params) => string;

const I18nContext = createContext<{ lang: Language; t: Translate }>({
  lang: "de",
  t: (key) => key,
});

export function I18nProvider({ lang, children }: { lang: Language; children: ReactNode }) {
  const value = useMemo(() => {
    const dict = dictionaries[lang];
    // Keys come from the backend too; unknown keys are shown as-is instead of crashing.
    const t: Translate = (key, params) => format(dict[key as MessageKey] ?? key, params);
    return { lang, t };
  }, [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useT() {
  return useContext(I18nContext);
}

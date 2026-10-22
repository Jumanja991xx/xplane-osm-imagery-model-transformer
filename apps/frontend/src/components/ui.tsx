import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-accent-600 text-white hover:bg-accent-700 disabled:bg-zinc-400",
  secondary:
    "bg-white text-zinc-800 ring-1 ring-zinc-300 hover:bg-zinc-50 disabled:text-zinc-400 dark:bg-zinc-800 dark:text-zinc-100 dark:ring-zinc-600 dark:hover:bg-zinc-700",
  ghost: "text-zinc-700 hover:bg-zinc-200 dark:text-zinc-200 dark:hover:bg-zinc-800",
  danger: "bg-red-600 text-white hover:bg-red-700",
};

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
      {...props}
    />
  );
}

export function Section({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-2">
      {title && (
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {title}
        </h3>
      )}
      {children}
    </section>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <input
        type="checkbox"
        className="h-4 w-4 accent-[var(--color-accent-600)]"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

const alertStyles = {
  info: "bg-accent-50 text-accent-700 ring-accent-100 dark:bg-accent-700/20 dark:text-accent-100 dark:ring-accent-700/40",
  warning:
    "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30",
  error: "bg-red-50 text-red-800 ring-red-200 dark:bg-red-500/10 dark:text-red-200 dark:ring-red-500/30",
  success:
    "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-200 dark:ring-emerald-500/30",
};

export function Alert({
  kind = "info",
  children,
}: {
  kind?: keyof typeof alertStyles;
  children: ReactNode;
}) {
  return <div className={`rounded-md px-3 py-2 text-sm ring-1 ${alertStyles[kind]}`}>{children}</div>;
}

export function Spinner() {
  return (
    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-accent-500 border-t-transparent" />
  );
}

export const inputClass =
  "w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-100 dark:border-zinc-600 dark:bg-zinc-900 dark:focus:ring-accent-700/40";

/** The bar at the bottom of every step: back on the left, the step's actions on the
 * right. Stays visible while the step scrolls. */
export function StepBar({
  back,
  children,
}: {
  back?: { label: ReactNode; onClick: () => void; testId?: string } | null;
  children?: ReactNode;
}) {
  return (
    <div className="sticky -bottom-4 z-10 -mx-4 flex items-center justify-between gap-2 border-t border-zinc-200 bg-zinc-50/95 px-4 py-2.5 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/95">
      {back ? (
        <Button variant="ghost" onClick={back.onClick} data-testid={back.testId ?? "step-back"}>
          ← {back.label}
        </Button>
      ) : (
        <span />
      )}
      <div className="flex flex-wrap justify-end gap-2">{children}</div>
    </div>
  );
}

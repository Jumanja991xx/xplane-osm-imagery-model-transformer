import { Component, type ReactNode } from "react";

/** Catches a crash of a part of the interface: shows what happened and a way out
 * instead of leaving a blank screen. */
export class ErrorBoundary extends Component<
  { children: ReactNode; onClose?: () => void; title: string; closeLabel: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="alertdialog" data-testid="crash">
        <div className="max-w-lg space-y-3 rounded-lg bg-white p-4 text-sm shadow-xl dark:bg-zinc-900">
          <p className="font-semibold">{this.props.title}</p>
          <code className="block text-xs break-all text-red-600">{this.state.error.message}</code>
          <button
            type="button"
            className="rounded-md bg-accent-600 px-3 py-1.5 text-white"
            onClick={() => {
              this.setState({ error: null });
              this.props.onClose?.();
            }}
          >
            {this.props.closeLabel}
          </button>
        </div>
      </div>
    );
  }
}

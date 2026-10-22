import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary
      title="Die App ist auf einen Fehler gestoßen / The app ran into an error"
      closeLabel="Neu laden / Reload"
      onClose={() => window.location.reload()}
    >
      <App />
    </ErrorBoundary>
  </StrictMode>,
);

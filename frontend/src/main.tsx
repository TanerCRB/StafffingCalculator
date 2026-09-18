import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
// Tokens first: every other stylesheet reads its colours and type from them.
import "./styles/tokens.css";
import "./styles/app.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
